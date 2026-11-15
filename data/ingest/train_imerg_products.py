"""Yield models on identical examples, swapping only the rain product (Early/Late/Final).

Reads the pairs built by imerg_subset. ET0, temperature, radiation, humidity, wind and
labels stay fixed, so any difference comes from the rain input. Retrospective only:
fit on harvests 2017-2018, validate on 2020-2021, 2022-2024 never read into a model.
Twelve independent harvests per crop are too few for a network, so the comparison uses
the historical mean and Ridge; the MLP study lives in train_mlp_v4 on the longer cohort.
"""
import csv
import json
from datetime import date, timedelta

from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES, features_at_cutoff
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv

SOURCE = ROOT / 'data/processed/imerg_subset_v1'
OUT = SOURCE / 'retrospective_training'
RUNS = ('Early', 'Late', 'Final')
HOLDOUT = {'3170107', '5107909'}
METEO = ('t_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def role_of_year(year):
    return 'train' if year in (2017, 2018) else ('validation' if year in (2020, 2021) else None)


def sowing_date(code, crop, year):
    return date(year - 1, 11 if code.startswith('31') else 10, 5) if crop == 'soy' else date(year, 1, 5)


def paired_rain(rows):
    paired = {}
    for row in rows:
        if row['matched_complete'] == 'True':
            paired.setdefault((row['municipality_code'], row['day_utc']), {}).update(
                {row['run']: float(row['rain_mm']), 'Final': float(row['final_mm'])})
    return {key: value for key, value in paired.items() if set(value) == set(RUNS)}


def window_rain(paired, code, start, horizon):
    days = [(start + timedelta(days=i)).isoformat() for i in range(horizon)]
    gaps = [day for day in days if (code, day) not in paired]
    if gaps:
        return None, gaps
    return {run: [paired[(code, day)][run] for day in days] for run in RUNS}, []


def main():
    import joblib
    import numpy as np
    import pandas as pd
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    pairs_path = SOURCE / 'rain_comparison_daily.csv'
    coverage_path = SOURCE / 'coverage.json'
    weather_path = ROOT / 'data/processed/training_v1/weather_daily.csv'
    et_path = ROOT / 'data/processed/water_balance_v1/et0_daily.csv'
    labels_path = ROOT / 'data/processed/training_v1/research_samples.csv'
    paired = paired_rain(read_rows(pairs_path))
    et = {(r['municipality_code'], r['day_utc']): float(r['et0_mm']) for r in read_rows(et_path)}
    weather = {(r['municipality_code'], r['day_utc']): {'day_utc': r['day_utc'], 'et0_mm': et[(r['municipality_code'], r['day_utc'])],
                                                         **{f: float(r[f]) for f in METEO}}
               for r in read_rows(weather_path) if '2016-01-01' <= r['day_utc'] <= '2021-12-31'}

    samples, exclusions, families = [], [], set()
    for row in read_rows(labels_path):
        year = int(row['harvest_year'])
        role = role_of_year(year)
        if role is None:
            continue
        code, crop = row['municipality_code'], row['crop']
        start = sowing_date(code, crop, year)
        families.add(row['sample_id'])
        for horizon in (60, 90):
            rain, gaps = window_rain(paired, code, start, horizon)
            if rain is None:
                exclusions.append({'sample_id': row['sample_id'], 'horizon_days': horizon, 'missing_days': len(gaps),
                                   'first_missing_day': gaps[0], 'reason': 'window_without_three_complete_products'})
                continue
            days = [(start + timedelta(days=i)).isoformat() for i in range(horizon)]
            for run in RUNS:
                prefix = [dict(weather[(code, day)], rain_mm=value) for day, value in zip(days, rain[run])]
                samples.append({'sample_id': row['sample_id'], 'municipality_code': code, 'state_code': code[:2],
                                'crop': crop, 'harvest_year': year, 'horizon_days': horizon, 'run': run, 'role': role,
                                'spatial_holdout': code in HOLDOUT, 'feature_cutoff': days[-1],
                                'yield_kg_ha': float(row['yield_kg_ha']),
                                **features_at_cutoff(prefix, crop, start, horizon)})
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(samples)
    if frame.duplicated(['sample_id', 'horizon_days', 'run']).any():
        raise ValueError('Duplicated example keys')
    if (frame.groupby(['sample_id', 'horizon_days']).run.nunique() != 3).any():
        raise ValueError('Products do not share the same examples')
    write_csv(OUT / 'samples.csv', samples)
    write_csv(OUT / 'exclusions.csv', exclusions or [{'sample_id': '', 'horizon_days': '', 'missing_days': '',
                                                      'first_missing_day': '', 'reason': ''}])

    metrics, predictions, contracts = [], [], {}
    for (crop, horizon), cohort in frame.groupby(['crop', 'horizon_days']):
        for run in RUNS:
            part = cohort[cohort.run == run]
            train = part[(part.role == 'train') & ~part.spatial_holdout]
            fits = {'historical_mean': (None, [])}
            for variant, candidates in (('weather', WEATHER_FEATURES), ('weather_hydro', [*WEATHER_FEATURES, *HYDRO_FEATURES])):
                features = [f for f in candidates if train[f].nunique(dropna=True) > 1]
                model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), Ridge(alpha=10))
                model.fit(train[features], train.yield_kg_ha)
                filename = f'{crop}_{horizon}_{run}_{variant}.joblib'
                joblib.dump(model, OUT / filename)
                contracts[filename] = {'features': features, 'sha256': sha(OUT / filename), 'train_n': len(train)}
                fits[f'{variant}_ridge'] = (model, features)
            for name, (model, features) in fits.items():
                for scope, holdout in (('temporal_validation', False), ('spatiotemporal_validation', True)):
                    ev = part[(part.role == 'validation') & (part.spatial_holdout == holdout)]
                    pred = model.predict(ev[features]) if model is not None else np.full(len(ev), train.yield_kg_ha.mean())
                    error = pred - ev.yield_kg_ha.to_numpy()
                    metrics.append({'crop': crop, 'horizon_days': horizon, 'run': run, 'model': name, 'scope': scope,
                                    'train_n': len(train), 'validation_n': len(ev),
                                    'mae_kg_ha': float(np.abs(error).mean()), 'rmse_kg_ha': float(np.sqrt((error ** 2).mean())),
                                    'bias_kg_ha': float(error.mean())})
                    for (_, row), value in zip(ev.iterrows(), pred):
                        predictions.append({'crop': crop, 'horizon_days': horizon, 'run': run, 'model': name, 'scope': scope,
                                            'sample_id': row.sample_id, 'state_code': row.state_code,
                                            'actual_kg_ha': row.yield_kg_ha, 'predicted_kg_ha': float(value)})
    write_csv(OUT / 'metrics.csv', metrics)
    write_csv(OUT / 'validation_predictions.csv', predictions)

    # Paired effect of the rain product: same examples, same model family, Final as reference.
    table = pd.DataFrame(predictions)
    table['abs_error'] = (table.predicted_kg_ha - table.actual_kg_ha).abs()
    wide = table[table.model != 'historical_mean'].pivot_table(
        index=['crop', 'horizon_days', 'model', 'scope', 'sample_id'], columns='run', values='abs_error').reset_index()
    effect = []
    for keys, group in wide.groupby(['crop', 'horizon_days', 'model', 'scope']):
        for run in ('Early', 'Late'):
            delta = group[run] - group['Final']
            effect.append({**dict(zip(('crop', 'horizon_days', 'model', 'scope'), keys)), 'run': run, 'n': len(delta),
                           'final_mae_kg_ha': float(group['Final'].mean()), 'run_mae_kg_ha': float(group[run].mean()),
                           'delta_mae_vs_final_kg_ha': float(delta.mean()),
                           'examples_better_than_final': int((delta < 0).sum())})
    write_csv(OUT / 'paired_product_effect.csv', effect)

    rain = frame.pivot_table(index=['sample_id', 'horizon_days', 'crop'], columns='run', values='rain_sum_mm').reset_index()
    write_csv(OUT / 'window_rain_by_product.csv', rain.to_dict('records'))
    save_json(OUT / 'manifest.json', {
        'inputs': {p.relative_to(ROOT).as_posix(): sha(p) for p in (pairs_path, coverage_path, weather_path, et_path, labels_path)},
        'label_families_in_declared_years': len(families),
        'examples_per_product': int(len(frame) / 3), 'excluded_examples': len(exclusions),
        'training_years': [2017, 2018], 'gap_year': 2019, 'validation_years': [2020, 2021],
        'reserved_test_years': [2022, 2023, 2024], 'test_evaluated': False, 'spatial_holdout': sorted(HOLDOUT),
        'models': contracts, 'mlp_fitted': False,
        'mlp_reason': 'twelve independent training harvests per crop; see data/processed/training_v4 for the MLP study',
        'operational_approved': False, 'availability': 'retrospective_reprocessed_products_first_seen_2026'})
    print(pd.DataFrame(effect).to_string(index=False))


if __name__ == '__main__':
    main()
