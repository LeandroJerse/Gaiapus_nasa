"""Multi-state, multi-horizon cohort for the horizon-conditional yield model.

Same sources and rules as cohort_v5 (PAM labels up to 2021, POWER forcing, selection on
2002-2016 only), extended to every soy-producing state and to cutoffs from 30 to 105 days
after sowing. Sowing dates are a fixed hypothetical calendar per state, not observed dates.
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from data.ingest import cohort_v5 as base
from data.ingest.hydro_training_features import features_at_cutoff
from data.ingest.water_balance_run import ROOT, save_json, write_csv

RAW = ROOT / 'data/raw/training_v6'
OUT = ROOT / 'data/processed/training_v6'
STATES = ('11', '15', '17', '21', '22', '29', '31', '35', '41', '42', '43', '50', '51', '52', '53')
HORIZONS = (30, 45, 60, 75, 90, 105)
TEST_YEARS = (2022, 2024)
# (month, day) of the hypothetical sowing; soy is sown in the year before the harvest year.
SOY_SOWING = {'51': (10, 5), '11': (10, 5), '50': (10, 15), '41': (10, 15), '35': (10, 15), '52': (10, 25),
              '53': (10, 25), '31': (11, 5), '42': (11, 10), '43': (11, 10), '29': (11, 20), '17': (11, 20),
              '21': (11, 20), '22': (11, 20), '15': (11, 20)}
MAIZE2_SOWING = {'51': (1, 5), '31': (1, 5), '11': (1, 20), '52': (2, 1), '53': (2, 1), '17': (2, 1), '21': (2, 1),
                 '22': (2, 1), '29': (2, 1), '15': (2, 1), '50': (2, 15), '41': (2, 15), '35': (2, 15),
                 '42': (2, 15), '43': (2, 15)}


def sowing_date(code, crop, year):
    month, day = (SOY_SOWING if crop == 'soy' else MAIZE2_SOWING)[code[:2]]
    return date(year - 1 if crop == 'soy' else year, month, day)


def acquire(workers):
    from shapely.geometry import shape
    labels = {crop: [] for crop in base.CROPS}
    for crop, config in base.CROPS.items():
        years = ','.join(map(str, range(max(config['first_year'], base.LABEL_YEARS[0]), base.LABEL_YEARS[1] + 1)))
        for state in STATES:
            url = (f'https://apisidra.ibge.gov.br/values/t/{config["table"]}/n6/in%20n3%20{state}/v/216,214,112/'
                   f'p/{years}/{config["classification"]}')
            labels[crop].extend(base.parse_labels(base.fetch(f'pam_{crop}_{state}.json', url, root=RAW), crop))
            print(f'labels {crop} {state}', flush=True)
    selected = {crop: set(base.select_municipalities(rows, crop)) for crop, rows in labels.items()}
    for crop in selected:
        selected[crop] |= set(base.MVP)
    codes = sorted(set().union(*selected.values()))
    points = {}
    for state in STATES:
        mesh = base.fetch(f'mesh_{state}.geojson', f'https://servicodados.ibge.gov.br/api/v3/malhas/estados/{state}'
                          '?formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=municipio', root=RAW)
        for feature in mesh['features']:
            code = feature['properties']['codarea']
            if code in codes:
                point = shape(feature['geometry']).representative_point()
                points[code] = (round(point.x, 5), round(point.y, 5))
    if set(points) != set(codes):
        raise ValueError('Municipal mesh does not cover every selected municipality')
    names = {r['municipality_code']: r['municipality'] for rows in labels.values() for r in rows}
    locations = [{'municipality_code': code, 'municipality': names[code], 'state_code': code[:2],
                  'longitude': points[code][0], 'latitude': points[code][1], 'mvp': code in base.MVP,
                  'spatial_holdout': base.spatial_holdout(code), 'soy': code in selected['soy'],
                  'maize2': code in selected['maize2'],
                  'point_method': 'IBGE_polygon_representative_point_NOT_farm_or_crop_weighted'} for code in codes]
    save_json(RAW / 'locations.json', locations)
    print(f'{len(codes)} municipalities (soy {len(selected["soy"])}, maize2 {len(selected["maize2"])})', flush=True)

    def power(location):
        url = ('https://power.larc.nasa.gov/api/temporal/daily/point?'
               f'parameters={base.POWER_PARAMS}&community=AG&longitude={location["longitude"]}&latitude={location["latitude"]}'
               '&start=20010101&end=20241231&format=JSON&time-standard=UTC')
        try:
            base.fetch(f'power/power_{location["municipality_code"]}.json', url, root=RAW)
            return None
        except Exception as error:
            return f'{location["municipality_code"]}: {type(error).__name__}'
    with ThreadPoolExecutor(workers) as pool:
        failures = [f for f in pool.map(power, locations) if f]
    print(f'POWER failures: {len(failures)} {failures[:10]}', flush=True)
    if failures:
        raise SystemExit('POWER acquisition incomplete; rerun to resume.')


def acquire_test():
    """Sealed 2022-2024 labels, kept in separate files and read only by build(test=True)."""
    for crop, config in base.CROPS.items():
        for state in STATES:
            url = (f'https://apisidra.ibge.gov.br/values/t/{config["table"]}/n6/in%20n3%20{state}/v/216,214,112/'
                   f'p/2022,2023,2024/{config["classification"]}')
            base.fetch(f'sealed_test/pam_{crop}_{state}.json', url, root=RAW)
            print(f'sealed labels {crop} {state}', flush=True)


def build(test=False):
    import pandas as pd
    locations = json.loads((RAW / 'locations.json').read_text(encoding='utf-8'))
    labels = []
    for crop in base.CROPS:
        for state in STATES:
            labels.extend(base.parse_labels(json.loads((RAW / f'pam_{crop}_{state}.json').read_bytes()), crop))
            if test:
                labels.extend(base.parse_labels(json.loads((RAW / f'sealed_test/pam_{crop}_{state}.json').read_bytes()),
                                                crop, years=TEST_YEARS))
    first_year, last_year = (TEST_YEARS[0], TEST_YEARS[1]) if test else (2002, base.LABEL_YEARS[1])
    history = {}
    for item in labels:
        if item['label_valid']:
            history.setdefault((item['municipality_code'], item['crop']), {})[item['harvest_year']] = item['yield_kg_ha']
    required = ('rain_mm', 'et0_mm', 't_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')
    samples, exclusions, coverage = [], [], []
    for index, location in enumerate(locations, 1):
        code = location['municipality_code']
        weather, elevation = base.daily_weather(code, root=RAW)
        coverage.append({'municipality_code': code, 'days': len(weather), 'elevation_m': elevation,
                         **{f'missing_{f}': sum(r[f] is None for r in weather.values()) for f in required}})
        for crop in base.CROPS:
            if not location[crop]:
                continue
            for year, value in sorted(history.get((code, crop), {}).items()):
                if not first_year <= year <= last_year:
                    continue
                start = sowing_date(code, crop, year)
                lag_mean, lag_count = base.lagged_yield(history[(code, crop)], year)
                for horizon in HORIZONS:
                    prefix = [weather[(start + timedelta(days=i)).isoformat()] for i in range(horizon)]
                    key = {'sample_id': f'{code}:{crop}:{year}', 'municipality_code': code, 'crop': crop,
                           'harvest_year': year, 'horizon_days': horizon}
                    gaps = sum(any(r[f] is None for f in required) for r in prefix)
                    if gaps:
                        exclusions.append({**key, 'reason': 'incomplete_forcing_window', 'days_with_missing_values': gaps})
                        continue
                    samples.append({**key, 'state_code': code[:2], 'mvp': location['mvp'],
                                    'spatial_holdout': location['spatial_holdout'],
                                    'sowing_date': start.isoformat(), 'feature_cutoff': prefix[-1]['day_utc'],
                                    'yield_kg_ha': value, 'yield_lag2to4_mean_kg_ha': lag_mean, 'yield_lag_count': lag_count,
                                    'latitude': location['latitude'], 'longitude': location['longitude'],
                                    'elevation_m': elevation, **features_at_cutoff(prefix, crop, start, horizon)})
        if index % 100 == 0:
            print(f'{index}/{len(locations)} municipalities', flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(samples)
    if frame.duplicated(['sample_id', 'horizon_days']).any() or not frame.harvest_year.between(first_year, last_year).all():
        raise ValueError('Duplicated keys or years outside the requested window')
    if test:
        frame.to_parquet(OUT / 'test_samples.parquet', index=False)
        save_json(OUT / 'test_quality_report.json', {
            'created_at': datetime.now(timezone.utc).isoformat(), 'rows': len(frame),
            'label_families': int(frame.sample_id.nunique()), 'years': sorted(int(y) for y in frame.harvest_year.unique()),
            'excluded_examples': len(exclusions), 'duplicate_example_keys': 0})
        print(json.dumps({'test_rows': len(frame), 'test_label_families': int(frame.sample_id.nunique())}))
        return
    frame.to_parquet(OUT / 'samples.parquet', index=False)
    write_csv(OUT / 'locations.csv', locations)
    write_csv(OUT / 'forcing_coverage.csv', coverage)
    write_csv(OUT / 'exclusions.csv', exclusions or [{'sample_id': '', 'reason': ''}])
    one = frame[frame.horizon_days == 60]
    by_state = one.groupby(['crop', 'state_code']).agg(label_families=('sample_id', 'nunique'),
                                                        municipalities=('municipality_code', 'nunique')).reset_index()
    write_csv(OUT / 'cohort_by_state.csv', by_state.to_dict('records'))
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'municipalities': len(locations), 'rows': len(frame),
              'label_families': int(frame.sample_id.nunique()), 'horizons': list(HORIZONS),
              'label_families_by_crop': {k: int(v) for k, v in one.groupby('crop').sample_id.nunique().items()},
              'municipalities_by_crop': {k: int(v) for k, v in one.groupby('crop').municipality_code.nunique().items()},
              'excluded_examples': len(exclusions), 'invalid_labels': sum(not r['label_valid'] for r in labels),
              'duplicate_example_keys': 0, 'max_label_year': int(frame.harvest_year.max()), 'test_rows_2022_2024': 0,
              'sowing_calendar': {'soy': SOY_SOWING, 'maize2': MAIZE2_SOWING, 'status': 'hypothetical_fixed_per_state'},
              'selection_rule': {c: {k: v for k, v in base.CROPS[c].items() if k.startswith('min_')} for c in base.CROPS},
              'availability_status': 'retrospective_only_vintages_unverified',
              'raw_bytes': sum(p.stat().st_size for p in RAW.rglob('*') if p.is_file())}
    save_json(OUT / 'quality_report.json', report)
    print(json.dumps({k: report[k] for k in ('municipalities', 'rows', 'label_families', 'label_families_by_crop',
                                             'municipalities_by_crop', 'excluded_examples', 'raw_bytes')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--workers', type=int, default=5)
    parser.add_argument('--test', action='store_true', help='Fetch and build the sealed 2022-2024 table (one-shot evaluation)')
    args = parser.parse_args()
    if args.test:
        acquire_test()
        build(test=True)
    else:
        if args.download:
            acquire(args.workers)
        build()
