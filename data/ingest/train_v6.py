"""Horizon-conditional yield models with walk-forward validation.

For each target harvest year Y the models are fitted on harvests up to Y-2, the lag with
which PAM labels are assumed to be published. A no-weather baseline (trend plus lagged
municipal yield) is fitted first; Ridge, gradient boosting and one MLP per crop then learn
the departure from it. The MLP takes the cutoff horizon as an input, so one network serves
every horizon the front end may ask for. Hyperparameters are chosen once, on the first
fold's training years only. `--test` scores the frozen choices once on 2022-2024.
"""
import argparse
import json

from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES
from data.ingest.train_mlp_v4 import cluster_bootstrap_ci, scores
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv

DATA = ROOT / 'data/processed/training_v6'
MODELS = ROOT / 'data/models/research_v6'
LABEL_LAG = 2
VALIDATION_YEARS = (2019, 2020, 2021)
TEST_YEARS = (2022, 2023, 2024)
BASE = ['harvest_year', 'yield_lag2to4_mean_kg_ha', 'yield_lag_count']
SITE = ['latitude', 'longitude', 'elevation_m']
# Cumulative sources seen by the residual models, on top of the baseline context.
SOURCES = [('weather', WEATHER_FEATURES), ('hydro', HYDRO_FEATURES), ('site', SITE)]
EXTRA = []  # optional sources are compared on identical samples in meteomatics_ablation
INNER_YEARS = 3
RIDGE_GRID = (1.0, 10.0, 100.0, 1000.0)
HGB_GRID = ({'max_iter': 200, 'max_leaf_nodes': 7, 'min_samples_leaf': 40, 'l2_regularization': 10.0, 'learning_rate': 0.05},
            {'max_iter': 400, 'max_leaf_nodes': 15, 'min_samples_leaf': 40, 'l2_regularization': 1.0, 'learning_rate': 0.05})
MLP_GRID = [{'hidden': hidden, 'dropout': dropout} for hidden in ((32, 16), (64, 32), (128, 64, 32), (256, 128, 64))
            for dropout in (0.0, 0.2)]
MAX_EPOCHS = 80
PATIENCE = 8
FINAL_SEEDS = (101, 102, 103, 104, 105)
SEED_WIN_SHARE = 0.8
SCOPES = {'temporal': lambda f: ~f.spatial_holdout,
          'spatiotemporal': lambda f: f.spatial_holdout,
          'mg_mt_temporal': lambda f: ~f.spatial_holdout & f.state_code.isin(['31', '51']),
          'mvp_temporal': lambda f: f.mvp & ~f.spatial_holdout,
          'mvp_spatiotemporal': lambda f: f.mvp & f.spatial_holdout}


def load(test=False):
    import pandas as pd
    frame = pd.read_parquet(DATA / 'samples.parquet')
    if (frame.harvest_year > 2021).any():
        raise ValueError('Sealed test years present in the validation table')
    if test:
        sealed = pd.read_parquet(DATA / 'test_samples.parquet')
        if sealed.harvest_year.min() < 2022:
            raise ValueError('Test table must hold only 2022-2024 harvests')
        frame = pd.concat([frame, sealed], ignore_index=True)
    sources = list(SOURCES)
    for name, filename, keys in EXTRA:
        path = DATA / filename
        if path.exists():
            extra = pd.read_parquet(path)
            frame = frame.merge(extra, on=keys, how='left', validate='many_to_one')
            sources.append((name, [c for c in extra.columns if c not in keys]))
    return frame, sources


def fold(frame, year):
    """Training rows for a target year: published labels only, held-out municipalities excluded."""
    train = frame[(frame.harvest_year <= year - LABEL_LAG) & ~frame.spatial_holdout]
    return train, frame[frame.harvest_year == year]


def inner_split(train, years=INNER_YEARS):
    last = sorted(train.harvest_year.unique())[-years:]
    stop = train.harvest_year.isin(last)
    return train[~stop], train[stop]


def usable(train, candidates):
    return [f for f in candidates if train[f].nunique(dropna=True) > 1]


def transformer():
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(SimpleImputer(strategy='median'), StandardScaler())


class Baseline:
    """Trend plus lagged municipal yield; identical for every horizon of a harvest."""

    def __init__(self, alpha=10.0):
        self.alpha = alpha

    def fit(self, train):
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        families = train.drop_duplicates('sample_id')
        self.features = usable(families, BASE)
        self.model = make_pipeline(transformer(), Ridge(alpha=self.alpha)).fit(families[self.features].astype(float), families.yield_kg_ha)
        return self

    def predict(self, frame):
        return self.model.predict(frame[self.features].astype(float))


class PerHorizon:
    """Ridge or HGB on the baseline residual, one model per cutoff horizon."""

    def __init__(self, kind, params, features):
        self.kind, self.params, self.candidates = kind, params, list(features)

    def fit(self, train, residual):
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        self.models = {}
        for horizon, part in train.groupby('horizon_days'):
            features = usable(part, self.candidates)
            regressor = Ridge(alpha=self.params) if self.kind == 'ridge' else HistGradientBoostingRegressor(
                random_state=42, early_stopping=False, loss='absolute_error', **self.params)
            model = make_pipeline(transformer(), regressor).fit(part[features].astype(float), residual[part.index])
            self.models[horizon] = (features, model)
        return self

    def predict(self, frame):
        import pandas as pd
        out = pd.Series(0.0, index=frame.index)
        for horizon, part in frame.groupby('horizon_days'):
            features, model = self.models[horizon]
            out[part.index] = model.predict(part[features].astype(float))
        return out.to_numpy()


class PooledMLP:
    """One network for all horizons; the horizon is an input and seeds are averaged."""

    def __init__(self, config, features):
        self.config, self.candidates = dict(config), [*features, 'horizon_days']

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

    def _tensors(self, train, residual):
        import torch
        self.features = usable(train, self.candidates)
        self.transformer = transformer().fit(train[self.features].astype(float))
        values = residual[train.index].to_numpy(float)
        self.scale = float(values.std()) or 1.0
        return self._x(train), torch.tensor(values / self.scale, dtype=torch.float32)

    def _x(self, frame):
        import torch
        return torch.tensor(self.transformer.transform(frame[self.features].astype(float)), dtype=torch.float32)

    def _run_epoch(self, network, optimizer, x, y, generator):
        import torch
        network.train()
        order = torch.randperm(len(x), generator=generator)
        for start in range(0, len(x), 512):
            batch = order[start:start + 512]
            optimizer.zero_grad()
            torch.nn.functional.smooth_l1_loss(network(x[batch]).squeeze(1), y[batch]).backward()
            optimizer.step()

    def _raw(self, network, x):
        import torch
        network.eval()
        with torch.no_grad():
            return network(x).squeeze(1).numpy() * self.scale

    def best_epoch(self, fit, stop, residual, seed=11, max_epochs=MAX_EPOCHS, patience=PATIENCE):
        import numpy as np
        import torch
        x, y = self._tensors(fit, residual)
        x_stop, truth = self._x(stop), residual[stop.index].to_numpy(float)
        network = self._network(x.shape[1], seed)
        optimizer = torch.optim.AdamW(network.parameters(), lr=1e-3, weight_decay=1e-4)
        generator, best = torch.Generator().manual_seed(seed), (1, float('inf'))
        for epoch in range(1, max_epochs + 1):
            self._run_epoch(network, optimizer, x, y, generator)
            score = float(np.abs(self._raw(network, x_stop) - truth).mean())
            if score < best[1]:
                best = (epoch, score)
            elif epoch - best[0] >= patience:
                break
        return best

    def fit(self, train, residual, epochs, seeds=FINAL_SEEDS):
        import torch
        x, y = self._tensors(train, residual)
        self.networks, self.epochs = [], int(epochs)
        for seed in seeds:
            network = self._network(x.shape[1], seed)
            optimizer = torch.optim.AdamW(network.parameters(), lr=1e-3, weight_decay=1e-4)
            generator = torch.Generator().manual_seed(seed)
            for _ in range(self.epochs):
                self._run_epoch(network, optimizer, x, y, generator)
            self.networks.append(network)
        return self

    def predict_seeds(self, frame):
        import numpy as np
        x = self._x(frame)
        return np.array([self._raw(network, x) for network in self.networks])

    def predict(self, frame):
        return self.predict_seeds(frame).mean(axis=0)


def select_hyperparameters(train, feature_sets):
    """All choices on the first fold's training years, with an inner temporal split."""
    import numpy as np
    import pandas as pd
    fit, stop = inner_split(train)
    base = Baseline().fit(fit)
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - base.predict(train), index=train.index)
    truth = residual[stop.index].to_numpy(float)
    chosen, trials = {}, []
    for group, features in feature_sets.items():
        for kind, grid in (('ridge', RIDGE_GRID), ('hgb', HGB_GRID)):
            errors = [float(np.abs(PerHorizon(kind, p, features).fit(fit, residual).predict(stop) - truth).mean()) for p in grid]
            chosen[(group, kind)] = grid[int(np.argmin(errors))]
            trials.extend({'sources': group, 'model': kind, 'params': json.dumps(p), 'inner_mae_kg_ha': e} for p, e in zip(grid, errors))
        runs = [(config, PooledMLP(config, features).best_epoch(fit, stop, residual)) for config in MLP_GRID]
        config, (epochs, _) = min(runs, key=lambda r: r[1][1])
        chosen[(group, 'mlp')] = {'config': config, 'epochs': epochs}
        trials.extend({'sources': group, 'model': 'mlp', 'params': json.dumps({**c, 'hidden': list(c['hidden']), 'epochs': e}),
                       'inner_mae_kg_ha': s} for c, (e, s) in runs)
        print(f'  selected {group}: mlp {config} epochs {epochs}', flush=True)
    baseline_inner = float(np.abs(truth).mean())
    trials.append({'sources': 'history', 'model': 'baseline', 'params': '{}', 'inner_mae_kg_ha': baseline_inner})
    return chosen, trials


def choose_champion(rows):
    """Frozen rule: lowest pooled temporal MAE; an MLP must also win the spatial scope with stable seeds."""
    others = [r for r in rows if r['model'] != 'mlp']
    best = min(others, key=lambda r: r['temporal'])
    for row in sorted((r for r in rows if r['model'] == 'mlp'), key=lambda r: r['temporal']):
        if row['temporal'] < best['temporal'] and row['spatiotemporal'] < best['spatiotemporal'] \
                and row['seed_win_share'] >= SEED_WIN_SHARE:
            return row
    return best


def supported_horizons(model_by_horizon, baseline_by_horizon):
    """A cutoff is served only where the model is at least as good as the no-weather baseline."""
    return sorted(h for h, mae in model_by_horizon.items() if mae <= baseline_by_horizon[h])


def run(years, test=False):
    import joblib
    import numpy as np
    import pandas as pd
    import torch
    torch.set_num_threads(6)
    frame, sources = load(test)
    MODELS.mkdir(parents=True, exist_ok=True)
    feature_sets, cumulative = {}, list(BASE)
    for group, columns in sources:
        cumulative = [*cumulative, *columns]
        feature_sets[group] = list(cumulative)
    hyper_path = MODELS / 'hyperparameters.json'
    tag = 'test' if test else 'validation'
    metrics, by_horizon, predictions, champions, search = [], [], [], [], []
    frozen = json.loads(hyper_path.read_text(encoding='utf-8')) if test else {}
    for crop in ('soy', 'maize2'):
        cohort = frame[frame.crop == crop].reset_index(drop=True)
        if test:
            chosen = {tuple(k.split('|')): v for k, v in frozen[crop].items()}
            for key, value in chosen.items():
                if key[1] == 'mlp':
                    value['config']['hidden'] = tuple(value['config']['hidden'])
        else:
            first_train, _ = fold(cohort, VALIDATION_YEARS[0])
            print(f'{crop}: selecting on {first_train.sample_id.nunique()} harvests', flush=True)
            chosen, trials = select_hyperparameters(first_train, feature_sets)
            search.extend({'crop': crop, **t} for t in trials)
            frozen[crop] = {'|'.join(k): ({'config': {**v['config'], 'hidden': list(v['config']['hidden'])}, 'epochs': v['epochs']}
                                          if k[1] == 'mlp' else v) for k, v in chosen.items()}
        outputs, seed_outputs = [], {}
        for year in years:
            train, target = fold(cohort, year)
            base = Baseline().fit(train)
            residual = pd.Series(train.yield_kg_ha.to_numpy(float) - base.predict(train), index=train.index)
            base_target = base.predict(target)
            meta = target[['sample_id', 'municipality_code', 'state_code', 'harvest_year', 'horizon_days', 'feature_cutoff',
                           'mvp', 'spatial_holdout', 'yield_kg_ha']].copy()
            meta['train_label_families'] = train.sample_id.nunique()
            columns = {('history', 'baseline'): base_target}
            for group, features in feature_sets.items():
                for kind in ('ridge', 'hgb'):
                    model = PerHorizon(kind, chosen[(group, kind)], features).fit(train, residual)
                    columns[(group, kind)] = base_target + model.predict(target)
                spec = chosen[(group, 'mlp')]
                mlp = PooledMLP(spec['config'], features).fit(train, residual, spec['epochs'])
                columns[(group, 'mlp')] = base_target + mlp.predict(target)
                seed_outputs.setdefault(group, []).append(
                    pd.DataFrame((base_target + mlp.predict_seeds(target)).T, index=target.index))
                if year == years[-1]:
                    joblib.dump({'baseline': base, 'model': mlp}, MODELS / f'{crop}_{group}_mlp_{tag}_{year}.joblib')
                    for kind in ('ridge', 'hgb'):
                        joblib.dump({'baseline': base, 'model': PerHorizon(kind, chosen[(group, kind)], features).fit(train, residual)},
                                    MODELS / f'{crop}_{group}_{kind}_{tag}_{year}.joblib')
            for (group, kind), values in columns.items():
                outputs.append(meta.assign(sources=group, model=kind, predicted_kg_ha=values))
            print(f'{crop} target {year}: train {train.sample_id.nunique()} harvests, target {target.sample_id.nunique()}', flush=True)
        table = pd.concat(outputs, ignore_index=False)
        summary = []
        for (group, kind), part in table.groupby(['sources', 'model'], sort=False):
            row = {'sources': group, 'model': kind, 'seed_win_share': 1.0}
            for scope, mask in SCOPES.items():
                ev = part[mask(part)]
                result = scores(ev.yield_kg_ha, ev.predicted_kg_ha)
                metrics.append({'crop': crop, 'sources': group, 'model': kind, 'scope': scope, **result,
                                'label_families': int(ev.sample_id.nunique()), 'municipalities': int(ev.municipality_code.nunique())})
                row[scope] = result['mae_kg_ha']
                for horizon, sub in ev.groupby('horizon_days'):
                    by_horizon.append({'crop': crop, 'sources': group, 'model': kind, 'scope': scope, 'horizon_days': horizon,
                                       **scores(sub.yield_kg_ha, sub.predicted_kg_ha)})
            summary.append(row)
            predictions.append(part[part.mvp].assign(crop=crop))
        best_other = min((r for r in summary if r['model'] != 'mlp'), key=lambda r: r['temporal'])
        temporal_all = table[(table.sources == 'history') & ~table.spatial_holdout]
        for row in summary:
            if row['model'] == 'mlp':
                seeds = pd.concat(seed_outputs[row['sources']]).loc[temporal_all.index]
                seed_mae = (seeds.sub(temporal_all.yield_kg_ha, axis=0)).abs().mean(axis=0)
                row.update(seed_win_share=float((seed_mae < best_other['temporal']).mean()),
                           seed_mae_mean=float(seed_mae.mean()), seed_mae_std=float(seed_mae.std(ddof=1)))
        champion = choose_champion(summary)
        pick = lambda r, scope: table[(table.sources == r['sources']) & (table.model == r['model']) & SCOPES[scope](table)]
        horizons = {}
        for name, row in (('champion', champion), ('baseline', next(r for r in summary if r['model'] == 'baseline'))):
            part = pick(row, 'temporal')
            horizons[name] = {int(h): float((s.predicted_kg_ha - s.yield_kg_ha).abs().mean()) for h, s in part.groupby('horizon_days')}
        champion_rows, baseline_rows = pick(champion, 'temporal'), pick(next(r for r in summary if r['model'] == 'baseline'), 'temporal')
        delta = (champion_rows.predicted_kg_ha - champion_rows.yield_kg_ha).abs().to_numpy() \
            - (baseline_rows.predicted_kg_ha - baseline_rows.yield_kg_ha).abs().to_numpy()
        low, high = cluster_bootstrap_ci(delta, champion_rows.municipality_code.to_numpy(), draws=500)
        champions.append({'crop': crop, 'champion_sources': champion['sources'], 'champion_model': champion['model'],
                          'champion_mae_kg_ha': {s: champion[s] for s in SCOPES},
                          'baseline_mae_kg_ha': {s: next(r for r in summary if r['model'] == 'baseline')[s] for s in SCOPES},
                          'champion_minus_baseline_temporal_kg_ha': float(delta.mean()), 'ci95': [low, high],
                          'mlp': [{k: v for k, v in r.items()} for r in summary if r['model'] == 'mlp'],
                          'champion_mae_by_horizon': horizons['champion'], 'baseline_mae_by_horizon': horizons['baseline'],
                          'supported_horizons': supported_horizons(horizons['champion'], horizons['baseline'])})
    write_csv(DATA / f'{tag}_metrics.csv', metrics)
    write_csv(DATA / f'{tag}_metrics_by_horizon.csv', by_horizon)
    pd.concat(predictions).to_csv(DATA / f'mvp_{tag}_predictions.csv', index=False)
    save_json(DATA / f'{tag}_champions.json', champions)
    manifest_path = MODELS / 'manifest.json'
    if test:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest.update(test_evaluated=True, test_input_sha256=sha(DATA / 'test_samples.parquet'))
    else:
        write_csv(DATA / 'hyperparameter_search.csv', search)
        save_json(hyper_path, frozen)
        manifest = {'version': 'research_v6_walk_forward', 'inputs': {'data/processed/training_v6/samples.parquet': sha(DATA / 'samples.parquet')},
                    'protocol': f'target year Y trained on harvests <= Y-{LABEL_LAG}; held-out municipalities never trained',
                    'validation_years': list(VALIDATION_YEARS), 'test_years': list(TEST_YEARS), 'test_evaluated': False,
                    'baseline_features': BASE, 'cumulative_sources': feature_sets, 'hyperparameters': 'hyperparameters.json',
                    'selection': f'first fold training years, inner split on its last {INNER_YEARS} years',
                    'champion_rule': choose_champion.__doc__, 'horizon_rule': supported_horizons.__doc__,
                    'operational_approved': False, 'availability': 'retrospective; input and label release vintages unverified'}
    save_json(manifest_path, manifest)
    for c in champions:
        print(c['crop'], c['champion_sources'], c['champion_model'], {k: round(v) for k, v in c['champion_mae_kg_ha'].items()},
              'baseline', {k: round(v) for k, v in c['baseline_mae_kg_ha'].items()}, 'horizons', c['supported_horizons'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Score the frozen choices once on the sealed test')
    args = parser.parse_args()
    if args.test:
        manifest = json.loads((MODELS / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['test_evaluated']:
            raise SystemExit('The sealed test was already scored once; refusing to score it again.')
        run(TEST_YEARS, test=True)
    else:
        run(VALIDATION_YEARS)


if __name__ == '__main__':
    # Import by name so pickled classes resolve to data.ingest.train_v6, not __main__.
    from data.ingest.train_v6 import main as entry
    entry()
