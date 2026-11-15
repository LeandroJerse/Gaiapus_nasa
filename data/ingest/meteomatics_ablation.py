"""Does Meteomatics soil moisture add skill? Same samples, same protocol, with and without it.

Rows are the MG/MT part of the v6 cohort that has a complete soil-moisture window.
Both feature sets are fitted on exactly those rows with the walk-forward protocol of
train_v6. The soil-moisture series is a reconstruction retrieved in 2026 whose past-date
source Meteomatics does not document, so this is a retrospective test, not evidence of operational availability.
"""
import json

from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES
from data.ingest.train_mlp_v4 import cluster_bootstrap_ci, scores
from data.ingest.train_v6 import BASE, DATA, HGB_GRID, RIDGE_GRID, SITE, VALIDATION_YEARS, Baseline, PerHorizon, fold, inner_split

# MLP experiments run only on CUDA (train_v7); this source test compares Ridge and boosting, as the plan asks.


def select_linear_and_boosting(train, feature_sets):
    import numpy as np
    import pandas as pd
    fit, stop = inner_split(train)
    base = Baseline().fit(fit)
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - base.predict(train), index=train.index)
    truth = residual[stop.index].to_numpy(float)
    chosen = {}
    for name, features in feature_sets.items():
        for kind, grid in (('ridge', RIDGE_GRID), ('hgb', HGB_GRID)):
            errors = [float(np.abs(PerHorizon(kind, p, features).fit(fit, residual).predict(stop) - truth).mean()) for p in grid]
            chosen[(name, kind)] = grid[int(np.argmin(errors))]
    return chosen
from data.ingest.water_balance_run import save_json, sha, write_csv

OUT = DATA / 'meteomatics_ablation'


def main():
    import pandas as pd
    samples = pd.read_parquet(DATA / 'samples.parquet')
    soil = pd.read_parquet(DATA / 'meteomatics_features.parquet')
    frame = samples.merge(soil, on=['sample_id', 'horizon_days'], how='inner', validate='one_to_one')
    soil_columns = [c for c in soil.columns if c not in ('sample_id', 'horizon_days')]
    complete = frame.groupby('sample_id').horizon_days.nunique() == samples.horizon_days.nunique()
    frame = frame[frame.sample_id.isin(complete[complete].index)]
    without = [*BASE, *WEATHER_FEATURES, *HYDRO_FEATURES, *SITE]
    feature_sets = {'without_soil_moisture': without, 'with_soil_moisture': [*without, *soil_columns]}
    OUT.mkdir(parents=True, exist_ok=True)
    metrics, effects, chosen_log = [], [], {}
    for crop in ('soy', 'maize2'):
        cohort = frame[frame.crop == crop].reset_index(drop=True)
        first_train, _ = fold(cohort, VALIDATION_YEARS[0])
        chosen = select_linear_and_boosting(first_train, feature_sets)
        chosen_log[crop] = {'|'.join(k): v for k, v in chosen.items()}
        outputs = []
        for year in VALIDATION_YEARS:
            train, target = fold(cohort, year)
            base = Baseline().fit(train)
            residual = pd.Series(train.yield_kg_ha.to_numpy(float) - base.predict(train), index=train.index)
            base_target = base.predict(target)
            meta = target[['sample_id', 'municipality_code', 'harvest_year', 'horizon_days', 'spatial_holdout', 'mvp', 'yield_kg_ha']]
            outputs.append(meta.assign(features='history_only', model='baseline', predicted_kg_ha=base_target))
            for name, features in feature_sets.items():
                for kind in ('ridge', 'hgb'):
                    model = PerHorizon(kind, chosen[(name, kind)], features).fit(train, residual)
                    outputs.append(meta.assign(features=name, model=kind, predicted_kg_ha=base_target + model.predict(target)))
            print(crop, year, 'train harvests', train.sample_id.nunique(), flush=True)
        table = pd.concat(outputs)
        table['abs_error'] = (table.predicted_kg_ha - table.yield_kg_ha).abs()
        for scope, mask in (('temporal', ~table.spatial_holdout), ('spatiotemporal', table.spatial_holdout)):
            part = table[mask]
            for (features, model), group in part.groupby(['features', 'model']):
                metrics.append({'crop': crop, 'scope': scope, 'features': features, 'model': model,
                                **scores(group.yield_kg_ha, group.predicted_kg_ha),
                                'label_families': int(group.sample_id.nunique()), 'municipalities': int(group.municipality_code.nunique())})
            for model in ('ridge', 'hgb'):
                a = part[(part.model == model) & (part.features == 'with_soil_moisture')].sort_values(['sample_id', 'horizon_days'])
                b = part[(part.model == model) & (part.features == 'without_soil_moisture')].sort_values(['sample_id', 'horizon_days'])
                delta = a.abs_error.to_numpy() - b.abs_error.to_numpy()
                low, high = cluster_bootstrap_ci(delta, a.municipality_code.to_numpy(), draws=1000)
                effects.append({'crop': crop, 'scope': scope, 'model': model, 'label_families': int(a.sample_id.nunique()),
                                'municipalities': int(a.municipality_code.nunique()),
                                'mae_without_kg_ha': float(b.abs_error.mean()), 'mae_with_kg_ha': float(a.abs_error.mean()),
                                'delta_mae_kg_ha': float(delta.mean()), 'ci95_low_kg_ha': low, 'ci95_high_kg_ha': high,
                                'gain_supported': high < 0})
    write_csv(OUT / 'metrics.csv', metrics)
    write_csv(OUT / 'soil_moisture_effect.csv', effects)
    save_json(OUT / 'manifest.json', {
        'inputs': {name: sha(DATA / name) for name in ('samples.parquet', 'meteomatics_features.parquet')},
        'rows': len(frame), 'label_families': int(frame.sample_id.nunique()), 'municipalities': int(frame.municipality_code.nunique()),
        'states': sorted(frame.state_code.unique()), 'soil_moisture_features': soil_columns, 'hyperparameters': chosen_log,
        'protocol': 'walk-forward, target year Y trained on harvests <= Y-2, identical rows for both feature sets',
        'gain_rule': 'upper bound of the municipality-clustered 95% interval of the MAE difference below zero',
        'availability': 'retrospective reconstruction; forecast issue times not involved'})
    print(pd.DataFrame(effects).round(1).to_string(index=False))


if __name__ == '__main__':
    main()
