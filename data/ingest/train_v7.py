"""Crop-expansion training: one CUDA MLP per crop against references, history, Ridge and boosting.

Protocol (same as v6): target harvest Y is predicted by models fitted on harvests up to Y-2;
municipalities in the spatial holdout never enter a fit; every cutoff of a harvest stays
in the same group. Hyperparameters, network size, batch size and precision are chosen
once, on the first fold's training years, with an inner temporal split. MLP runs require
CUDA: there is no CPU fallback. The rules below were fixed before any validation score.
"""
import argparse
import json
import time

from data.ingest import gpu
from data.ingest.hydro_training_features import HYDRO_FEATURES, WEATHER_FEATURES
from data.ingest.train_mlp_v4 import cluster_bootstrap_ci, scores
from data.ingest.train_v6 import PerHorizon, transformer, usable
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv

OUT = ROOT / 'data/processed/training_v7'
MODELS = ROOT / 'data/models/research_v7'
V6 = ROOT / 'data/processed/training_v6/samples.parquet'
V7 = ROOT / 'data/processed/training_v7/samples.parquet'
LABEL_LAG = 2
VALIDATION_YEARS = (2019, 2020, 2021)
SITE = ['latitude', 'longitude', 'elevation_m']
ANNUAL_BASE = ['harvest_year', 'yield_lag2to4_mean_kg_ha', 'yield_lag_count']
COFFEE_BASE = ['harvest_year', *[f'yield_lag{k}_kg_ha' for k in range(2, 7)], 'yield_lag_count']
COFFEE_HYDRO = ['coffee_deficit_mm', 'coffee_stress_days', 'coffee_eta_etc_ratio', 'coffee_storage_end_mm',
                'coffee_deficit_dry_season_mm', 'coffee_deficit_flowering_mm', 'coffee_deficit_filling_mm']
COFFEE_REFS = ['ref_last_available_kg_ha', 'ref_same_phase_kg_ha', 'ref_biennial_mean_kg_ha', 'ref_cycle_mean_kg_ha']
COHORTS = {
    'soy': ('v6', 'annual'), 'maize2': ('v6', 'annual'), 'maize1': ('v7', 'annual'), 'wheat': ('v7', 'annual'),
    'rice': ('v7', 'annual'), 'bean1': ('v7', 'annual'), 'bean2': ('v7', 'annual'),
    'coffee_arabica': ('v7', 'coffee'), 'coffee_conilon': ('v7', 'coffee'), 'coffee_total_mg': ('v7', 'coffee'),
}
MLP_GRID = [{'hidden': h, 'dropout': d, 'weight_decay': w} for h in ((32, 16), (64, 32), (128, 64, 32), (256, 128, 64))
            for d in (0.0, 0.2) for w in (1e-4, 1e-2)]
RIDGE_GRID = (1.0, 10.0, 100.0, 1000.0)
HGB_GRID = ({'max_iter': 200, 'max_leaf_nodes': 7, 'min_samples_leaf': 40, 'l2_regularization': 10.0, 'learning_rate': 0.05},
            {'max_iter': 400, 'max_leaf_nodes': 15, 'min_samples_leaf': 40, 'l2_regularization': 1.0, 'learning_rate': 0.05})
BATCH_CANDIDATES = (256, 1024, 4096)
MAX_EPOCHS, PATIENCE = 120, 10
FINAL_SEEDS = (101, 102, 103, 104, 105)
SEED_WIN_SHARE = 0.8
RULES = {
    'champion': 'lowest pooled temporal MAE; an MLP also needs a lower spatiotemporal MAE and >=80% of seeds below the best other model',
    'validated': 'evaluable cohort, champion beats the history model in temporal MAE with a municipality-clustered 95% interval '
                 'below zero, and spatiotemporal MAE <= 1.15 x temporal MAE',
    'experimental': 'evaluable cohort where the gain interval crosses zero or the spatial check fails',
    'reference_only': 'evaluable cohort where no model beats the plain history reference; serve the reference with its error',
    'unavailable': 'fewer than 30 training municipalities, fewer than 8 held-out municipalities or fewer than 2 validation years',
    'separate_mg_mt_model': 'not in this round (declared before evaluation)',
}


def feature_sets(kind):
    if kind == 'annual':
        base, groups = ANNUAL_BASE, [('weather', WEATHER_FEATURES), ('hydro', HYDRO_FEATURES), ('site', SITE)]
    else:
        base, groups = COFFEE_BASE, [('weather', [*WEATHER_FEATURES, 'prev_rain_mm', 'prev_heat_days']), ('hydro', COFFEE_HYDRO), ('site', SITE)]
    cumulative, out = list(base), {}
    for name, columns in groups:
        cumulative = [*cumulative, *columns]
        out[name] = list(cumulative)
    return base, out


def fold(frame, year):
    train = frame[(frame.harvest_year <= year - LABEL_LAG) & ~frame.spatial_holdout]
    return train, frame[frame.harvest_year == year]


def inner_split(train, years):
    last = sorted(train.harvest_year.unique())[-years:]
    stop = train.harvest_year.isin(last)
    return train[~stop], train[stop]


class History:
    """Ridge on trend and published lagged yields; one model per cutoff because coffee lags depend on the milestone."""

    def __init__(self, features, alpha=10.0):
        self.candidates, self.alpha = list(features), alpha

    def fit(self, train):
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        self.models = {}
        for horizon, part in train.groupby('horizon_days'):
            features = usable(part, self.candidates)
            self.models[horizon] = (features, make_pipeline(transformer(), Ridge(alpha=self.alpha)).fit(part[features].astype(float), part.yield_kg_ha))
        return self

    def predict(self, frame):
        import numpy as np
        out = np.zeros(len(frame))
        for horizon in frame.horizon_days.unique():
            mask = (frame.horizon_days == horizon).to_numpy()
            features, model = self.models[horizon]
            out[mask] = model.predict(frame.loc[mask, features].astype(float))
        return out


class GpuMLP:
    """One network for all cutoffs of a crop (cutoff as input) on the residual of the history model, trained on CUDA."""

    def __init__(self, config, features, device, batch=1024, amp=False):
        self.config, self.candidates, self.device, self.batch, self.amp = dict(config), [*features, 'horizon_days'], device, batch, amp

    def _network(self, width, seed):
        import torch
        torch.manual_seed(seed)
        layers, previous = [], width
        for size in self.config['hidden']:
            layers += [torch.nn.Linear(previous, size), torch.nn.ReLU()]
            if self.config['dropout']:
                layers.append(torch.nn.Dropout(self.config['dropout']))
            previous = size
        return torch.nn.Sequential(*layers, torch.nn.Linear(previous, 1)).to(self.device)

    def _x(self, frame):
        import torch
        return torch.as_tensor(self.transformer.transform(frame[self.features].astype(float)), dtype=torch.float32, device=self.device)

    def _prepare(self, train, residual):
        import torch
        started = time.perf_counter()
        self.features = usable(train, self.candidates)
        self.transformer = transformer().fit(train[self.features].astype(float))
        values = residual[train.index].to_numpy(float)
        self.scale = float(values.std()) or 1.0
        prepared = time.perf_counter()
        x = self._x(train)
        y = torch.as_tensor(values / self.scale, dtype=torch.float32, device=self.device)
        torch.cuda.synchronize(self.device)
        self.timing = {'prep_s': prepared - started, 'transfer_s': time.perf_counter() - prepared}
        return x, y

    def _epoch(self, network, optimizer, scaler, x, y, generator):
        import torch
        network.train()
        order = torch.randperm(len(x), generator=generator, device=self.device)
        for start in range(0, len(x), self.batch):
            batch = order[start:start + self.batch]
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast('cuda', dtype=torch.float16, enabled=self.amp):
                criterion = torch.nn.functional.mse_loss if self.config.get('loss') == 'mse' else torch.nn.functional.smooth_l1_loss
                loss = criterion(network(x[batch]).squeeze(1).float(), y[batch])
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

    def _raw(self, network, x):
        import torch
        network.eval()
        with torch.no_grad():
            return network(x).squeeze(1).float().cpu().numpy() * self.scale

    def _tools(self, network, seed):
        import torch
        optimizer = torch.optim.AdamW(network.parameters(), lr=self.config.get('learning_rate', 1e-3), weight_decay=self.config['weight_decay'])
        return optimizer, torch.amp.GradScaler('cuda', enabled=self.amp), torch.Generator(device=self.device).manual_seed(seed)

    def best_epoch(self, fit, stop, residual, seed=11):
        import numpy as np
        x, y = self._prepare(fit, residual)
        x_stop, truth = self._x(stop), residual[stop.index].to_numpy(float)
        network = self._network(x.shape[1], seed)
        optimizer, scaler, generator = self._tools(network, seed)
        best = (1, float('inf'))
        for epoch in range(1, MAX_EPOCHS + 1):
            self._epoch(network, optimizer, scaler, x, y, generator)
            score = float(np.abs(self._raw(network, x_stop) - truth).mean())
            if score < best[1]:
                best = (epoch, score)
            elif epoch - best[0] >= PATIENCE:
                break
        return best

    def fit(self, train, residual, epochs, seeds=FINAL_SEEDS):
        import torch
        x, y = self._prepare(train, residual)
        self.networks, self.epochs, self.seed_log = [], int(epochs), []
        for seed in seeds:
            torch.cuda.reset_peak_memory_stats(self.device)
            started = time.perf_counter()
            network = self._network(x.shape[1], seed)
            optimizer, scaler, generator = self._tools(network, seed)
            for _ in range(self.epochs):
                self._epoch(network, optimizer, scaler, x, y, generator)
            torch.cuda.synchronize(self.device)
            self.seed_log.append({'seed': seed, 'train_s': time.perf_counter() - started, 'epochs': self.epochs,
                                  'peak_vram_mb': torch.cuda.max_memory_allocated(self.device) / 2 ** 20,
                                  'device': str(next(network.parameters()).device)})
            self.networks.append(network)
        return self

    def predict_seeds(self, frame):
        import numpy as np
        x = self._x(frame)
        return np.array([self._raw(network, x) for network in self.networks])

    def predict(self, frame):
        return self.predict_seeds(frame).mean(axis=0)

    def export(self, folder, name, history):
        """Portable artifact: weights as state_dict (.pt) plus JSON preprocessing; no pickled project classes."""
        import torch
        imputer, scaler = self.transformer.steps[0][1], self.transformer.steps[1][1]
        history_spec = {str(h): linear_spec(features, model) for h, (features, model) in history.models.items()}
        spec = {'format': 'gaiapus-mlp-v1', 'features': self.features, 'imputer_median': imputer.statistics_.tolist(),
                'scaler_mean': scaler.mean_.tolist(), 'scaler_scale': scaler.scale_.tolist(), 'residual_scale': self.scale,
                'hidden': list(self.config['hidden']), 'dropout': self.config['dropout'], 'seeds': [s['seed'] for s in self.seed_log],
                'epochs': self.epochs, 'history_by_horizon': history_spec}
        weights = {f'seed{i}.{k}': v.detach().cpu() for i, net in enumerate(self.networks) for k, v in net.state_dict().items()}
        torch.save(weights, folder / f'{name}.pt')
        save_json(folder / f'{name}.json', spec)
        return spec


def linear_spec(features, pipeline):
    prep, ridge = pipeline.steps[0][1], pipeline.steps[-1][1]  # make_pipeline(transformer(), Ridge): nested preprocessing
    imputer, scaler = prep.steps[0][1], prep.steps[1][1]
    return {'features': list(features), 'imputer_median': imputer.statistics_.tolist(), 'scaler_mean': scaler.mean_.tolist(),
            'scaler_scale': scaler.scale_.tolist(), 'coef': ridge.coef_.tolist(), 'intercept': float(ridge.intercept_)}


def select_batch_and_precision(fit, stop, residual, features, device):
    """Pilot: same network, several batch sizes and fp16 autocast; keep the fastest setting within 1% of the best inner MAE."""
    import torch
    trials = []
    for batch in BATCH_CANDIDATES:
        for amp in (False, True):
            torch.cuda.reset_peak_memory_stats(device)
            started = time.perf_counter()
            model = GpuMLP({'hidden': (128, 64, 32), 'dropout': 0.0, 'weight_decay': 1e-4}, features, device, batch, amp)
            epoch, score = model.best_epoch(fit, stop, residual)
            torch.cuda.synchronize(device)
            elapsed = time.perf_counter() - started
            trials.append({'batch': batch, 'amp': amp, 'inner_mae_kg_ha': score, 'best_epoch': epoch, 'seconds': elapsed,
                           'seconds_per_epoch': elapsed / max(epoch + PATIENCE, 1),
                           'peak_vram_mb': torch.cuda.max_memory_allocated(device) / 2 ** 20, **model.timing})
    best = min(t['inner_mae_kg_ha'] for t in trials)
    chosen = min((t for t in trials if t['inner_mae_kg_ha'] <= best * 1.01), key=lambda t: t['seconds_per_epoch'])
    return chosen['batch'], chosen['amp'], trials


def select_hyperparameters(train, sets, base, inner_years, device):
    import numpy as np
    import pandas as pd
    fit, stop = inner_split(train, inner_years)
    history = History(base).fit(fit)
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - history.predict(train), index=train.index)
    truth = residual[stop.index].to_numpy(float)
    batch, amp, pilot = select_batch_and_precision(fit, stop, residual, sets[list(sets)[-1]], device)
    chosen, trials = {'batch': batch, 'amp': amp}, []
    for group, features in sets.items():
        for kind, grid in (('ridge', RIDGE_GRID), ('hgb', HGB_GRID)):
            errors = [float(np.abs(PerHorizon(kind, p, features).fit(fit, residual).predict(stop) - truth).mean()) for p in grid]
            chosen[(group, kind)] = grid[int(np.argmin(errors))]
            trials.extend({'sources': group, 'model': kind, 'params': json.dumps(p), 'inner_mae_kg_ha': e} for p, e in zip(grid, errors))
        runs = [(c, GpuMLP(c, features, device, batch, amp).best_epoch(fit, stop, residual)) for c in MLP_GRID]
        config, (epochs, _) = min(runs, key=lambda r: r[1][1])
        chosen[(group, 'mlp')] = {'config': config, 'epochs': epochs}
        trials.extend({'sources': group, 'model': 'mlp', 'params': json.dumps({**c, 'hidden': list(c['hidden']), 'epochs': e}),
                       'inner_mae_kg_ha': s} for c, (e, s) in runs)
    trials.append({'sources': 'history', 'model': 'history', 'params': '{}', 'inner_mae_kg_ha': float(np.abs(truth).mean())})
    return chosen, trials, pilot


def choose_champion(rows):
    others = [r for r in rows if r['model'] != 'mlp']
    best = min(others, key=lambda r: r['temporal'])
    for row in sorted((r for r in rows if r['model'] == 'mlp'), key=lambda r: r['temporal']):
        if row['temporal'] < best['temporal'] and row['spatiotemporal'] < best['spatiotemporal'] and row['seed_win_share'] >= SEED_WIN_SHARE:
            return row
    return best


def publication_status(evaluable, champion, history_row, ci_high):
    if not evaluable:
        return 'unavailable'
    if champion['model'] in ('history', 'reference') or champion['temporal'] >= history_row['temporal']:
        return 'reference_only'
    if ci_high < 0 and champion['spatiotemporal'] <= 1.15 * champion['temporal']:
        return 'validated'
    return 'experimental'


def load_cohort(name):
    import pandas as pd
    source, kind = COHORTS[name]
    frame = pd.read_parquet(V6 if source == 'v6' else V7)
    frame = frame[frame.crop == name].reset_index(drop=True)
    if (frame.harvest_year > 2021).any():
        raise ValueError('Sealed test years present')
    if 'milestone' not in frame:
        frame['milestone'] = 'day_' + frame.horizon_days.astype(str)
    if kind == 'annual':
        frame['ref_lag_mean_kg_ha'] = frame.yield_lag2to4_mean_kg_ha
    return frame, kind


def run_cohort(name, device, pilot_only=False):
    import numpy as np
    import pandas as pd
    frame, kind = load_cohort(name)
    base, sets = feature_sets(kind)
    inner_years = 2 if name in ('coffee_arabica', 'coffee_conilon') else 3
    first_train, _ = fold(frame, VALIDATION_YEARS[0])
    held_out = frame[frame.spatial_holdout].municipality_code.nunique()
    evaluable = (first_train.municipality_code.nunique() >= 30 and held_out >= 8
                 and frame[frame.harvest_year.isin(VALIDATION_YEARS)].harvest_year.nunique() >= 2)
    info = {'cohort': name, 'kind': kind, 'harvests': int(frame.sample_id.nunique()), 'municipalities': int(frame.municipality_code.nunique()),
            'states': sorted(frame.state_code.unique()), 'training_municipalities': int(first_train.municipality_code.nunique()),
            'held_out_municipalities': int(held_out), 'years': [int(frame.harvest_year.min()), int(frame.harvest_year.max())],
            'cutoffs': sorted(int(h) for h in frame.horizon_days.unique()), 'evaluable': bool(evaluable)}
    if not evaluable:
        return info, None
    print(f'[{name}] selecting on {first_train.sample_id.nunique()} harvests', flush=True)
    started = time.perf_counter()
    chosen, trials, pilot = select_hyperparameters(first_train, sets, base, inner_years, device)
    info['selection_seconds'] = time.perf_counter() - started
    info['batch'], info['amp'] = chosen['batch'], chosen['amp']
    if pilot_only:
        return info, {'pilot': pilot, 'trials': trials}
    refs = COFFEE_REFS if kind == 'coffee' else ['ref_lag_mean_kg_ha']
    outputs, seeds_by_group, timing = [], {}, []
    for year in VALIDATION_YEARS:
        train, target = fold(frame, year)
        if target.empty:
            continue
        history = History(base).fit(train)
        residual = pd.Series(train.yield_kg_ha.to_numpy(float) - history.predict(train), index=train.index)
        base_target = history.predict(target)
        keep = ['sample_id', 'municipality_code', 'state_code', 'harvest_year', 'horizon_days', 'milestone', 'mvp',
                'spatial_holdout', 'yield_kg_ha'] + (['high_year'] if 'high_year' in target else [])
        meta = target[keep].copy()
        mean = float(train.yield_kg_ha.mean())
        for ref in refs:
            outputs.append(meta.assign(sources=ref.replace('_kg_ha', ''), model='reference', predicted_kg_ha=target[ref].fillna(mean).to_numpy(),
                                       ref_covered=target[ref].notna().to_numpy()))
        outputs.append(meta.assign(sources='history', model='history', predicted_kg_ha=base_target, ref_covered=True))
        for group, features in sets.items():
            for kind_ in ('ridge', 'hgb'):
                model = PerHorizon(kind_, chosen[(group, kind_)], features).fit(train, residual)
                outputs.append(meta.assign(sources=group, model=kind_, predicted_kg_ha=base_target + model.predict(target), ref_covered=True))
                if year == VALIDATION_YEARS[-1] and kind_ == 'ridge':
                    save_json(MODELS / f'{name}_{group}_ridge_{year}.json', {
                        'format': 'gaiapus-ridge-v1', 'history_by_horizon': {str(h): linear_spec(f, m) for h, (f, m) in history.models.items()},
                        'residual_by_horizon': {str(h): linear_spec(f, m) for h, (f, m) in model.models.items()}})
            spec = chosen[(group, 'mlp')]
            mlp = GpuMLP(spec['config'], features, device, chosen['batch'], chosen['amp']).fit(train, residual, spec['epochs'])
            eval_started = time.perf_counter()
            per_seed = base_target + mlp.predict_seeds(target)
            eval_s = time.perf_counter() - eval_started
            timing.extend({'cohort': name, 'fold': year, 'sources': group, **mlp.timing, 'eval_s': eval_s, **s} for s in mlp.seed_log)
            outputs.append(meta.assign(sources=group, model='mlp', predicted_kg_ha=per_seed.mean(axis=0), ref_covered=True))
            seeds_by_group.setdefault(group, []).append(pd.DataFrame(per_seed.T, index=target.index))
            if year == VALIDATION_YEARS[-1]:
                mlp.export(MODELS, f'{name}_{group}_mlp_{year}', history)
        print(f'[{name}] target {year}: train {train.sample_id.nunique()} harvests / {train.municipality_code.nunique()} municipalities, '
              f'target {target.sample_id.nunique()}', flush=True)
    table = pd.concat(outputs)
    metrics, summary = [], []
    for (group, model), part in table.groupby(['sources', 'model'], sort=False):
        row = {'sources': group, 'model': model, 'seed_win_share': 1.0}
        for scope in ('temporal', 'spatiotemporal'):
            ev = part[part.spatial_holdout == (scope == 'spatiotemporal')]
            result = scores(ev.yield_kg_ha, ev.predicted_kg_ha)
            metrics.append({'cohort': name, 'sources': group, 'model': model, 'scope': scope, 'breakdown': 'all', 'value': 'all', **result,
                            'mae_pct_of_mean': 100 * result['mae_kg_ha'] / float(ev.yield_kg_ha.mean()),
                            'label_families': int(ev.sample_id.nunique()), 'municipalities': int(ev.municipality_code.nunique()),
                            'coverage': float(ev.ref_covered.mean())})
            row[scope] = result['mae_kg_ha']
            for column in ('horizon_days', 'state_code', 'harvest_year', 'milestone') + (('high_year',) if 'high_year' in ev else ()):
                for value, sub in ev.groupby(column):
                    metrics.append({'cohort': name, 'sources': group, 'model': model, 'scope': scope, 'breakdown': column, 'value': str(value),
                                    **scores(sub.yield_kg_ha, sub.predicted_kg_ha), 'mae_pct_of_mean': None,
                                    'label_families': int(sub.sample_id.nunique()), 'municipalities': int(sub.municipality_code.nunique()),
                                    'coverage': float(sub.ref_covered.mean())})
        summary.append(row)
    learned = [r for r in summary if r['model'] in ('ridge', 'hgb', 'history')]
    best_other = min(learned, key=lambda r: r['temporal'])
    temporal_rows = table[(table.sources == 'history') & ~table.spatial_holdout]
    for row in summary:
        if row['model'] == 'mlp':
            seeds = pd.concat(seeds_by_group[row['sources']]).loc[temporal_rows.index]
            seed_mae = seeds.sub(temporal_rows.yield_kg_ha, axis=0).abs().mean(axis=0)
            row.update(seed_win_share=float((seed_mae < best_other['temporal']).mean()), seed_mae_std=float(seed_mae.std(ddof=1)))
    champion = choose_champion(summary)
    history_row = next(r for r in summary if r['model'] == 'history')
    pick = lambda r: table[(table.sources == r['sources']) & (table.model == r['model']) & ~table.spatial_holdout].sort_index()
    a, b = pick(champion), pick(history_row)
    delta = (a.predicted_kg_ha - a.yield_kg_ha).abs().to_numpy() - (b.predicted_kg_ha - b.yield_kg_ha).abs().to_numpy()
    low, high = cluster_bootstrap_ci(delta, a.municipality_code.to_numpy(), draws=500)
    best_mlp = min((r for r in summary if r['model'] == 'mlp'), key=lambda r: r['temporal'])
    info.update(champion=f"{champion['sources']}+{champion['model']}", champion_mae=champion['temporal'],
                champion_spatial_mae=champion['spatiotemporal'], history_mae=history_row['temporal'],
                history_spatial_mae=history_row['spatiotemporal'], gain_vs_history=float(delta.mean()), gain_ci95=[low, high],
                best_mlp=f"{best_mlp['sources']}+mlp", best_mlp_mae=best_mlp['temporal'], best_mlp_spatial_mae=best_mlp['spatiotemporal'],
                best_mlp_seed_win_share=best_mlp['seed_win_share'], mean_yield=float(frame.yield_kg_ha.mean()),
                status=publication_status(True, champion, history_row, high), summary=summary,
                hyperparameters={'|'.join(k) if isinstance(k, tuple) else k: v for k, v in chosen.items()})
    table.to_parquet(OUT / f'predictions_{name}.parquet')
    return info, {'metrics': metrics, 'trials': trials, 'pilot': pilot, 'timing': timing}


TEST_YEARS = (2022, 2023, 2024)


def load_test(name):
    import pandas as pd
    source, kind = COHORTS[name]
    path = ROOT / 'data/processed/training_v6/test_samples.parquet' if source == 'v6' else OUT / 'test_samples.parquet'
    test = pd.read_parquet(path)
    test = test[test.crop == name].reset_index(drop=True)
    if not test.harvest_year.between(TEST_YEARS[0], TEST_YEARS[-1]).all():
        raise ValueError('Test table must hold only 2022-2024 harvests')
    if 'milestone' not in test:
        test['milestone'] = 'day_' + test.horizon_days.astype(str)
    if kind == 'annual':
        test['ref_lag_mean_kg_ha'] = test.yield_lag2to4_mean_kg_ha
    return test


def score(name, table):
    metrics = []
    for (group, model), part in table.groupby(['sources', 'model'], sort=False):
        for scope in ('temporal', 'spatiotemporal'):
            ev = part[part.spatial_holdout == (scope == 'spatiotemporal')]
            if ev.empty:
                continue
            result = scores(ev.yield_kg_ha, ev.predicted_kg_ha)
            metrics.append({'cohort': name, 'sources': group, 'model': model, 'scope': scope, 'breakdown': 'all', 'value': 'all', **result,
                            'mae_pct_of_mean': 100 * result['mae_kg_ha'] / float(ev.yield_kg_ha.mean()),
                            'label_families': int(ev.sample_id.nunique()), 'municipalities': int(ev.municipality_code.nunique()),
                            'coverage': float(ev.ref_covered.mean())})
            for column in ('horizon_days', 'state_code', 'harvest_year', 'milestone') + (('high_year',) if 'high_year' in ev else ()):
                for value, sub in ev.groupby(column):
                    metrics.append({'cohort': name, 'sources': group, 'model': model, 'scope': scope, 'breakdown': column, 'value': str(value),
                                    **scores(sub.yield_kg_ha, sub.predicted_kg_ha), 'mae_pct_of_mean': None,
                                    'label_families': int(sub.sample_id.nunique()), 'municipalities': int(sub.municipality_code.nunique()),
                                    'coverage': float(sub.ref_covered.mean())})
    return metrics


def test_cohort(name, device):
    """One-shot evaluation on 2022-2024 with every choice frozen in the validation run."""
    import pandas as pd
    folder = OUT / 'test' / name
    if (folder / 'cohort.json').exists():
        raise SystemExit(f'The sealed test was already scored for {name}; refusing to score it again.')
    info = json.loads((OUT / f'validation/{name}/cohort.json').read_text(encoding='utf-8'))
    frozen = info['hyperparameters']
    frame, kind = load_cohort(name)
    test = load_test(name)
    combined = pd.concat([frame, test], ignore_index=True)
    base, sets = feature_sets(kind)
    refs = COFFEE_REFS if kind == 'coffee' else ['ref_lag_mean_kg_ha']
    outputs = []
    for year in TEST_YEARS:
        train = combined[(combined.harvest_year <= year - LABEL_LAG) & ~combined.spatial_holdout]
        target = test[test.harvest_year == year]
        if target.empty:
            continue
        history = History(base).fit(train)
        residual = pd.Series(train.yield_kg_ha.to_numpy(float) - history.predict(train), index=train.index)
        base_target = history.predict(target)
        keep = ['sample_id', 'municipality_code', 'state_code', 'harvest_year', 'horizon_days', 'milestone', 'mvp',
                'spatial_holdout', 'yield_kg_ha'] + (['high_year'] if 'high_year' in target else [])
        meta = target[keep].copy()
        mean = float(train.yield_kg_ha.mean())
        for ref in refs:
            outputs.append(meta.assign(sources=ref.replace('_kg_ha', ''), model='reference', predicted_kg_ha=target[ref].fillna(mean).to_numpy(),
                                       ref_covered=target[ref].notna().to_numpy()))
        outputs.append(meta.assign(sources='history', model='history', predicted_kg_ha=base_target, ref_covered=True))
        for group, features in sets.items():
            for kind_ in ('ridge', 'hgb'):
                model = PerHorizon(kind_, frozen[f'{group}|{kind_}'], features).fit(train, residual)
                outputs.append(meta.assign(sources=group, model=kind_, predicted_kg_ha=base_target + model.predict(target), ref_covered=True))
            spec = frozen[f'{group}|mlp']
            config = {**spec['config'], 'hidden': tuple(spec['config']['hidden'])}
            mlp = GpuMLP(config, features, device, frozen['batch'], frozen['amp']).fit(train, residual, spec['epochs'])
            outputs.append(meta.assign(sources=group, model='mlp', predicted_kg_ha=base_target + mlp.predict(target), ref_covered=True))
        print(f'[{name}] test {year}: train {train.sample_id.nunique()} harvests, target {target.sample_id.nunique()}', flush=True)
    table = pd.concat(outputs)
    folder.mkdir(parents=True, exist_ok=True)
    table.to_parquet(OUT / f'test_predictions_{name}.parquet')
    metrics = score(name, table)
    write_csv(folder / 'metrics.csv', metrics)
    overall = {(m['sources'], m['model'], m['scope']): m for m in metrics if m['breakdown'] == 'all'}
    champion = tuple(info['champion'].split('+'))
    pick = lambda s, m: table[(table.sources == s) & (table.model == m) & ~table.spatial_holdout].sort_index()
    a, b = pick(*champion), pick('history', 'history')
    delta = (a.predicted_kg_ha - a.yield_kg_ha).abs().to_numpy() - (b.predicted_kg_ha - b.yield_kg_ha).abs().to_numpy()
    low, high = cluster_bootstrap_ci(delta, a.municipality_code.to_numpy(), draws=1000)
    result = {'cohort': name, 'test_years': list(TEST_YEARS), 'frozen_champion': info['champion'], 'validation_status': info['status'],
              'champion_mae': overall[(*champion, 'temporal')]['mae_kg_ha'],
              'champion_spatial_mae': overall.get((*champion, 'spatiotemporal'), {}).get('mae_kg_ha'),
              'champion_bias': overall[(*champion, 'temporal')]['bias_kg_ha'],
              'history_mae': overall[('history', 'history', 'temporal')]['mae_kg_ha'],
              'champion_minus_history': float(delta.mean()), 'ci95': [low, high],
              'harvests': int(test.sample_id.nunique()), 'municipalities': int(test.municipality_code.nunique()),
              'scored_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
    save_json(folder / 'cohort.json', result)
    return result


def main():
    import torch
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohorts', default=','.join(COHORTS))
    parser.add_argument('--pilot', action='store_true', help='Only the batch/precision pilot and selection on the first fold')
    parser.add_argument('--test', action='store_true', help='Score the sealed 2022-2024 test once with the frozen choices')
    args = parser.parse_args()
    device, diag = gpu.require_cuda()
    step = gpu.training_step_check(device)
    print('CUDA', diag, step, flush=True)
    if args.test:
        results = [test_cohort(name, device) for name in args.cohorts.split(',')]
        for r in results:
            print(json.dumps(r, default=str), flush=True)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    tag = 'pilot' if args.pilot else 'validation'
    for name in args.cohorts.split(','):
        started = time.perf_counter()
        info, extra = run_cohort(name, device, pilot_only=args.pilot)
        info['seconds'] = time.perf_counter() - started
        folder = OUT / tag / name
        folder.mkdir(parents=True, exist_ok=True)
        if extra:
            write_csv(folder / 'gpu_pilot.csv', [{'cohort': name, **p} for p in extra['pilot']])
            write_csv(folder / 'hyperparameter_search.csv', [{'cohort': name, **t} for t in extra['trials']])
            if extra.get('metrics'):
                write_csv(folder / 'metrics.csv', extra['metrics'])
            if extra.get('timing'):
                write_csv(folder / 'gpu_timing.csv', extra['timing'])
        save_json(folder / 'cohort.json', info)
        print(json.dumps({k: v for k, v in info.items() if k not in ('summary', 'hyperparameters')}, default=str), flush=True)
    save_json(MODELS / f'{tag}_manifest_{args.cohorts.replace(",", "-")}.json', {
        'version': 'research_v7_crop_expansion', 'rules': RULES, 'cuda': diag, 'training_step_check': step,
        'torch_deterministic': torch.are_deterministic_algorithms_enabled(),
        'inputs': {str(p.relative_to(ROOT)): sha(p) for p in (V6, V7) if p.exists()},
        'protocol': f'target Y fitted on harvests <= Y-{LABEL_LAG}; held-out municipalities never fitted', 'validation_years': list(VALIDATION_YEARS),
        'test_years': [2022, 2024], 'test_evaluated': False, 'operational_approved': False,
        'artifacts': {p.name: sha(p) for p in sorted(MODELS.glob('*')) if p.suffix in ('.pt', '.json') and 'manifest' not in p.name}})


if __name__ == '__main__':
    # Import by name so any pickled class resolves to data.ingest.train_v7, not __main__.
    from data.ingest.train_v7 import main as entry
    entry()
