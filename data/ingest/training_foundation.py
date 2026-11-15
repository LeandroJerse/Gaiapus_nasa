"""Acquire a small, auditable municipal crop/climate research cohort."""
import argparse
import csv
import gzip
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from data.ingest.weather import normalize_power

RAW = Path('data/raw/training_v1')
OUT = Path('data/processed/training_v1')
SITES = {'3170206': 'Uberlandia', '3170107': 'Uberaba', '3170404': 'Unai',
         '3147006': 'Paracatu', '5107925': 'Sorriso', '5107909': 'Sinop',
         '5105259': 'Lucas do Rio Verde', '5102637': 'Campo Novo do Parecis'}
PARAMS = 'IMERG_PRECTOT,T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN,RH2M,WS2M,T2MDEW,PS'
START, END = date(2001, 1, 1), date(2024, 12, 31)
MISSING = {'-', '...', '..', 'X', 'x', ''}


def decode_json(raw):
    return json.loads(gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw)


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fetch(name, url):
    path = RAW / name
    meta_path = path.with_suffix(path.suffix + '.manifest.json')
    if path.exists() and meta_path.exists():
        metadata = json.loads(meta_path.read_bytes())
        if metadata['url'] != url or metadata['sha256'] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f'Cache provenance mismatch: {name}')
        return decode_json(path.read_bytes())
    for attempt in range(3):
        try:
            request = Request(url, headers={'User-Agent': 'GaiapusResearch/0.1', 'Accept': 'application/json'})
            with urlopen(request, timeout=150) as response:
                raw = response.read(50_000_001)
                if len(raw) > 50_000_000:
                    raise ValueError('Per-response 50 MB acquisition budget exceeded')
                payload = decode_json(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            save_json(meta_path, {'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                                 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
            print('Downloaded', name, len(raw), flush=True)
            return payload
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def write_csv(name, rows):
    rows = list(rows)
    if not rows:
        raise ValueError(f'Empty export: {name}')
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def acquire():
    from shapely.geometry import shape
    locations = []
    for code in SITES:
        location = fetch(f'locality_{code}.json', f'https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{code}')
        if str(location['id']) != code:
            raise ValueError('Unexpected IBGE locality')
        boundary = fetch(f'boundary_{code}.geojson', f'https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{code}?formato=application/vnd.geo%2Bjson&qualidade=intermediaria')
        geometry = shape(boundary['features'][0]['geometry'])
        point = geometry.representative_point()
        lon, lat = round(point.x, 5), round(point.y, 5)
        locations.append({'municipality_code': code, 'municipality': location['nome'], 'state_code': code[:2],
                          'longitude': lon, 'latitude': lat, 'point_method': 'IBGE_polygon_representative_point_NOT_farm_or_crop_weighted'})
        query = urlencode({'parameters': PARAMS, 'community': 'AG', 'longitude': lon, 'latitude': lat,
                           'start': '20010101', 'end': '20241231', 'format': 'JSON', 'time-standard': 'UTC'})
        fetch(f'power_{code}.json', 'https://power.larc.nasa.gov/api/temporal/daily/point?' + query)
    save_json(RAW / 'locations.json', locations)
    ids = ','.join(SITES)
    years = ','.join(map(str, range(2001, 2025)))
    fetch('pam_soy.json', f'https://apisidra.ibge.gov.br/values/t/5457/n6/{ids}/v/8331,216,214,112/p/{years}/c782/40124')
    fetch('pam_maize2.json', f'https://apisidra.ibge.gov.br/values/t/839/n6/{ids}/v/109,216,214,112/p/{years}/c81/114254')


def valid_label_values(item):
    for key in ('area_planted_ha', 'area_harvested_ha', 'production_t', 'yield_kg_ha'):
        value = item.get(key)
        if value is None or not math.isfinite(value) or value < 0:
            return False
    return item['area_planted_ha'] > 0 and item['area_harvested_ha'] > 0


def normalize_labels():
    labels = {}
    columns = {'8331': 'area_planted_ha', '109': 'area_planted_ha', '216': 'area_harvested_ha',
               '214': 'production_t', '112': 'yield_kg_ha'}
    units = {'8331': '1006', '109': '1006', '216': '1006', '214': '1017', '112': '33'}
    for crop, table, category in [('soy', 5457, '40124'), ('maize2', 839, '114254')]:
        raw = decode_json((RAW / f'pam_{crop}.json').read_bytes())
        seen = set()
        for row in raw[1:]:
            code, year, var = row['D1C'], int(row['D3C']), row['D2C']
            if code not in SITES or row['D4C'] != category or row['MC'] != units[var]:
                raise ValueError('SIDRA dimension or unit mismatch')
            key = (code, crop, year)
            if (*key, var) in seen:
                raise ValueError('Duplicate SIDRA record')
            seen.add((*key, var))
            item = labels.setdefault(key, {'municipality_code': code, 'crop': crop, 'harvest_year': year,
                                           'table': table, 'target_scale': 'municipality',
                                           **{v: None for v in columns.values()}})
            item[columns[var]] = None if row['V'] in MISSING else float(row['V'])
        for key, item in labels.items():
            item['label_valid'] = valid_label_values(item)
            if item['label_valid']:
                expected = item['production_t'] * 1000 / item['area_harvested_ha']
                item['yield_consistency_error_kg_ha'] = round(abs(expected - item['yield_kg_ha']), 3)
                if abs(expected - item['yield_kg_ha']) > max(2, expected * .005):
                    item['label_valid'] = False
            else:
                item['yield_consistency_error_kg_ha'] = None
    return labels


def longest_dry(values):
    longest = current = 0
    for value in values:
        current = current + 1 if value is not None and value < 1 else 0
        longest = max(longest, current)
    return longest


def summarize(rows):
    complete = all(all(r[k] is not None for k in ('rain_mm', 't_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')) for r in rows)
    result = {'days': len(rows), 'complete': complete}
    if not complete or not rows:
        return result
    rain = [r['rain_mm'] for r in rows]
    return {**result, 'rain_sum_mm': round(sum(rain), 3), 'dry_days_lt1mm': sum(v < 1 for v in rain),
            'max_dry_spell_days': longest_dry(rain),
            't_min_mean_c': round(sum(r['t_min_c'] for r in rows) / len(rows), 3),
            't_max_mean_c': round(sum(r['t_max_c'] for r in rows) / len(rows), 3),
            'heat_days_gt35c': sum(r['t_max_c'] > 35 for r in rows),
            'radiation_sum_mj_m2': round(sum(r['radiation_mj_m2'] for r in rows), 3),
            'rh_mean_pct': round(sum(r['rh2m_pct'] for r in rows) / len(rows), 3),
            'wind_mean_m_s': round(sum(r['ws2m_m_s'] for r in rows) / len(rows), 3)}


def build():
    locations = json.loads((RAW / 'locations.json').read_bytes())
    labels = normalize_labels()
    daily, monthly, samples, exclusions, climate_audit = [], [], [], [], []
    for site in locations:
        code = site['municipality_code']
        payload = decode_json((RAW / f'power_{code}.json').read_bytes())
        rows = normalize_power(payload, START, END)
        params = payload['properties']['parameter']
        fill = payload['header']['fill_value']
        extras = [('RH2M', 'rh2m_pct', '%'), ('WS2M', 'ws2m_m_s', 'm/s'), ('T2MDEW', 'dewpoint_c', 'C'), ('PS', 'pressure_kpa', 'kPa')]
        for name, _, unit in extras:
            if payload['parameters'][name]['units'] != unit:
                raise ValueError(f'Unexpected {name} unit')
        for row in rows:
            key = row['day_utc'].replace('-', '')
            for name, field, _ in extras:
                value = params[name].get(key)
                row[field] = None if value is None or value == fill else float(value)
                if row[field] is not None and not math.isfinite(row[field]):
                    raise ValueError('Nonfinite weather')
            if row['rh2m_pct'] is not None and not 0 <= row['rh2m_pct'] <= 100:
                raise ValueError('Humidity out of range')
            if row['ws2m_m_s'] is not None and row['ws2m_m_s'] < 0:
                raise ValueError('Negative wind')
            row.update(municipality_code=code, point_method=site['point_method'], rain_reference_status='not_independently_validated_for_this_point')
        daily.extend(rows)
        grouped = defaultdict(list)
        for row in rows:
            grouped[row['day_utc'][:7]].append(row)
        for month, selected in sorted(grouped.items()):
            metrics = summarize(selected)
            # Fixed schema, including months with missing data.
            template = {k: None for k in summarize([{'rain_mm': 0, 't_min_c': 0, 't_max_c': 0, 'radiation_mj_m2': 0, 'rh2m_pct': 0, 'ws2m_m_s': 0}])}
            monthly.append({'municipality_code': code, 'month': month, **template, **metrics})
        climate_audit.append({'municipality_code': code, 'days': len(rows),
                              'missing_by_field': {field: sum(r[field] is None for r in rows) for field in ['rain_mm', 't_min_c', 't_max_c', 'radiation_mj_m2'] + [x[1] for x in extras]},
                              'source_parameters': payload['parameters'], 'source_header': payload['header']})
        for crop in ('soy', 'maize2'):
            for year in range(2002, 2025):
                label = labels.get((code, crop, year))
                start = date(year - 1, 10, 1) if crop == 'soy' else date(year, 1, 1)
                cutoff = date(year, 1, 31) if crop == 'soy' else date(year, 4, 30)
                selected = [r for r in rows if start.isoformat() <= r['day_utc'] <= cutoff.isoformat()]
                metrics = summarize(selected)
                if not label or not label['label_valid'] or not metrics['complete'] or len(selected) != (cutoff-start).days+1:
                    exclusions.append({'municipality_code': code, 'crop': crop, 'harvest_year': year, 'reason': 'invalid_label_or_incomplete_weather'})
                    continue
                # Conservative reporting lag; actual release-time reconstruction remains a gate.
                lag_labels = [labels.get((code, crop, year-i), {}) for i in (2, 3, 4)]
                past = [r['yield_kg_ha'] for r in lag_labels if r.get('label_valid')]
                sample = {'sample_id': f'{code}:{crop}:{year}', 'municipality_code': code,
                          'state_code': code[:2], 'crop': crop, 'harvest_year': year,
                          'window_start': start.isoformat(), 'feature_cutoff': cutoff.isoformat(),
                          'window_basis': 'fixed_calendar_proxy_NOT_observed_phenology',
                          'split_time': 'train' if year <= 2018 else ('validation' if year <= 2021 else 'test'),
                          'spatial_holdout': code in {'3170107', '5107909'},
                          'yield_kg_ha': label['yield_kg_ha'], 'historical_yield_lag2to4_mean': sum(past)/len(past) if past else None,
                          'label_lag_count': len(past), **metrics}
                samples.append(sample)
    write_csv('locations.csv', locations)
    write_csv('labels.csv', labels.values())
    write_csv('weather_daily.csv', daily)
    write_csv('weather_monthly.csv', monthly)
    write_csv('research_samples.csv', samples)
    save_json(OUT / 'quality_report.json', {'climate': climate_audit, 'exclusions': exclusions,
              'invalid_labels': [r for r in labels.values() if not r['label_valid']],
              'counts': {'locations': len(locations), 'daily_rows': len(daily), 'monthly_rows': len(monthly),
                         'labels': len(labels), 'samples': len(samples)},
              'production_ready': False,
              'gates': ['municipal_crop_area_weighted_weather', 'ground_rain_validation', 'historical_release_vintages',
                        'observed_phenology_and_field_yield_for_field_predictions', 'out_of_time_and_spatial_validation']})
    # National snapshot already collected: no second download.
    zarc = Path('data/raw/zarc/tabua-2026-2027.csv')
    with zarc.open(encoding='utf-8-sig', newline='') as stream:
        selected = [r for r in csv.DictReader(stream, delimiter=';') if r['geocodigo'] in SITES and r['Cod_Cultura'] in {'12016720000011', '12015080000021'}]
    write_csv('zarc_2026_2027_profiles.csv', selected)
    paths = list(RAW.rglob('*')) + list(OUT.glob('*.csv'))
    save_json(OUT / 'manifest.json', {'created_at': datetime.now(timezone.utc).isoformat(),
              'files': {str(p): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths if p.is_file()},
              'zarc_source_sha256': hashlib.sha256(zarc.read_bytes()).hexdigest(),
              'warning': 'Research cohort; municipal labels; current revised snapshots; not a field yield predictor.'})
    print(json.dumps({'daily': len(daily), 'samples': len(samples), 'excluded': len(exclusions), 'zarc_profiles': len(selected)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download:
        acquire()
    build()
