"""Small MLP against mean/trend/Ridge/HGB on the frozen v3 cohort.

Same rows, splits and feature contracts as research_v3. Every choice (columns,
imputation, scaling, architecture, L2, epochs) uses training years only, with an
internal temporal split. Validation 2019-2021 is read once per frozen model and the
2022-2024 test stays sealed: its rows do not exist in the input file.
"""
import json

from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv

SOURCE = ROOT / 'data/processed/training_v3/research_samples.csv'
OUT = ROOT / 'data/processed/training_v4'
MODELS = ROOT / 'data/models/research_v4'
VARIANTS = {'weather': ['harvest_year', *WEATHER_FEATURES],
            'weather_hydro': ['harvest_year', *WEATHER_FEATURES, *HYDRO_FEATURES]}
# Frozen before any validation score was read.
GRID = [{'hidden': hidden, 'alpha': alpha} for hidden in ((8,), (16, 8)) for alpha in (0.1, 1.0, 10.0)]
# The residual model was added after the first validation read showed the plain network
# collapsing to the training mean; same grid, same rule, labelled as post hoc in the manifest.
MLP_MODELS = {'mlp_ensemble': False, 'mlp_trend_residual_ensemble': True}
SELECTION_SEEDS = (11, 12, 13)
FINAL_SEEDS = tuple(range(101, 111))
INNER_YEARS = 3
MAX_EPOCHS = 1500
PATIENCE = 100
SEED_WIN_SHARE = 0.8


def check_reserved(frame):
    years = set(int(y) for y in frame.harvest_year)
    if any(y >= 2022 for y in years) or years & {2017, 2018}:
        raise ValueError('Sealed test or embargo years present in the input')


def check_training_isolation(train):
    if train.harvest_year.max() > 2016 or train.spatial_holdout.any():
        raise ValueError('Training isolation failed')


def inner_temporal_split(train, years=INNER_YEARS):
    last = sorted(train.harvest_year.unique())[-years:]
    stop = train[train.harvest_year.isin(last)]
    return train[~train.harvest_year.isin(last)], stop


def _prepare(train, features, trend=False):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    transformer = make_pipeline(SimpleImputer(strategy='median'), StandardScaler())
    x = transformer.fit_transform(train[features].astype(float))
    target = train.yield_kg_ha.to_numpy(float)
    trend_model = None
    if trend:
        # Same contract as the trend baseline; the network only learns the departure from it.
        trend_model = make_pipeline(StandardScaler(), Ridge(alpha=10)).fit(train[['harvest_year']], target)
        target = target - trend_model.predict(train[['harvest_year']])
    y_mean, y_std = float(target.mean()), float(target.std()) or 1.0
    return transformer, x, (target - y_mean) / y_std, y_mean, y_std, trend_model


def _restore(scaled, frame, y_mean, y_std, trend_model):
    values = scaled * y_std + y_mean
    return values + trend_model.predict(frame[['harvest_year']]) if trend_model is not None else values


def _network(config, seed):
    from sklearn.neural_network import MLPRegressor
    return MLPRegressor(hidden_layer_sizes=config['hidden'], alpha=config['alpha'], activation='relu',
                        solver='adam', learning_rate_init=1e-3, batch_size=16, shuffle=True, random_state=seed)


def best_epoch(train, features, config, seed, max_epochs=MAX_EPOCHS, patience=PATIENCE, trend=False):
    """Early stopping on the last training years; returns (epoch, inner MAE in kg/ha)."""
    import numpy as np
    fit, stop = inner_temporal_split(train)
    transformer, x, y, y_mean, y_std, trend_model = _prepare(fit, features, trend)
    x_stop = transformer.transform(stop[features].astype(float))
    truth = stop.yield_kg_ha.to_numpy(float)
    network = _network(config, seed)
    best = (1, float('inf'))
    for epoch in range(1, max_epochs + 1):
        network.partial_fit(x, y)
        score = float(np.abs(_restore(network.predict(x_stop), stop, y_mean, y_std, trend_model) - truth).mean())
        if score < best[1]:
            best = (epoch, score)
        elif epoch - best[0] >= patience:
            break
    return best


def fit_bundle(train, features, config, epochs, seeds=FINAL_SEEDS, trend=False):
    transformer, x, y, y_mean, y_std, trend_model = _prepare(train, features, trend)
    networks = []
    for seed in seeds:
        network = _network(config, seed)
        for _ in range(epochs):
            network.partial_fit(x, y)
        networks.append(network)
    return {'features': list(features), 'transformer': transformer, 'networks': networks, 'seeds': list(seeds),
            'y_mean': y_mean, 'y_std': y_std, 'trend': trend_model, 'config': {'hidden': list(config['hidden']), 'alpha': config['alpha']},
            'epochs': int(epochs)}


def predict_bundle(bundle, frame):
    import numpy as np
    x = bundle['transformer'].transform(frame[bundle['features']].astype(float))
    per_seed = np.array([_restore(n.predict(x), frame, bundle['y_mean'], bundle['y_std'], bundle.get('trend'))
                         for n in bundle['networks']])
    return per_seed, per_seed.mean(axis=0)


def promote(temporal, spatial, seed_win_share):
    """Each pair is (MLP ensemble MAE, best baseline MAE)."""
    return temporal[0] < temporal[1] and spatial[0] < spatial[1] and seed_win_share >= SEED_WIN_SHARE


def cluster_bootstrap_ci(values, groups, draws=2000, seed=7):
    import numpy as np
    values = np.asarray(values, float)
    groups = np.asarray(groups)
    names = sorted(set(groups))
    members = [values[groups == name] for name in names]
    rng = np.random.default_rng(seed)
    means = [np.concatenate([members[i] for i in rng.integers(len(names), size=len(names))]).mean()
             for _ in range(draws)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def scores(truth, pred):
    import numpy as np
    error = np.asarray(pred, float) - np.asarray(truth, float)
    return {'n': int(len(error)), 'mae_kg_ha': float(np.abs(error).mean()),
            'rmse_kg_ha': float(np.sqrt((error ** 2).mean())), 'bias_kg_ha': float(error.mean())}


def select_config(train, features, trend=False):
    import numpy as np
    trials = []
    for config in GRID:
        runs = [best_epoch(train, features, config, seed, trend=trend) for seed in SELECTION_SEEDS]
        trials.append({'hidden': list(config['hidden']), 'alpha': config['alpha'],
                       'inner_mae_kg_ha': float(np.mean([r[1] for r in runs])),
                       'epochs': int(np.median([r[0] for r in runs]))})
    chosen = min(trials, key=lambda t: t['inner_mae_kg_ha'])
    return {'hidden': tuple(chosen['hidden']), 'alpha': chosen['alpha']}, chosen['epochs'], trials


def main():
    import joblib
    import numpy as np
    import pandas as pd
    import sklearn
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    frozen = json.loads((ROOT / 'data/processed/training_v3/delivery_manifest.json').read_text(encoding='utf-8'))
    relative = SOURCE.relative_to(ROOT).as_posix()
    if frozen['files'][relative]['sha256'] != sha(SOURCE):
        raise ValueError('v3 samples differ from the frozen delivery')
    frame = pd.read_csv(SOURCE, dtype={'municipality_code': str, 'state_code': str})
    check_reserved(frame)
    if frame.duplicated(['sample_id', 'horizon_days']).any():
        raise ValueError('Duplicated example keys')
    OUT.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    reference = pd.read_csv(ROOT / 'data/processed/training_v3/model_comparison.csv')

    metrics, by_group, predictions, seed_rows, search, paired, decisions, contracts = [], [], [], [], [], [], [], {}
    scopes = (('temporal_validation', False), ('spatiotemporal_validation', True))

    def record(meta, ev, pred):
        truth = ev.yield_kg_ha.to_numpy(float)
        metrics.append({**meta, **scores(truth, pred)})
        for column in ('state_code', 'municipality_code', 'harvest_year'):
            for value, part in ev.assign(pred=pred).groupby(column):
                by_group.append({**meta, 'group': column, 'value': value, **scores(part.yield_kg_ha, part.pred)})
        for (_, row), value in zip(ev.iterrows(), pred):
            predictions.append({**meta, 'sample_id': row.sample_id, 'municipality_code': row.municipality_code,
                                'harvest_year': row.harvest_year, 'feature_cutoff': row.feature_cutoff,
                                'actual_kg_ha': row.yield_kg_ha, 'predicted_kg_ha': float(value)})

    for crop in ('soy', 'maize2'):
        for horizon in (60, 90):
            cohort = frame[(frame.crop == crop) & (frame.horizon_days == horizon)]
            train = cohort[cohort.split == 'train']
            check_training_isolation(train)
            evaluation = {scope: cohort[(cohort.split == 'validation') & (cohort.spatial_holdout == holdout)]
                          for scope, holdout in scopes}
            base = {'crop': crop, 'horizon_days': horizon, 'train_n': len(train),
                    'train_label_families': train.sample_id.nunique()}
            fitted = {}
            for variant, model_name, candidates in (
                    ('none', 'historical_mean', []), ('trend', 'ridge', ['harvest_year']),
                    *[(v, m, c) for v, c in VARIANTS.items() for m in ('ridge', 'hgb')]):
                features = [f for f in candidates if train[f].nunique(dropna=True) > 1]
                if model_name == 'historical_mean':
                    constant = float(train.yield_kg_ha.mean())
                    predict = lambda ev, c=constant: np.full(len(ev), c)
                else:
                    regressor = Ridge(alpha=10) if model_name == 'ridge' else HistGradientBoostingRegressor(
                        max_iter=100, max_leaf_nodes=7, min_samples_leaf=15, l2_regularization=10,
                        random_state=42, early_stopping=False)
                    pipeline = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), regressor)
                    pipeline.fit(train[features], train.yield_kg_ha)
                    predict = lambda ev, p=pipeline, f=features: p.predict(ev[f])
                for scope, ev in evaluation.items():
                    pred = predict(ev)
                    record({**base, 'variant': variant, 'model': model_name, 'scope': scope}, ev, pred)
                    fitted[(variant, model_name, scope)] = pred
                    if model_name != 'historical_mean':
                        # The baselines must reproduce research_v3 exactly: same rows, same contract.
                        old = reference[(reference.crop == crop) & (reference.horizon_days == horizon) &
                                        (reference.variant == variant) & (reference.model == model_name) &
                                        (reference['scope'] == scope)].mae_kg_ha.item()
                        if abs(old - metrics[-1]['mae_kg_ha']) > 1e-6:
                            raise ValueError(f'Baseline drift against v3: {crop} {horizon} {variant} {model_name}')

            for variant, name, trend in [(v, n, t) for v in VARIANTS for n, t in MLP_MODELS.items()]:
                # The residual network gets the year only through the trend term.
                candidates = [f for f in VARIANTS[variant] if not (trend and f == 'harvest_year')]
                features = [f for f in candidates if train[f].nunique(dropna=True) > 1]
                config, epochs, trials = select_config(train, features, trend)
                search.extend({'crop': crop, 'horizon_days': horizon, 'variant': variant, 'model': name, **t,
                               'selected': t['hidden'] == list(config['hidden']) and t['alpha'] == config['alpha']}
                              for t in trials)
                bundle = fit_bundle(train, features, config, epochs, trend=trend)
                artifact = f'{crop}_d{horizon}_{variant}_{name}.joblib'
                joblib.dump(bundle, MODELS / artifact)
                contracts[artifact] = {'features': features, 'hidden': list(config['hidden']), 'alpha': config['alpha'],
                                       'epochs': epochs, 'seeds': list(FINAL_SEEDS), 'training_end': 2016,
                                       'target': 'yield minus linear trend' if trend else 'yield',
                                       'declared_after_first_validation_read': trend,
                                       'training_samples': list(train.sample_id), 'operational_approved': False}
                for scope, ev in evaluation.items():
                    per_seed, mean = predict_bundle(bundle, ev)
                    meta = {**base, 'variant': variant, 'model': name, 'scope': scope}
                    record(meta, ev, mean)
                    truth = ev.yield_kg_ha.to_numpy(float)
                    seed_mae = np.abs(per_seed - truth).mean(axis=1)
                    ridge = fitted[(variant, 'ridge', scope)]
                    seed_rows.append({**meta, 'seeds': len(seed_mae),
                                      'mae_mean_kg_ha': float(seed_mae.mean()), 'mae_std_kg_ha': float(seed_mae.std(ddof=1)),
                                      'mae_min_kg_ha': float(seed_mae.min()), 'mae_max_kg_ha': float(seed_mae.max()),
                                      'ridge_mae_kg_ha': float(np.abs(ridge - truth).mean()),
                                      'seed_share_better_than_ridge': float((seed_mae < np.abs(ridge - truth).mean()).mean())})
                    delta = np.abs(mean - truth) - np.abs(ridge - truth)
                    low, high = cluster_bootstrap_ci(delta, ev.municipality_code.to_numpy())
                    paired.append({**meta, 'against': f'{variant}_ridge', 'n': len(delta),
                                   'clusters': ev.municipality_code.nunique(),
                                   'mean_abs_error_delta_kg_ha': float(delta.mean()),
                                   'ci95_low_kg_ha': low, 'ci95_high_kg_ha': high,
                                   'examples_mlp_better': int((delta < 0).sum())})
                    fitted[(variant, name, 'seed_mae', scope)] = seed_mae

            # Promotion rule frozen in promote(): the strongest baseline per scope is the bar.
            current = [m for m in metrics if m['crop'] == crop and m['horizon_days'] == horizon]
            for variant, name in [(v, n) for v in VARIANTS for n in MLP_MODELS]:
                pairs, share = {}, None
                for scope, _ in scopes:
                    rows = [m for m in current if m['scope'] == scope]
                    mlp = next(m for m in rows if m['model'] == name and m['variant'] == variant)
                    best = min((m for m in rows if m['model'] not in MLP_MODELS), key=lambda m: m['mae_kg_ha'])
                    pairs[scope] = (mlp['mae_kg_ha'], best['mae_kg_ha'], f"{best['variant']}_{best['model']}")
                    if scope == 'temporal_validation':
                        share = float((fitted[(variant, name, 'seed_mae', scope)] < best['mae_kg_ha']).mean())
                t, s = pairs['temporal_validation'], pairs['spatiotemporal_validation']
                decisions.append({'crop': crop, 'horizon_days': horizon, 'variant': variant, 'model': name,
                                  'mlp_temporal_mae_kg_ha': t[0], 'best_baseline_temporal': t[2],
                                  'best_baseline_temporal_mae_kg_ha': t[1], 'mlp_spatiotemporal_mae_kg_ha': s[0],
                                  'best_baseline_spatiotemporal': s[2], 'best_baseline_spatiotemporal_mae_kg_ha': s[1],
                                  'seed_share_better_than_best_temporal': share,
                                  'mlp_promoted': promote(t[:2], s[:2], share)})
            print(f'{crop} d{horizon} done', flush=True)

    write_csv(OUT / 'model_comparison.csv', metrics)
    write_csv(OUT / 'model_comparison_by_group.csv', by_group)
    write_csv(OUT / 'validation_predictions.csv', predictions)
    write_csv(OUT / 'mlp_seed_variability.csv', seed_rows)
    write_csv(OUT / 'mlp_inner_search.csv', search)
    write_csv(OUT / 'paired_mlp_vs_ridge.csv', paired)
    write_csv(OUT / 'promotion_decisions.csv', decisions)

    # One row per example, with the quality evidence required by the data contract.
    audit = pd.read_csv(ROOT / 'data/processed/training_v3/cutoff_audit.csv')
    examples = frame.merge(audit, on=['sample_id', 'horizon_days'], validate='one_to_one')
    examples['label_family_id'] = examples.sample_id
    examples['rain_variant'] = 'power_v1_point_retrospective'
    examples.to_parquet(OUT / 'examples.parquet', index=False)
    units = {'rain_sum_mm': 'mm', 'dry_days': 'days', 'max_dry_spell': 'days', 't_min_mean_c': 'degC',
             't_max_mean_c': 'degC', 'heat_days': 'days', 'radiation_sum_mj_m2': 'MJ/m2', 'rh_mean_pct': '%',
             'wind_mean_m_s': 'm/s', 'harvest_year': 'year', 'hydro_storage_end_mm': 'mm'}
    suffix = {'observed_days': 'days', 'deficit_mm': 'mm', 'eta_etc_ratio': 'ratio', 'stress_days': 'days'}
    dictionary = [{'feature': f, 'unit': units.get(f) or next(u for s, u in suffix.items() if f.endswith(s)),
                   'group': 'trend' if f == 'harvest_year' else ('hydro' if f.startswith('hydro_') else 'weather'),
                   'window': 'sowing to cutoff inclusive', 'missing_share': float(frame[f].isna().mean()),
                   'missing_meaning': 'phase not started at cutoff' if frame[f].isna().any() else ''}
                  for f in VARIANTS['weather_hydro']]
    write_csv(OUT / 'feature_dictionary.csv', dictionary)
    quality = {'rows': len(examples), 'label_families': int(examples.label_family_id.nunique()),
               'duplicate_example_keys': int(examples.duplicated(['sample_id', 'horizon_days']).sum()),
               'post_cutoff_rows_used': int(examples.post_cutoff_rows_used.sum()),
               'max_observation_after_cutoff': int((examples.max_observation_date > examples.feature_cutoff).sum()),
               'incomplete_windows': int((examples.observed_days != examples.horizon_days).sum()),
               'rows_by_split': {k: int(v) for k, v in examples.split.value_counts().items()},
               'test_rows_2022_2024': int((examples.harvest_year >= 2022).sum()),
               'operational_eligible_rows': int(examples.operational_eligible.sum()),
               'availability_status': 'retrospective_only_vintages_unverified'}
    save_json(OUT / 'quality_report.json', quality)
    if quality['duplicate_example_keys'] or quality['post_cutoff_rows_used'] or quality['max_observation_after_cutoff'] \
            or quality['test_rows_2022_2024']:
        raise ValueError('Quality gate failed')

    save_json(MODELS / 'manifest.json', {
        'version': 'research_v4_mlp', 'inputs': {relative: sha(SOURCE)}, 'models_by_artifact': contracts,
        'models': {p.name: sha(p) for p in sorted(MODELS.glob('*.joblib'))},
        'bundle_format': 'joblib dict: features, transformer (median imputer + standard scaler), networks, y_mean, y_std',
        'versions': {'sklearn': sklearn.__version__, 'numpy': np.__version__, 'pandas': pd.__version__},
        'search': {'grid': [{'hidden': list(g['hidden']), 'alpha': g['alpha']} for g in GRID],
                   'selection_seeds': list(SELECTION_SEEDS), 'inner_validation': f'last {INNER_YEARS} training years',
                   'max_epochs': MAX_EPOCHS, 'patience': PATIENCE, 'refit': 'all training years, selected epochs'},
        'promotion_rule': f'ensemble MAE below the best baseline in both scopes and >= {SEED_WIN_SHARE:.0%} of seeds '
                          'below it in temporal validation',
        'training_years': [2002, 2016], 'embargo_years': [2017, 2018], 'validation_years': [2019, 2020, 2021],
        'test_years': [2022, 2023, 2024], 'test_evaluated': False, 'spatial_holdout': ['3170107', '5107909'],
        'operational_approved': False, 'publication_time_leakage_resolved': False,
        'limitations': ['Validation 2019-2021 was already inspected in earlier rounds; it is not an untouched test',
                        'Reprocessed POWER/IMERG Final, not historical release vintages',
                        'Municipal yield, hypothetical sowing dates, fixed AD4/70% soil water']})
    print(pd.DataFrame(decisions).to_string(index=False))


if __name__ == '__main__':
    main()
