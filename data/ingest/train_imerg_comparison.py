"""Fit retrospective municipal yield models on identical Early/Late/Final samples."""
import csv
import json
from datetime import date, timedelta

from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES, features_at_cutoff
from data.ingest.imerg_operational import OUT, ROOT
from data.ingest.water_balance_run import save_json, sha, write_csv


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    readiness = OUT / 'readiness.json'
    pairs_path = OUT / 'rain_comparison_daily.csv'
    paired = {}
    for row in read_rows(pairs_path):
        if row['matched_complete'] == 'True':
            key = row['municipality_code'], row['day_utc']
            paired.setdefault(key, {}).update({
                row['run']: float(row['rain_mm']), 'Final': float(row['final_mm'])
            })
    start = date(2016, 1, 1)
    locations = json.loads((ROOT / 'data/raw/training_v1/locations.json').read_text(encoding='utf-8'))
    expected = {(str(loc['municipality_code']), (start + timedelta(days=i)).isoformat())
                for loc in locations for i in range((date(2022, 1, 1) - start).days)}
    complete = sum(set(paired.get(key, {})) == {'Early', 'Late', 'Final'} for key in expected)
    if complete != len(expected):
        raise SystemExit(f'Paired coverage incomplete: {complete}/{len(expected)} municipality-days.')

    import joblib
    import pandas as pd
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_absolute_error
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    weather_path = ROOT / 'data/processed/training_v1/weather_daily.csv'
    et_path = ROOT / 'data/processed/water_balance_v1/et0_daily.csv'
    labels_path = ROOT / 'data/processed/training_v1/research_samples.csv'
    fields = ('t_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')
    et = {(r['municipality_code'], r['day_utc']): float(r['et0_mm']) for r in read_rows(et_path)}
    weather = {}
    for row in read_rows(weather_path):
        key = row['municipality_code'], row['day_utc']
        if key in paired:
            weather[key] = {'day_utc': key[1], 'et0_mm': et[key],
                            **{field: float(row[field]) for field in fields}}

    samples = []
    for row in read_rows(labels_path):
        year = int(row['harvest_year'])
        if year not in (2017, 2018, 2020, 2021):
            continue
        code, crop = row['municipality_code'], row['crop']
        start = date(year-1, 11 if code.startswith('31') else 10, 5) if crop == 'soy' else date(year, 1, 5)
        for horizon in (60, 90):
            keys = [(code, (start+timedelta(days=i)).isoformat()) for i in range(horizon)]
            for run in ('Early', 'Late', 'Final'):
                prefix = [dict(weather[key], rain_mm=paired[key][run]) for key in keys]
                samples.append({'sample_id': row['sample_id'], 'year': year, 'crop': crop,
                                'horizon': horizon, 'run': run, 'yield_kg_ha': float(row['yield_kg_ha']),
                                'holdout': code in {'3170107', '5107909'},
                                **features_at_cutoff(prefix, crop, start, horizon)})
    output = OUT / 'retrospective_training'
    output.mkdir(parents=True, exist_ok=True)
    metrics = []
    contracts = {}
    for (crop, horizon, run), cohort in pd.DataFrame(samples).groupby(['crop', 'horizon', 'run']):
        train = cohort[(cohort.year <= 2018) & ~cohort.holdout]
        for variant, candidates in (
            ('weather', WEATHER_FEATURES), ('weather_hydro', [*WEATHER_FEATURES, *HYDRO_FEATURES])
        ):
            features = [f for f in candidates if train[f].nunique(dropna=True) > 1]
            model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), Ridge(alpha=10))
            model.fit(train[features], train.yield_kg_ha)
            filename = f'{crop}_{horizon}_{run}_{variant}.joblib'
            joblib.dump(model, output / filename)
            contracts[filename] = {'features': features, 'sha256': sha(output / filename)}
            for holdout in (False, True):
                validation = cohort[(cohort.year >= 2020) & (cohort.holdout == holdout)]
                mae = mean_absolute_error(validation.yield_kg_ha, model.predict(validation[features]))
                metrics.append({'crop': crop, 'horizon': horizon, 'run': run, 'variant': variant,
                                'spatial_holdout': holdout, 'train_n': len(train),
                                'validation_n': len(validation), 'mae_kg_ha': mae})
    write_csv(output / 'metrics.csv', metrics)
    save_json(output / 'manifest.json', {
        'inputs': {str(path.relative_to(ROOT)): sha(path) for path in (
            readiness, pairs_path, weather_path, et_path, labels_path)},
        'training_years': [2017, 2018], 'validation_years': [2020, 2021],
        'models': contracts,
        'reserved_test_years': [2022, 2023, 2024], 'operational_approved': False,
    })


if __name__ == '__main__':
    main()
