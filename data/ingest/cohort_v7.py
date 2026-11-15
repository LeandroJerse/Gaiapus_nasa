"""Crop-expansion cohorts: annual crops from the registry and coffee by tree milestone.

Annual crops: PAM labels up to 2021, municipalities selected on 2002-2016 only, NASA
POWER forcing, cutoffs every 15 days after a hypothetical state sowing date.
Coffee: PAM table 1613 by species (arabica, conilon) from 2012 and the identified
"coffee total" series from 2001. Cutoffs are tree milestones (pre-flowering,
post-flowering, grain filling) counted from 1 June of the year before harvest, and
lagged yields respect the assumed PAM publication date at each milestone.
POWER files already fetched for v6 are reused; nothing is downloaded twice.
"""
import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from data.ingest import cohort_v5 as v5
from data.ingest.crops import REGISTRY, cycle_days, horizons, irrigated_system, sowing_date
from data.ingest.hydro_training_features import features_at_cutoff, weather_summary
from data.ingest.water_balance_run import ROOT, save_json, write_csv
from data.models.water_balance import bucket_day

RAW = ROOT / 'data/raw/training_v7'
V6_RAW = ROOT / 'data/raw/training_v6'
OUT = ROOT / 'data/processed/training_v7'
LABEL_YEARS = (2001, 2021)
NEW_CROPS = ('maize1', 'wheat', 'rice', 'bean1', 'bean2')
COFFEE = {
    'coffee_arabica': {'name': 'Café arábica', 'category': '31619', 'states': ('31',), 'first_year': 2012,
                       'milestones': {'pre_flowering': (8, 31, -1), 'post_flowering': (11, 30, -1), 'grain_filling': (2, 28, 0)}},
    'coffee_conilon': {'name': 'Café conilon', 'category': '31620', 'states': ('32',), 'first_year': 2012,
                       'milestones': {'pre_flowering': (7, 31, -1), 'post_flowering': (10, 31, -1), 'grain_filling': (1, 31, 0)}},
    'coffee_total_mg': {'name': 'Café total MG (espécie não separada)', 'category': '2723', 'states': ('31',), 'first_year': 2001,
                        'milestones': {'pre_flowering': (8, 31, -1), 'post_flowering': (11, 30, -1), 'grain_filling': (2, 28, 0)}},
}
# PAM Y-2 is assumed released around September of Y-1: not yet at pre-flowering.
COFFEE_MIN_LAG = {'pre_flowering': 3, 'post_flowering': 2, 'grain_filling': 2}
COFFEE_WATER = {'kc': 0.95, 'p': 0.40, 'cad_mm': 95.0, 'source': 'FAO-56 coffee (Kc 0.90-0.95, p 0.40); CAD AD6 hypothesis'}
COFFEE_MIN = {'min_years': 4, 'min_mean_area_ha': 200}  # species labels start in 2012: 4 of the 5 selection years


def parse_labels(payload, crop, category, years=LABEL_YEARS):
    columns = {'216': 'area_harvested_ha', '214': 'production_t', '112': 'yield_kg_ha'}
    units = {'216': '1006', '214': '1017', '112': '33'}
    labels, seen, rejected = {}, set(), []
    for row in payload[1:]:
        code, year, variable = row['D1C'], int(row['D3C']), row['D2C']
        if row['D4C'] != category or row['MC'] != units[variable] or len(code) != 7:
            raise ValueError('SIDRA dimension or unit mismatch')
        if not years[0] <= year <= years[1]:
            raise ValueError('Label year outside the allowed window; the 2022-2024 test is sealed')
        if (code, year, variable) in seen:
            raise ValueError('Duplicate SIDRA record')
        seen.add((code, year, variable))
        item = labels.setdefault((code, year), {'municipality_code': code, 'municipality': row['D1N'], 'crop': crop,
                                                'harvest_year': year, 'area_harvested_ha': None, 'production_t': None,
                                                'yield_kg_ha': None, '_raw': {}})
        item['_raw'][variable] = row['V']
        item[columns[variable]] = None if row['V'] in v5.MISSING else float(row['V'])
    out = []
    for item in labels.values():
        raw = item.pop('_raw')
        reason = None
        if all(raw.get(v) in v5.MISSING or raw.get(v) is None for v in units):
            reason = 'all_fields_absent'
        elif any(item[c] is None for c in columns.values()):
            reason = 'field_absent'
        elif item['area_harvested_ha'] == 0:
            reason = 'zero_harvested_area'
        elif any(not math.isfinite(item[c]) or item[c] < 0 for c in columns.values()):
            reason = 'invalid_value'
        elif not v5.label_valid(item):
            reason = 'production_area_yield_mismatch'
        item['label_valid'] = reason is None
        item['rejection_reason'] = reason or ''
        out.append(item)
    return out


def select(labels, min_years, min_area, years=(2002, 2016)):
    history = {}
    for item in labels:
        if item['label_valid'] and years[0] <= item['harvest_year'] <= years[1]:
            history.setdefault(item['municipality_code'], []).append(item['area_harvested_ha'])
    return sorted(c for c, a in history.items() if len(a) >= min_years and sum(a) / len(a) >= min_area)


def fetch_cached(name, url):
    """Reuse an identical v6 download when its receipt matches; otherwise fetch into v7."""
    if (V6_RAW / name).exists() and (V6_RAW / (name + '.manifest.json')).exists():
        receipt = json.loads((V6_RAW / (name + '.manifest.json')).read_text(encoding='utf-8'))
        if receipt['url'] == url:
            return v5.fetch(name, url, root=V6_RAW)
    return v5.fetch(name, url, root=RAW)


def power_root(code):
    return V6_RAW if (V6_RAW / f'power/power_{code}.json.manifest.json').exists() else RAW


def coffee_features(weather, start, cutoff):
    """Weather and a constant-Kc coffee bucket from 1 June of the year before harvest to the cutoff."""
    days = (cutoff - start).days + 1
    rows = [weather[(start + timedelta(days=i)).isoformat()] for i in range(days)]
    if any(r[k] is None for r in rows for k in ('rain_mm', 'et0_mm', 't_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')):
        return None
    result = weather_summary(rows)
    storage, deficit, stress, eta, etc_total = .7 * COFFEE_WATER['cad_mm'], 0.0, 0, 0.0, 0.0
    windows = {'dry_season': (6, 7, 8, 9), 'flowering': (9, 10, 11), 'filling': (12, 1, 2)}
    window_deficit = {k: 0.0 for k in windows}
    for r in rows:
        etc = COFFEE_WATER['kc'] * r['et0_mm']
        item = bucket_day(storage, COFFEE_WATER['cad_mm'], r['rain_mm'], etc, COFFEE_WATER['p'])
        storage = item['storage_end_mm']
        deficit += item['et_deficit_mm']; eta += item['eta_mm']; etc_total += etc; stress += item['ks'] < 1 - 1e-9
        for key, months in windows.items():
            if int(r['day_utc'][5:7]) in months:
                window_deficit[key] += item['et_deficit_mm']
    result.update(coffee_deficit_mm=deficit, coffee_stress_days=stress, coffee_eta_etc_ratio=eta / etc_total if etc_total else None,
                  coffee_storage_end_mm=storage, **{f'coffee_deficit_{k}_mm': v for k, v in window_deficit.items()})
    return result


def previous_season(weather, harvest_year):
    """Rain and heat over the previous production year (June Y-2 to May Y-1), entirely before every milestone."""
    start, end = date(harvest_year - 2, 6, 1), date(harvest_year - 1, 5, 31)
    rows = [weather.get((start + timedelta(days=i)).isoformat()) for i in range((end - start).days + 1)]
    if any(r is None or r['rain_mm'] is None or r['t_max_c'] is None for r in rows):
        return {'prev_rain_mm': None, 'prev_heat_days': None}
    return {'prev_rain_mm': sum(r['rain_mm'] for r in rows), 'prev_heat_days': sum(r['t_max_c'] > 34 for r in rows)}


def coffee_lags(history, year, milestone):
    minimum = COFFEE_MIN_LAG[milestone]
    lags = {f'yield_lag{k}_kg_ha': (history.get(year - k) if k >= minimum else None) for k in range(2, 7)}
    available = [history[year - k] for k in range(minimum, 7) if year - k in history]
    same_phase = [history[year - k] for k in (2, 4, 6) if k >= minimum and year - k in history]
    lags['ref_last_available_kg_ha'] = next((history[year - k] for k in range(minimum, 7) if year - k in history), None)
    lags['ref_same_phase_kg_ha'] = same_phase[0] if same_phase else None
    lags['ref_biennial_mean_kg_ha'] = sum(same_phase[:2]) / len(same_phase[:2]) if same_phase else None
    lags['ref_cycle_mean_kg_ha'] = sum(available[:2]) / len(available[:2]) if available else None
    lags['yield_lag_count'] = len(available)
    return lags


def acquire(workers):
    from shapely.geometry import shape
    labels, selected = [], {}
    years = lambda first: ','.join(map(str, range(max(first, LABEL_YEARS[0]), LABEL_YEARS[1] + 1)))
    for crop in NEW_CROPS:
        entry = REGISTRY[crop]
        rows = []
        for state in entry['sowing']:
            url = (f'https://apisidra.ibge.gov.br/values/t/{entry["table"]}/n6/in%20n3%20{state}/v/216,214,112/'
                   f'p/{years(entry["first_year"])}/{entry["classification"]}/{entry["category"]}')
            rows.extend(parse_labels(fetch_cached(f'pam_{crop}_{state}.json', url), crop, entry['category']))
        labels.extend(rows)
        selected[crop] = select(rows, entry['min_train_years'], entry['min_mean_area_ha'])
        print(f'{crop}: {len(selected[crop])} municipalities selected', flush=True)
    for crop, entry in COFFEE.items():
        rows = []
        for state in entry['states']:
            url = (f'https://apisidra.ibge.gov.br/values/t/1613/n6/in%20n3%20{state}/v/216,214,112/'
                   f'p/{years(entry["first_year"])}/c82/{entry["category"]}')
            rows.extend(parse_labels(fetch_cached(f'pam_{crop}_{state}.json', url), crop, entry['category']))
        labels.extend(rows)
        selected[crop] = select(rows, COFFEE_MIN['min_years'] if entry['first_year'] >= 2012 else 10, COFFEE_MIN['min_mean_area_ha'],
                                years=(entry['first_year'], 2016) if entry['first_year'] >= 2012 else (2002, 2016))
        print(f'{crop}: {len(selected[crop])} municipalities selected', flush=True)
    states = sorted({s for c in NEW_CROPS for s in REGISTRY[c]['sowing']} | {s for e in COFFEE.values() for s in e['states']})
    codes = sorted(set().union(*selected.values()))
    points = {}
    for state in states:
        mesh = fetch_cached(f'mesh_{state}.geojson', f'https://servicodados.ibge.gov.br/api/v3/malhas/estados/{state}'
                            '?formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=municipio')
        for feature in mesh['features']:
            if feature['properties']['codarea'] in codes:
                point = shape(feature['geometry']).representative_point()
                points[feature['properties']['codarea']] = (round(point.x, 5), round(point.y, 5))
    if set(points) != set(codes):
        raise ValueError('Municipal mesh does not cover every selected municipality')
    names = {r['municipality_code']: r['municipality'] for r in labels}
    locations = [{'municipality_code': c, 'municipality': names[c], 'state_code': c[:2], 'longitude': points[c][0],
                  'latitude': points[c][1], 'spatial_holdout': v5.spatial_holdout(c), 'mvp': c in v5.MVP,
                  **{crop: c in selected[crop] for crop in selected}} for c in codes]
    save_json(RAW / 'locations.json', locations)
    write_csv(RAW / 'labels_all.csv', labels)
    print(f'{len(codes)} municipalities; {sum(power_root(c) == V6_RAW for c in codes)} reuse v6 POWER files', flush=True)

    def power(location):
        code = location['municipality_code']
        if power_root(code) == V6_RAW:
            return None
        url = ('https://power.larc.nasa.gov/api/temporal/daily/point?'
               f'parameters={v5.POWER_PARAMS}&community=AG&longitude={location["longitude"]}&latitude={location["latitude"]}'
               '&start=20010101&end=20241231&format=JSON&time-standard=UTC')
        try:
            v5.fetch(f'power/power_{code}.json', url, root=RAW)
            return None
        except Exception as error:
            return f'{code}: {type(error).__name__}'
    with ThreadPoolExecutor(workers) as pool:
        failures = [f for f in pool.map(power, locations) if f]
    print(f'POWER failures: {len(failures)} {failures[:10]}', flush=True)
    if failures:
        raise SystemExit('POWER acquisition incomplete; rerun to resume.')


TEST_YEARS = (2022, 2024)


def acquire_test():
    """Sealed 2022-2024 labels for every v7 crop, kept apart and read only by build(test=True)."""
    rows = []
    years = ','.join(map(str, range(TEST_YEARS[0], TEST_YEARS[1] + 1)))
    for crop in NEW_CROPS:
        entry = REGISTRY[crop]
        for state in entry['sowing']:
            url = (f'https://apisidra.ibge.gov.br/values/t/{entry["table"]}/n6/in%20n3%20{state}/v/216,214,112/'
                   f'p/{years}/{entry["classification"]}/{entry["category"]}')
            rows.extend(parse_labels(v5.fetch(f'sealed_test/pam_{crop}_{state}.json', url, root=RAW), crop, entry['category'], years=TEST_YEARS))
    for crop, entry in COFFEE.items():
        for state in entry['states']:
            url = f'https://apisidra.ibge.gov.br/values/t/1613/n6/in%20n3%20{state}/v/216,214,112/p/{years}/c82/{entry["category"]}'
            rows.extend(parse_labels(v5.fetch(f'sealed_test/pam_{crop}_{state}.json', url, root=RAW), crop, entry['category'], years=TEST_YEARS))
    write_csv(RAW / 'sealed_test/labels_test.csv', rows)
    print(f'sealed labels: {len(rows)}', flush=True)


def build(test=False):
    import pandas as pd
    locations = json.loads((RAW / 'locations.json').read_text(encoding='utf-8'))
    labels = pd.read_csv(RAW / 'labels_all.csv', dtype={'municipality_code': str})
    if test:
        labels = pd.concat([labels, pd.read_csv(RAW / 'sealed_test/labels_test.csv', dtype={'municipality_code': str})], ignore_index=True)
    first_year = TEST_YEARS[0] if test else 2002
    last_year = TEST_YEARS[1] if test else LABEL_YEARS[1]
    valid = labels[labels.label_valid]
    history = {k: dict(zip(g.harvest_year, g.yield_kg_ha)) for k, g in valid.groupby(['municipality_code', 'crop'])}
    rows, exclusions, coverage = [], [], []
    for index, location in enumerate(locations, 1):
        code = location['municipality_code']
        weather, elevation = v5.daily_weather(code, root=power_root(code))
        base = {'municipality_code': code, 'state_code': code[:2], 'mvp': location['mvp'], 'spatial_holdout': location['spatial_holdout'],
                'latitude': location['latitude'], 'longitude': location['longitude'], 'elevation_m': elevation}
        for crop in NEW_CROPS:
            if not location.get(crop):
                continue
            entry = REGISTRY[crop]
            for year, value in sorted(history.get((code, crop), {}).items()):
                if not first_year <= year <= last_year:
                    continue
                start = sowing_date(crop, code, year)
                lag_mean, lag_count = v5.lagged_yield(history[(code, crop)], year)
                for horizon in horizons(crop):
                    prefix = [weather[(start + timedelta(days=i)).isoformat()] for i in range(horizon)]
                    key = {'sample_id': f'{code}:{crop}:{year}', 'crop': crop, 'harvest_year': year, 'horizon_days': horizon}
                    if any(r[f] is None for r in prefix for f in ('rain_mm', 'et0_mm', 't_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')):
                        exclusions.append({**key, 'municipality_code': code, 'reason': 'incomplete_forcing_window'})
                        continue
                    features = features_at_cutoff(prefix, crop, start, horizon, config=entry['water'], weather_only=entry['water'] is None)
                    rows.append({**key, **base, 'sowing_date': start.isoformat(), 'feature_cutoff': prefix[-1]['day_utc'],
                                 'milestone': f'day_{horizon}', 'irrigated_system': irrigated_system(crop, code),
                                 'yield_kg_ha': value, 'yield_lag2to4_mean_kg_ha': lag_mean, 'yield_lag_count': lag_count, **features})
        for crop, entry in COFFEE.items():
            if not location.get(crop):
                continue
            series = history.get((code, crop), {})
            for year, value in sorted(series.items()):
                if year < entry['first_year'] + 2 or not first_year <= year <= last_year:
                    continue
                start = date(year - 1, 6, 1)
                for milestone, (month, day, offset) in entry['milestones'].items():
                    cutoff = date(year + offset, month, day)
                    key = {'sample_id': f'{code}:{crop}:{year}', 'crop': crop, 'harvest_year': year,
                           'horizon_days': (cutoff - start).days + 1}
                    features = coffee_features(weather, start, cutoff)
                    if features is None:
                        exclusions.append({**key, 'municipality_code': code, 'reason': 'incomplete_forcing_window'})
                        continue
                    rows.append({**key, **base, 'sowing_date': start.isoformat(), 'feature_cutoff': cutoff.isoformat(),
                                 'milestone': milestone, 'irrigated_system': False, 'yield_kg_ha': value,
                                 'high_year': None, **coffee_lags(series, year, milestone), **previous_season(weather, year),
                                 **features})
        coverage.append({'municipality_code': code, 'elevation_m': elevation, 'power_root': power_root(code).name})
        if index % 200 == 0:
            print(f'{index}/{len(locations)} municipalities', flush=True)
    frame = pd.DataFrame(rows)
    # Biennial phase of each coffee harvest: above or below the municipal mean of the two neighbouring years (descriptive only).
    coffee = frame.crop.str.startswith('coffee')
    for (code, crop), group in frame[coffee].groupby(['municipality_code', 'crop']):
        series = history.get((code, crop), {})
        for year in group.harvest_year.unique():
            around = [series[y] for y in (year - 1, year + 1) if y in series]
            if around:
                frame.loc[group.index[group.harvest_year == year], 'high_year'] = series[year] > sum(around) / len(around)
    if frame.duplicated(['sample_id', 'horizon_days']).any() or not frame.harvest_year.between(first_year, last_year).all():
        raise ValueError('Duplicated keys or years outside the requested window')
    if (pd.to_datetime(frame.feature_cutoff) >= pd.to_datetime(frame.harvest_year.astype(str) + '-12-31')).any():
        raise ValueError('Cutoff after the harvest year')
    OUT.mkdir(parents=True, exist_ok=True)
    if test:
        frame.to_parquet(OUT / 'test_samples.parquet', index=False)
        write_csv(OUT / 'test_exclusions.csv', exclusions or [{'sample_id': '', 'reason': ''}])
        print(frame.drop_duplicates('sample_id').groupby('crop').agg(harvests=('sample_id', 'nunique'),
              municipalities=('municipality_code', 'nunique'), years=('harvest_year', lambda s: sorted(s.unique()))).to_string())
        return
    frame.to_parquet(OUT / 'samples.parquet', index=False)
    write_csv(OUT / 'exclusions.csv', exclusions or [{'sample_id': '', 'reason': ''}])
    write_csv(OUT / 'forcing_coverage.csv', coverage)
    rejected = labels[~labels.label_valid].groupby(['crop', 'rejection_reason']).size().reset_index(name='labels')
    write_csv(OUT / 'label_rejections.csv', rejected.to_dict('records') or [{'crop': '', 'rejection_reason': '', 'labels': 0}])
    one = frame.sort_values('horizon_days').drop_duplicates('sample_id')
    summary = one.groupby('crop').agg(harvests=('sample_id', 'nunique'), municipalities=('municipality_code', 'nunique'),
                                      states=('state_code', 'nunique'), first_year=('harvest_year', 'min'),
                                      last_year=('harvest_year', 'max')).reset_index()
    summary['held_out_municipalities'] = summary.crop.map(one[one.spatial_holdout].groupby('crop').municipality_code.nunique())
    write_csv(OUT / 'cohort_summary.csv', summary.to_dict('records'))
    save_json(OUT / 'quality_report.json', {
        'created_at': datetime.now(timezone.utc).isoformat(), 'rows': len(frame), 'harvests': int(frame.sample_id.nunique()),
        'excluded_examples': len(exclusions), 'labels_read': len(labels), 'labels_rejected': int((~labels.label_valid).sum()),
        'duplicate_example_keys': 0, 'max_label_year': int(frame.harvest_year.max()), 'test_rows_2022_2024': 0,
        'calendars': {c: REGISTRY[c]['sowing'] for c in NEW_CROPS}, 'coffee_milestones': {c: e['milestones'] for c, e in COFFEE.items()},
        'coffee_min_lag': COFFEE_MIN_LAG, 'coffee_water': COFFEE_WATER,
        'water_sources': {c: REGISTRY[c]['water_source'] for c in NEW_CROPS},
        'availability_status': 'retrospective_only_vintages_unverified',
        'raw_bytes_v7': sum(p.stat().st_size for p in RAW.rglob('*') if p.is_file())})
    print(summary.to_string(index=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--test', action='store_true', help='Fetch the sealed 2022-2024 labels and build the test table')
    args = parser.parse_args()
    if args.test:
        acquire_test()
        build(test=True)
    else:
        if args.download:
            acquire(args.workers)
        build()
