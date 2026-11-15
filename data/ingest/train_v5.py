"""Yield models on the expanded MG/MT cohort, with a cumulative source ablation.

Ridge, gradient boosting and an MLP are fitted on the same rows. Every choice
(columns, imputation, scaling, hyperparameters, network size, epochs) is made on
training harvests with an inner temporal split. Validation 2019-2021 is only scored.
`--test` scores the frozen champions once on 2022-2024 and needs the sealed test table.
"""
import argparse
import json

from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES
from data.ingest.train_mlp_v4 import cluster_bootstrap_ci, scores
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv

DATA = ROOT / 'data/processed/training_v5'
MODELS = ROOT / 'data/models/research_v5'
# Cumulative groups: each one adds a source to the previous.
GROUPS = [('trend', ['harvest_year']),
          ('history', ['yield_lag2to4_mean_kg_ha', 'yield_lag_count']),
          ('weather', WEATHER_FEATURES),
          ('hydro', HYDRO_FEATURES),
          ('site', ['latitude', 'longitude', 'elevation_m'])]
# Optional sources, joined when their table exists: (group, file, join keys).
EXTRA = [('soil', 'soil_features.parquet', ['municipality_code']),
         ('vegetation', 'vegetation_features.parquet', ['sample_id', 'horizon_days'])]
INNER_YEARS = 3
RIDGE_GRID = (1.0, 10.0, 100.0)
HGB_GRID = ({'max_iter': 100, 'max_leaf_nodes': 7, 'min_samples_leaf': 15, 'l2_regularization': 10.0, 'learning_rate': 0.1},
            {'max_iter': 300, 'max_leaf_nodes': 15, 'min_samples_leaf': 20, 'l2_regularization': 1.0, 'learning_rate': 0.05})
# Capacity grows only if the inner temporal validation asks for it.
MLP_GRID = [{'hidden': hidden, 'weight_decay': decay, 'dropout': dropout}
            for hidden in ((16, 8), (64, 32), (128, 64, 32)) for decay in (1e-4, 1e-2) for dropout in (0.0, 0.2)]
SELECTION_SEEDS = (11, 12)
FINAL_SEEDS = (101, 102, 103, 104, 105)
MAX_EPOCHS = 300
PATIENCE = 30
SEED_WIN_SHARE = 0.8
SCOPES = {'temporal_validation': lambda f: ~f.spatial_holdout,
          'spatiotemporal_validation': lambda f: f.spatial_holdout,
          'mvp_temporal_validation': lambda f: f.mvp & ~f.spatial_holdout,
          'mvp_spatiotemporal_validation': lambda f: f.mvp & f.spatial_holdout}


def load_frame(test=False):
    import pandas as pd
    frame = pd.read_parquet(DATA / ('test_samples.parquet' if test else 'samples.parquet'))
    groups = list(GROUPS)
    for name, filename, keys in EXTRA:
        path = DATA / filename
        if path.exists():
            extra = pd.read_parquet(path)
            columns = [c for c in extra.columns if c not in keys]
            frame = frame.merge(extra, on=keys, how='left', validate='many_to_one')
            groups.append((name, columns))
    return frame, groups


def inner_split(train, years=INNER_YEARS):
    last = sorted(train.harvest_year.unique())[-years:]
    stop = train.harvest_year.isin(last)
    return train[~stop], train[stop]


def usable(train, candidates):
    return [f for f in candidates if train[f].nunique(dropna=True) > 1]


def make_transformer():
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(SimpleImputer(strategy='median'), StandardScaler())


def fit_sklearn(kind, params, train, features):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    regressor = Ridge(alpha=params) if kind == 'ridge' else HistGradientBoostingRegressor(
        random_state=42, early_stopping=False, **params)
    return make_pipeline(make_transformer(), regressor).fit(train[features].astype(float), train.yield_kg_ha)


def select_sklearn(kind, train, features):
    import numpy as np
    fit, stop = inner_split(train)
    grid = RIDGE_GRID if kind == 'ridge' else HGB_GRID
    errors = [float(np.abs(fit_sklearn(kind, p, fit, features).predict(stop[features].astype(float)) - stop.yield_kg_ha).mean())
              for p in grid]
    return grid[int(np.argmin(errors))], min(errors)


def trend_model(train):
    from sklearn.linear_model import LinearRegression
    return LinearRegression().fit(train[['harvest_year']], train.yield_kg_ha)


class TorchMLP:
    """Seed ensemble of small networks on standardized inputs; optional linear-trend residual target."""

    def __init__(self, config, features, residual):
        self.config, self.features, self.residual = dict(config), list(features), residual

    def _target(self, frame):
        y = frame.yield_kg_ha.to_numpy(float)
        return y - self.trend.predict(frame[['harvest_year']]) if self.residual else y

    def _prepare(self, train):
        import torch
        self.trend = trend_model(train) if self.residual else None
        self.transformer = make_transformer().fit(train[self.features].astype(float))
        target = self._target(train)
        self.y_mean, self.y_std = float(target.mean()), float(target.std()) or 1.0
        x = torch.tensor(self.transformer.transform(train[self.features].astype(float)), dtype=torch.float32)
        return x, torch.tensor((target - self.y_mean) / self.y_std, dtype=torch.float32)

    def _network(self, width, seed):
        import torch
        torch.manual_seed(seed)
        layers, previous = [], width
        for size in self.config['hidden']:
            layers += [torch.nn.Linear(previous, size), torch.nn.ReLU()]
            if self.config['dropout']:
                layers.append(torch.nn.Dropout(self.config['dropout']))
            previous = size
        return torch.nn.Sequential(*layers, torch.nn.Linear(previous, 1))

    def _epoch(self, network, optimizer, x, y, generator):
        import torch
        network.train()
        order = torch.randperm(len(x), generator=generator)
        for start in range(0, len(x), 128):
            batch = order[start:start + 128]
            optimizer.zero_grad()
            torch.nn.functional.mse_loss(network(x[batch]).squeeze(1), y[batch]).backward()
            optimizer.step()

    def _raw(self, network, frame):
        import torch
        network.eval()
        x = torch.tensor(self.transformer.transform(frame[self.features].astype(float)), dtype=torch.float32)
        with torch.no_grad():
            values = network(x).squeeze(1).numpy() * self.y_std + self.y_mean
        return values + self.trend.predict(frame[['harvest_year']]) if self.residual else values

    def best_epoch(self, train, seed, max_epochs=MAX_EPOCHS, patience=PATIENCE):
        import numpy as np
        import torch
        fit, stop = inner_split(train)
        x, y = self._prepare(fit)
        network = self._network(x.shape[1], seed)
        optimizer = torch.optim.AdamW(network.parameters(), lr=1e-3, weight_decay=self.config['weight_decay'])
        generator = torch.Generator().manual_seed(seed)
        truth, best = stop.yield_kg_ha.to_numpy(float), (1, float('inf'))
        for epoch in range(1, max_epochs + 1):
            self._epoch(network, optimizer, x, y, generator)
            score = float(np.abs(self._raw(network, stop) - truth).mean())
            if score < best[1]:
                best = (epoch, score)
            elif epoch - best[0] >= patience:
                break
        return best

    def fit(self, train, epochs, seeds=FINAL_SEEDS):
        import torch
        x, y = self._prepare(train)
        self.networks, self.epochs, self.seeds = [], int(epochs), list(seeds)
        for seed in seeds:
            network = self._network(x.shape[1], seed)
            optimizer = torch.optim.AdamW(network.parameters(), lr=1e-3, weight_decay=self.config['weight_decay'])
            generator = torch.Generator().manual_seed(seed)
            for _ in range(self.epochs):
                self._epoch(network, optimizer, x, y, generator)
            self.networks.append(network)
        return self

    def predict_seeds(self, frame):
        import numpy as np
        return np.array([self._raw(network, frame) for network in self.networks])

    def predict(self, frame):
        return self.predict_seeds(frame).mean(axis=0)


def select_mlp(train, features, residual):
    import numpy as np
    trials = []
    for config in MLP_GRID:
        runs = [TorchMLP(config, features, residual).best_epoch(train, seed) for seed in SELECTION_SEEDS]
        trials.append({**config, 'hidden': list(config['hidden']), 'inner_mae_kg_ha': float(np.mean([r[1] for r in runs])),
                       'epochs': int(np.median([r[0] for r in runs]))})
    best = min(trials, key=lambda t: t['inner_mae_kg_ha'])
    config = {'hidden': tuple(best['hidden']), 'weight_decay': best['weight_decay'], 'dropout': best['dropout']}
    return config, best['epochs'], trials


def choose_champion(rows):
    """Frozen rule: lowest temporal-validation MAE; an MLP needs to win both cohort scopes with stable seeds."""
    def mae(row, scope):
        return row['mae'][scope]
    baselines = [r for r in rows if not r['model'].startswith('mlp')]
    best = min(baselines, key=lambda r: mae(r, 'temporal_validation'))
    for row in sorted((r for r in rows if r['model'].startswith('mlp')), key=lambda r: mae(r, 'temporal_validation')):
        if mae(row, 'temporal_validation') < mae(best, 'temporal_validation') \
                and mae(row, 'spatiotemporal_validation') < mae(best, 'spatiotemporal_validation') \
                and row['seed_win_share'] >= SEED_WIN_SHARE:
            return row
    return best


def train_all():
    import joblib
    import numpy as np
    import torch
    torch.set_num_threads(4)
    frame, groups = load_frame()
    if (frame.harvest_year >= 2022).any():
        raise ValueError('Sealed test years present in the training table')
    MODELS.mkdir(parents=True, exist_ok=True)
    metrics, by_state, predictions, search, seed_rows, champions, contracts = [], [], [], [], [], [], {}
    for crop in ('soy', 'maize2'):
        for horizon in (60, 90):
            cohort = frame[(frame.crop == crop) & (frame.horizon_days == horizon)]
            train = cohort[cohort.split == 'train']
            validation = cohort[cohort.split == 'validation']
            if train.harvest_year.max() > 2016 or train.spatial_holdout.any() or validation.harvest_year.min() < 2019:
                raise ValueError('Split isolation failed')
            evaluation = {scope: validation[mask(validation)] for scope, mask in SCOPES.items()}
            base = {'crop': crop, 'horizon_days': horizon, 'train_label_families': int(train.sample_id.nunique()),
                    'train_municipalities': int(train.municipality_code.nunique())}
            candidates, summary, fitted = [], [], {}

            def record(group, model_name, predict, extra=None):
                maes = {}
                for scope, ev in evaluation.items():
                    pred = predict(ev)
                    row = {**base, 'sources': group, 'model': model_name, 'scope': scope, **scores(ev.yield_kg_ha, pred),
                           'municipalities': int(ev.municipality_code.nunique())}
                    metrics.append(row)
                    maes[scope] = row['mae_kg_ha']
                    fitted[(group, model_name, scope)] = pred
                    for state, part in ev.assign(pred=pred).groupby('state_code'):
                        by_state.append({**base, 'sources': group, 'model': model_name, 'scope': scope, 'state_code': state,
                                         **scores(part.yield_kg_ha, part.pred)})
                    if scope.startswith('mvp'):
                        for (_, r), value in zip(ev.iterrows(), pred):
                            predictions.append({**base, 'sources': group, 'model': model_name, 'scope': scope,
                                                'sample_id': r.sample_id, 'municipality_code': r.municipality_code,
                                                'harvest_year': r.harvest_year, 'feature_cutoff': r.feature_cutoff,
                                                'actual_kg_ha': r.yield_kg_ha, 'predicted_kg_ha': float(value)})
                summary.append({'sources': group, 'model': model_name, 'mae': maes, 'seed_win_share': 1.0, **(extra or {})})

            constant = float(train.yield_kg_ha.mean())
            record('none', 'historical_mean', lambda ev: np.full(len(ev), constant))
            for index, (group, _) in enumerate(groups):
                candidates = [f for _, columns in groups[:index + 1] for f in columns]
                features = usable(train, candidates)
                for kind in ('ridge', 'hgb'):
                    if group == 'trend' and kind == 'hgb':
                        continue
                    params, inner = select_sklearn(kind, train, features)
                    model = fit_sklearn(kind, params, train, features)
                    artifact = f'{crop}_d{horizon}_{group}_{kind}.joblib'
                    joblib.dump(model, MODELS / artifact)
                    contracts[artifact] = {'features': features, 'params': params, 'inner_mae_kg_ha': inner}
                    record(group, kind, lambda ev, m=model, f=features: m.predict(ev[f].astype(float)), {'artifact': artifact})
                if group == 'trend':
                    continue
                for name, residual in (('mlp', False), ('mlp_trend_residual', True)):
                    used = [f for f in features if not (residual and f == 'harvest_year')]
                    config, epochs, trials = select_mlp(train, used, residual)
                    search.extend({**base, 'sources': group, 'model': name, **t,
                                   'selected': t['hidden'] == list(config['hidden']) and t['weight_decay'] == config['weight_decay']
                                   and t['dropout'] == config['dropout']} for t in trials)
                    model = TorchMLP(config, used, residual).fit(train, epochs)
                    artifact = f'{crop}_d{horizon}_{group}_{name}.joblib'
                    joblib.dump(model, MODELS / artifact)
                    contracts[artifact] = {'features': used, 'params': {**config, 'hidden': list(config['hidden'])},
                                           'epochs': epochs, 'seeds': list(FINAL_SEEDS)}
                    record(group, name, model.predict, {'artifact': artifact})
                    ev = evaluation['temporal_validation']
                    seed_mae = np.abs(model.predict_seeds(ev) - ev.yield_kg_ha.to_numpy(float)).mean(axis=1)
                    summary[-1]['seed_mae'] = seed_mae
                    seed_rows.append({**base, 'sources': group, 'model': name, 'scope': 'temporal_validation',
                                      'seeds': len(seed_mae), 'mae_mean_kg_ha': float(seed_mae.mean()),
                                      'mae_std_kg_ha': float(seed_mae.std(ddof=1)), 'mae_min_kg_ha': float(seed_mae.min()),
                                      'mae_max_kg_ha': float(seed_mae.max())})
                print(f'{crop} d{horizon} +{group} done', flush=True)

            best_baseline = min((s for s in summary if not s['model'].startswith('mlp')),
                                key=lambda s: s['mae']['temporal_validation'])
            for row in summary:
                if 'seed_mae' in row:
                    row['seed_win_share'] = float((row['seed_mae'] < best_baseline['mae']['temporal_validation']).mean())
            champion = choose_champion(summary)
            best_mlp = min((s for s in summary if s['model'].startswith('mlp')), key=lambda s: s['mae']['temporal_validation'])
            paired = {}
            for scope, ev in evaluation.items():
                truth = ev.yield_kg_ha.to_numpy(float)
                delta = np.abs(fitted[(best_mlp['sources'], best_mlp['model'], scope)] - truth) \
                    - np.abs(fitted[(best_baseline['sources'], best_baseline['model'], scope)] - truth)
                low, high = cluster_bootstrap_ci(delta, ev.municipality_code.to_numpy())
                paired[scope] = {'delta_mae_kg_ha': float(delta.mean()), 'ci95_low': low, 'ci95_high': high,
                                 'clusters': int(ev.municipality_code.nunique())}
            champions.append({**base, 'champion_sources': champion['sources'], 'champion_model': champion['model'],
                              'champion_artifact': champion.get('artifact'),
                              **{f'champion_{scope}_mae_kg_ha': champion['mae'][scope] for scope in SCOPES},
                              'best_baseline': f"{best_baseline['sources']}+{best_baseline['model']}",
                              'best_mlp': f"{best_mlp['sources']}+{best_mlp['model']}",
                              'best_mlp_temporal_mae_kg_ha': best_mlp['mae']['temporal_validation'],
                              'best_mlp_spatiotemporal_mae_kg_ha': best_mlp['mae']['spatiotemporal_validation'],
                              'best_mlp_seed_win_share': best_mlp['seed_win_share'],
                              'mlp_minus_baseline_temporal_kg_ha': paired['temporal_validation']['delta_mae_kg_ha'],
                              'mlp_minus_baseline_temporal_ci95': [paired['temporal_validation']['ci95_low'],
                                                                   paired['temporal_validation']['ci95_high']],
                              'mlp_minus_baseline_spatiotemporal_kg_ha': paired['spatiotemporal_validation']['delta_mae_kg_ha'],
                              'mlp_minus_baseline_spatiotemporal_ci95': [paired['spatiotemporal_validation']['ci95_low'],
                                                                         paired['spatiotemporal_validation']['ci95_high']]})
    write_csv(DATA / 'model_comparison.csv', metrics)
    write_csv(DATA / 'model_comparison_by_state.csv', by_state)
    write_csv(DATA / 'mvp_validation_predictions.csv', predictions)
    write_csv(DATA / 'mlp_inner_search.csv', search)
    write_csv(DATA / 'mlp_seed_variability.csv', seed_rows)
    save_json(DATA / 'champions.json', champions)
    save_json(MODELS / 'manifest.json', {
        'version': 'research_v5_expanded_cohort', 'inputs': {'data/processed/training_v5/samples.parquet': sha(DATA / 'samples.parquet')},
        'source_groups_cumulative': [{'group': g, 'features': c} for g, c in groups],
        'artifacts': {name: {**contract, 'sha256': sha(MODELS / name)} for name, contract in contracts.items()},
        'grids': {'ridge_alpha': list(RIDGE_GRID), 'hgb': list(HGB_GRID),
                  'mlp': [{**g, 'hidden': list(g['hidden'])} for g in MLP_GRID]},
        'selection': f'inner temporal split: last {INNER_YEARS} training years', 'champion_rule': choose_champion.__doc__,
        'training_years': [2002, 2016], 'embargo_years': [2017, 2018], 'validation_years': [2019, 2021],
        'test_years': [2022, 2024], 'test_evaluated': False, 'operational_approved': False,
        'availability': 'retrospective; input and label release vintages unverified'})
    for row in champions:
        print(row['crop'], row['horizon_days'], row['champion_sources'], row['champion_model'],
              round(row['champion_temporal_validation_mae_kg_ha'], 1), round(row['champion_spatiotemporal_validation_mae_kg_ha'], 1),
              '| mvp', round(row['champion_mvp_temporal_validation_mae_kg_ha'], 1),
              round(row['champion_mvp_spatiotemporal_validation_mae_kg_ha'], 1), '| best mlp', row['best_mlp'],
              round(row['best_mlp_temporal_mae_kg_ha'], 1))


def evaluate_test():
    """One-shot scoring of the frozen champions on 2022-2024."""
    import joblib
    manifest_path = MODELS / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['test_evaluated']:
        raise SystemExit('The sealed test was already scored once; refusing to score it again.')
    champions = json.loads((DATA / 'champions.json').read_text(encoding='utf-8'))
    frame, _ = load_frame(test=True)
    if frame.harvest_year.min() < 2022:
        raise ValueError('Test table must hold only 2022-2024 harvests')
    rows, predictions = [], []
    for champion in champions:
        cohort = frame[(frame.crop == champion['crop']) & (frame.horizon_days == champion['horizon_days'])]
        artifact = champion['champion_artifact']
        features = manifest['artifacts'][artifact]['features']
        model = joblib.load(MODELS / artifact)
        for scope, mask in SCOPES.items():
            ev = cohort[mask(cohort)]
            pred = model.predict(ev) if champion['champion_model'].startswith('mlp') else model.predict(ev[features].astype(float))
            rows.append({'crop': champion['crop'], 'horizon_days': champion['horizon_days'],
                         'sources': champion['champion_sources'], 'model': champion['champion_model'],
                         'scope': scope.replace('validation', 'test'), **scores(ev.yield_kg_ha, pred),
                         'municipalities': int(ev.municipality_code.nunique())})
            if scope.startswith('mvp'):
                for (_, r), value in zip(ev.iterrows(), pred):
                    predictions.append({'sample_id': r.sample_id, 'horizon_days': r.horizon_days, 'municipality_code': r.municipality_code,
                                        'crop': r.crop, 'harvest_year': r.harvest_year, 'feature_cutoff': r.feature_cutoff,
                                        'actual_kg_ha': r.yield_kg_ha, 'predicted_kg_ha': float(value)})
    write_csv(DATA / 'test_metrics.csv', rows)
    write_csv(DATA / 'mvp_test_predictions.csv', predictions)
    manifest['test_evaluated'] = True
    manifest['test_input_sha256'] = sha(DATA / 'test_samples.parquet')
    save_json(manifest_path, manifest)
    for row in rows:
        print(row['crop'], row['horizon_days'], row['sources'], row['model'], row['scope'], row['n'], round(row['mae_kg_ha'], 1),
              round(row['bias_kg_ha'], 1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Score the frozen champions once on the sealed test')
    args = parser.parse_args()
    # Import by name so pickled classes resolve to data.ingest.train_v5, not __main__.
    from data.ingest.train_v5 import evaluate_test as score, train_all as fit
    score() if args.test else fit()
