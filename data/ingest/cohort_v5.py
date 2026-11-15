"""Expanded MG/MT cohort: more comparable municipalities, hence more independent harvests.

Labels: IBGE PAM (SIDRA 5457 soy, 839 second-crop maize), harvests up to 2021 only.
The 2022-2024 labels are not requested here, so the sealed test cannot leak.
Forcing: NASA POWER daily point (IMERG_PRECTOT rain plus meteorology), same contract as v1.
Selection uses training years only (2002-2016).
"""
import argparse
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from data.ingest.hydro_training_features import features_at_cutoff
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv
from data.models.water_balance import et0_daily

RAW = ROOT / 'data/raw/training_v5'
OUT = ROOT / 'data/processed/training_v5'
STATES = ('31', '51')
MVP = ('3170206', '3170107', '3170404', '3147006', '5107925', '5107909', '5105259', '5102637')
MVP_HOLDOUT = {'3170107', '5107909'}
LABEL_YEARS = (2001, 2021)
TRAIN_YEARS = (2002, 2016)
CROPS = {'soy': {'table': 5457, 'planted': '8331', 'classification': 'c782/40124', 'category': '40124',
                 'first_year': 2001, 'min_train_years': 10, 'min_mean_area_ha': 5000},
         'maize2': {'table': 839, 'planted': '109', 'classification': 'c81/114254', 'category': '114254',
                    'first_year': 2003, 'min_train_years': 8, 'min_mean_area_ha': 2000}}
POWER_PARAMS = 'IMERG_PRECTOT,T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN,RH2M,WS2M,T2MDEW,PS'
POWER_UNITS = {'IMERG_PRECTOT': 'mm/day', 'T2M_MIN': 'C', 'T2M_MAX': 'C', 'ALLSKY_SFC_SW_DWN': 'MJ/m^2/day',
               'RH2M': '%', 'WS2M': 'm/s', 'T2MDEW': 'C', 'PS': 'kPa'}
POWER_FIELDS = {'IMERG_PRECTOT': 'rain_mm', 'T2M_MIN': 't_min_c', 'T2M_MAX': 't_max_c',
                'ALLSKY_SFC_SW_DWN': 'radiation_mj_m2', 'RH2M': 'rh2m_pct', 'WS2M': 'ws2m_m_s',
                'T2MDEW': 'dewpoint_c', 'PS': 'pressure_kpa'}
MISSING = {'-', '...', '..', 'X', 'x', ''}


def fetch(name, url, attempts=5, root=None):
    """Immutable cache: a file is reused only if its URL and hash match the receipt."""
    import requests
    path = (root or RAW) / name
    receipt = path.with_suffix(path.suffix + '.manifest.json')
    if path.exists() and receipt.exists():
        meta = json.loads(receipt.read_text(encoding='utf-8'))
        if meta['url'] != url or meta['sha256'] != sha(path):
            raise ValueError(f'Cache provenance mismatch: {name}')
        return json.loads(path.read_bytes())
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=180, headers={'User-Agent': 'research-client/0.1', 'Accept': 'application/json'})
            response.raise_for_status()
            payload = response.json()
            break
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * 2 ** attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    save_json(receipt, {'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                        'bytes': len(response.content), 'sha256': hashlib.sha256(response.content).hexdigest()})
    return payload


def label_valid(item):
    values = [item.get(k) for k in ('area_harvested_ha', 'production_t', 'yield_kg_ha')]
    if any(v is None or not math.isfinite(v) or v < 0 for v in values) or item['area_harvested_ha'] <= 0:
        return False
    expected = item['production_t'] * 1000 / item['area_harvested_ha']
    # PAM publishes rounded yield; reject rows where production and area disagree with it.
    return abs(expected - item['yield_kg_ha']) <= max(2, expected * .005)


def parse_labels(payload, crop, years=LABEL_YEARS):
    config = CROPS[crop]
    columns = {'216': 'area_harvested_ha', '214': 'production_t', '112': 'yield_kg_ha'}
    units = {'216': '1006', '214': '1017', '112': '33'}
    labels, seen = {}, set()
    for row in payload[1:]:
        code, year, variable = row['D1C'], int(row['D3C']), row['D2C']
        if row['D4C'] != config['category'] or row['MC'] != units[variable] or len(code) != 7:
            raise ValueError('SIDRA dimension or unit mismatch')
        if not years[0] <= year <= years[1]:
            raise ValueError('Label year outside the allowed window; the 2022-2024 test is sealed')
        if (code, year, variable) in seen:
            raise ValueError('Duplicate SIDRA record')
        seen.add((code, year, variable))
        item = labels.setdefault((code, year), {'municipality_code': code, 'municipality': row['D1N'], 'crop': crop,
                                                'harvest_year': year, **dict.fromkeys(columns.values())})
        item[columns[variable]] = None if row['V'] in MISSING else float(row['V'])
    for item in labels.values():
        item['label_valid'] = label_valid(item)
    return list(labels.values())


def select_municipalities(labels, crop):
    """Comparable producers, judged on training years only."""
    config = CROPS[crop]
    history = {}
    for item in labels:
        if item['label_valid'] and TRAIN_YEARS[0] <= item['harvest_year'] <= TRAIN_YEARS[1]:
            history.setdefault(item['municipality_code'], []).append(item['area_harvested_ha'])
    return sorted(code for code, areas in history.items()
                  if len(areas) >= config['min_train_years'] and sum(areas) / len(areas) >= config['min_mean_area_ha'])


def spatial_holdout(code):
    """The MVP split is fixed; other municipalities are held out by a stable hash (about one in five)."""
    if code in MVP:
        return code in MVP_HOLDOUT
    return int(hashlib.sha256(code.encode()).hexdigest(), 16) % 5 == 0


def split_of(year, holdout):
    if year <= TRAIN_YEARS[1]:
        return 'unused_spatial_history' if holdout else 'train'
    return 'embargo' if year <= 2018 else 'validation'


def sowing_date(code, crop, year):
    return date(year - 1, 11 if code.startswith('31') else 10, 5) if crop == 'soy' else date(year, 1, 5)


def lagged_yield(history, year):
    """PAM for year-2 and earlier is assumed published before the decision; year-1 is not used."""
    past = [history[year - lag] for lag in (2, 3, 4) if year - lag in history]
    return (sum(past) / len(past) if past else None), len(past)


def acquire(workers):
    from shapely.geometry import shape
    years = lambda first: ','.join(map(str, range(max(first, LABEL_YEARS[0]), LABEL_YEARS[1] + 1)))
    labels = {crop: [] for crop in CROPS}
    for crop, config in CROPS.items():
        for state in STATES:
            url = (f'https://apisidra.ibge.gov.br/values/t/{config["table"]}/n6/in%20n3%20{state}/v/216,214,112/'
                   f'p/{years(config["first_year"])}/{config["classification"]}')
            labels[crop].extend(parse_labels(fetch(f'pam_{crop}_{state}.json', url), crop))
    selected = {crop: set(select_municipalities(rows, crop)) | set(MVP) for crop, rows in labels.items()}
    codes = sorted(set().union(*selected.values()))
    points = {}
    for state in STATES:
        mesh = fetch(f'mesh_{state}.geojson', f'https://servicodados.ibge.gov.br/api/v3/malhas/estados/{state}'
                                              '?formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=municipio')
        for feature in mesh['features']:
            code = feature['properties']['codarea']
            if code in codes:
                point = shape(feature['geometry']).representative_point()
                points[code] = (round(point.x, 5), round(point.y, 5))
    if set(points) != set(codes):
        raise ValueError('Municipal mesh does not cover every selected municipality')
    names = {r['municipality_code']: r['municipality'] for rows in labels.values() for r in rows}
    locations = [{'municipality_code': code, 'municipality': names[code], 'state_code': code[:2],
                  'longitude': points[code][0], 'latitude': points[code][1], 'mvp': code in MVP,
                  'spatial_holdout': spatial_holdout(code), 'soy': code in selected['soy'], 'maize2': code in selected['maize2'],
                  'point_method': 'IBGE_polygon_representative_point_NOT_farm_or_crop_weighted'} for code in codes]
    save_json(RAW / 'locations.json', locations)
    print(f'{len(codes)} municipalities (soy {len(selected["soy"])}, maize2 {len(selected["maize2"])})', flush=True)

    def power(location):
        lon, lat = location['longitude'], location['latitude']
        url = ('https://power.larc.nasa.gov/api/temporal/daily/point?'
               f'parameters={POWER_PARAMS}&community=AG&longitude={lon}&latitude={lat}'
               '&start=20010101&end=20241231&format=JSON&time-standard=UTC')
        try:
            fetch(f'power/power_{location["municipality_code"]}.json', url)
            return None
        except Exception as error:
            return f'{location["municipality_code"]}: {type(error).__name__}'
    with ThreadPoolExecutor(workers) as pool:
        failures = [f for f in pool.map(power, locations) if f]
    print(f'POWER failures: {failures}', flush=True)
    if failures:
        raise SystemExit('POWER acquisition incomplete; rerun to resume.')


def daily_weather(code, root=None):
    payload = json.loads(((root or RAW) / f'power/power_{code}.json').read_bytes())
    return parse_power(payload, code, date(2001, 1, 1), date(2024, 12, 31))


def parse_power(payload, code, first, last):
    """Daily rows with FAO-56 ET0 from a POWER payload; shared by the cohort builders and live serving."""
    if payload['header'].get('time_standard') != 'UTC':
        raise ValueError('POWER time standard must be UTC')
    for name, unit in POWER_UNITS.items():
        if payload['parameters'][name]['units'] != unit:
            raise ValueError(f'Unexpected POWER unit: {name}')
    fill = payload['header']['fill_value']
    lon, lat, elevation = payload['geometry']['coordinates']
    series = payload['properties']['parameter']
    rows = {}
    day = first
    while day <= last:
        key = day.strftime('%Y%m%d')
        values = {field: series[name].get(key) for name, field in POWER_FIELDS.items()}
        values = {k: None if v is None or v == fill or not math.isfinite(v) else float(v) for k, v in values.items()}
        row = {'municipality_code': code, 'day_utc': day.isoformat(), **values, 'et0_mm': None}
        needed = [values[k] for k in ('t_min_c', 't_max_c', 'radiation_mj_m2', 'ws2m_m_s', 'pressure_kpa', 'dewpoint_c')]
        if None not in needed and values['t_min_c'] <= values['t_max_c'] and values['radiation_mj_m2'] >= 0:
            row['et0_mm'] = et0_daily(day, lat, elevation, *needed[:5], dewpoint=needed[5])['et0_mm']
        rows[row['day_utc']] = row
        day += timedelta(days=1)
    return rows, elevation


def build():
    import pandas as pd
    locations = json.loads((RAW / 'locations.json').read_text(encoding='utf-8'))
    labels = []
    for crop in CROPS:
        for state in STATES:
            labels.extend(parse_labels(json.loads((RAW / f'pam_{crop}_{state}.json').read_bytes()), crop))
    history = {}
    for item in labels:
        if item['label_valid']:
            history.setdefault((item['municipality_code'], item['crop']), {})[item['harvest_year']] = item['yield_kg_ha']
    samples, exclusions, coverage = [], [], []
    required = ('rain_mm', 'et0_mm', 't_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')
    for index, location in enumerate(locations, 1):
        code = location['municipality_code']
        weather, elevation = daily_weather(code)
        coverage.append({'municipality_code': code, 'days': len(weather), 'elevation_m': elevation,
                         **{f'missing_{f}': sum(r[f] is None for r in weather.values()) for f in required}})
        for crop in CROPS:
            if not location[crop]:
                continue
            for year, value in sorted(history.get((code, crop), {}).items()):
                if year < 2002:
                    continue
                start = sowing_date(code, crop, year)
                lag_mean, lag_count = lagged_yield(history[(code, crop)], year)
                for horizon in (60, 90):
                    prefix = [weather[(start + timedelta(days=i)).isoformat()] for i in range(horizon)]
                    key = {'sample_id': f'{code}:{crop}:{year}', 'municipality_code': code, 'crop': crop,
                           'harvest_year': year, 'horizon_days': horizon}
                    gaps = sum(any(r[f] is None for f in required) for r in prefix)
                    if gaps:
                        exclusions.append({**key, 'reason': 'incomplete_forcing_window', 'days_with_missing_values': gaps})
                        continue
                    samples.append({**key, 'state_code': code[:2], 'mvp': location['mvp'],
                                    'sowing_date': start.isoformat(), 'feature_cutoff': prefix[-1]['day_utc'],
                                    'split': split_of(year, location['spatial_holdout']),
                                    'spatial_holdout': location['spatial_holdout'], 'yield_kg_ha': value,
                                    'yield_lag2to4_mean_kg_ha': lag_mean, 'yield_lag_count': lag_count,
                                    'latitude': location['latitude'], 'longitude': location['longitude'],
                                    'elevation_m': elevation, **features_at_cutoff(prefix, crop, start, horizon)})
        if index % 50 == 0:
            print(f'{index}/{len(locations)} municipalities', flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(samples)
    if frame.duplicated(['sample_id', 'horizon_days']).any() or (frame.harvest_year > LABEL_YEARS[1]).any():
        raise ValueError('Duplicated keys or sealed years in the cohort')
    if (frame.feature_cutoff >= frame.harvest_year.astype(str) + '-07-01').any():
        raise ValueError('Feature cutoff after the harvest season')
    frame.to_parquet(OUT / 'samples.parquet', index=False)
    write_csv(OUT / 'labels.csv', labels)
    write_csv(OUT / 'locations.csv', locations)
    write_csv(OUT / 'forcing_coverage.csv', coverage)
    write_csv(OUT / 'exclusions.csv', exclusions or [{'sample_id': '', 'reason': ''}])
    summary = frame[frame.horizon_days == 60].groupby(['crop', 'split']).agg(
        label_families=('sample_id', 'nunique'), municipalities=('municipality_code', 'nunique')).reset_index()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'municipalities': len(locations),
              'rows': len(frame), 'label_families': int(frame.sample_id.nunique()), 'excluded_examples': len(exclusions),
              'invalid_labels': sum(not r['label_valid'] for r in labels), 'labels_read': len(labels),
              'duplicate_example_keys': 0, 'observations_after_cutoff': 0,
              'max_label_year': int(frame.harvest_year.max()), 'test_rows_2022_2024': 0,
              'by_crop_and_split': summary.to_dict('records'),
              'selection_rule': {c: {k: v for k, v in CROPS[c].items() if k.startswith('min_')} for c in CROPS},
              'selection_years': list(TRAIN_YEARS), 'availability_status': 'retrospective_only_vintages_unverified',
              'lagged_yield_assumption': 'PAM of harvest year-2 and earlier assumed published before the decision date',
              'raw_bytes': sum(p.stat().st_size for p in RAW.rglob('*') if p.is_file())}
    save_json(OUT / 'quality_report.json', report)
    print(summary.to_string(index=False))
    print(json.dumps({k: report[k] for k in ('municipalities', 'rows', 'label_families', 'excluded_examples', 'raw_bytes')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.download:
        acquire(args.workers)
    build()
