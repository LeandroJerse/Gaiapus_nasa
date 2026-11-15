"""v8 experiments: nested selection inside walk-forward folds, following data/frozen/training_v8_protocol.json.

For each external fold Y (2019, 2020, 2021) the training data are the pool harvests <= Y-2
(spatial holdout excluded). Inside it, the last two training years are the internal
validation and the internal fit uses years <= first validation year - 2. Every choice
(base, feature set, learner, hyperparameters, shrinkage) is made on the internal
validation of the scope; the chosen candidate is refitted on the whole fold training set
and scored once on year Y. MLPs run on CUDA. Only allowlisted development data are read.
"""
import argparse
import json
import time
from itertools import product

from data.ingest import gpu
from data.ingest import train_v7 as v7
from data.ingest.cohort_v8 import load_development
from data.ingest.train_v6 import PerHorizon
from data.ingest.water_balance_run import ROOT, save_json, write_csv

OUT = ROOT / 'data/processed/training_v8'
MODELS = ROOT / 'data/models/research_v8'
PROTOCOL = json.loads((ROOT / 'data/frozen/training_v8_protocol.json').read_text(encoding='utf-8'))
FOLDS = (2019, 2020, 2021)
SHRINK = tuple(PROTOCOL['candidates']['shrinkage'])
RIDGE = tuple(PROTOCOL['candidates']['learners']['ridge_alpha'])
HGB = tuple(PROTOCOL['candidates']['learners']['hgb'])
MLP_SPEC = PROTOCOL['candidates']['learners']['mlp']
MLP_GRID = [{'hidden': tuple(h), 'dropout': d, 'weight_decay': w, 'learning_rate': lr}
            for h, d, w, lr in product(MLP_SPEC['hidden'], MLP_SPEC['dropout'], MLP_SPEC['weight_decay'], MLP_SPEC['learning_rate'])]
SELECTION_SEED, FINAL_SEEDS = MLP_SPEC['selection_seed'], tuple(MLP_SPEC['final_seeds'])
REF_COLUMNS = {'ref_lag_mean': 'ref_lag_mean_kg_ha', 'ref_cycle_mean': 'ref_cycle_mean_kg_ha', 'ref_last_available': 'ref_last_available_kg_ha',
               'ref_same_phase': 'ref_same_phase_kg_ha', 'ref_biennial_mean': 'ref_biennial_mean_kg_ha'}
SCOPES = {
    'soy_mgmt': {'crop': 'soy', 'states': ('31', '51'), 'pools': ('national', 'scope'), 'v7_regional_mlp': True},
    'maize2_mgmt': {'crop': 'maize2', 'states': ('31', '51'), 'pools': ('national', 'scope')},
    'maize2_national': {'crop': 'maize2', 'states': None, 'pools': ('national',)},
    'coffee_arabica': {'crop': 'coffee_arabica', 'states': ('31',), 'pools': ('scope',)},
    'coffee_total_mg': {'crop': 'coffee_total_mg', 'states': ('31',), 'pools': ('scope',)},
    'coffee_conilon': {'crop': 'coffee_conilon', 'states': ('32',), 'pools': ('scope',)},
}


def feature_sets(kind):
    base, sets = v7.feature_sets(kind)
    out = {'F1_weather': sets['weather'], 'F2_weather_hydro': sets['hydro'], 'F3_full': sets['site']}
    if kind == 'annual':
        out['F3_no_latlon'] = [c for c in sets['site'] if c not in ('latitude', 'longitude')]
    return base, out


def bases_for(kind):
    return ('ref_lag_mean', 'history') if kind == 'annual' else ('ref_cycle_mean', 'ref_last_available', 'history', 'mean')


class History(v7.History):
    """v7 history Ridge; a cutoff whose fit rows have no varying feature (e.g. coffee fits on 2012-2014,
    before any lag exists) predicts the mean yield of that cutoff's fit rows instead of failing."""

    def fit(self, train):
        from data.ingest.train_v6 import usable
        self.fallback = {}
        usable_parts = []
        for horizon, part in train.groupby('horizon_days'):
            if usable(part, self.candidates):
                usable_parts.append(part)
            else:
                self.fallback[horizon] = float(part.yield_kg_ha.mean())
        super().fit(train.loc[[i for p in usable_parts for i in p.index]] if usable_parts else train.iloc[:0])
        return self

    def predict(self, frame):
        import numpy as np
        out = np.zeros(len(frame))
        fitted = ~frame.horizon_days.isin(list(self.fallback)).to_numpy()
        if fitted.any():
            out[fitted] = super().predict(frame[fitted])
        for horizon, value in self.fallback.items():
            out[(frame.horizon_days == horizon).to_numpy()] = value
        return out


def base_fit(name, fit, base_cols):
    """Return a predictor rows -> base prediction, fitted only on `fit`."""
    import numpy as np
    mean = float(fit.yield_kg_ha.mean())
    if name == 'history':
        model = History(base_cols).fit(fit)
        return model.predict
    if name == 'mean':
        return lambda rows: np.full(len(rows), mean)
    return reference_predictor(REF_COLUMNS[name], fit)


def reference_predictor(column, fit):
    """Published reference; a row without it gets the mean yield of the same state in `fit` (the fold's training
    rows), or the mean of all `fit` rows when that state is absent."""
    pool_mean = float(fit.yield_kg_ha.mean())
    state_mean = fit.groupby('state_code').yield_kg_ha.mean()

    def predict(rows):
        fill = rows.state_code.map(state_mean).fillna(pool_mean)
        return rows[column].fillna(fill).to_numpy(float)
    predict.state_mean, predict.pool_mean = {str(k): float(v) for k, v in state_mean.items()}, pool_mean
    return predict


def harvest_scores(rows, pred, weighting):
    """MAE averaged over cutoffs within each harvest, then over harvests (or 50% per state)."""
    import numpy as np
    frame = rows[['sample_id', 'state_code']].assign(err=np.abs(np.asarray(pred, float) - rows.yield_kg_ha.to_numpy(float)))
    per_harvest = frame.groupby(['state_code', 'sample_id']).err.mean()
    if weighting == 'state50':
        return float(per_harvest.groupby(level=0).mean().mean())
    return float(per_harvest.mean())


def mlp_inner(config, features, fit, val, residual, val_residual, device, max_epochs=120, patience=10):
    """Early stopping on the internal validation; returns (best_epoch, correction predicted for val at that epoch)."""
    import numpy as np
    model = v7.GpuMLP(config, features, device, MLP_SPEC['batch'], False)
    x, y = model._prepare(fit, residual)
    x_val, truth = model._x(val), np.asarray(val_residual, float)
    network = model._network(x.shape[1], SELECTION_SEED)
    optimizer, scaler, generator = model._tools(network, SELECTION_SEED)
    best = (1, float('inf'), None)
    for epoch in range(1, max_epochs + 1):
        model._epoch(network, optimizer, scaler, x, y, generator)
        pred = model._raw(network, x_val)
        score = float(np.abs(pred - truth).mean())
        if score < best[1]:
            best = (epoch, score, pred)
        elif epoch - best[0] >= patience:
            break
    return best[0], best[2]


def fold_split(pool_frame, frame, year, scope_mask):
    """External fold: fit on pool harvests <= Y-2 (spatial holdout out); score the scope rows of year Y."""
    train = pool_frame[(pool_frame.harvest_year <= year - 2) & ~pool_frame.spatial_holdout]
    target = frame[(frame.harvest_year == year) & scope_mask(frame)]
    return train, target


def inner_split(train):
    """Internal validation = last two training years; internal fit = years <= first validation year - 2."""
    years = sorted(train.harvest_year.unique())
    val_years = years[-2:]
    return train[train.harvest_year <= val_years[0] - 2], train[train.harvest_year.isin(val_years)], val_years


def select(train, scope_mask, kind, weighting, device, extra_sets=None, log=None, log_limit=15):
    """Nested selection on the internal validation of one fold's training data."""
    import pandas as pd
    base_cols, sets = feature_sets(kind)
    sets = {**sets, **(extra_sets or {})}
    fit, val, val_years = inner_split(train)
    val_scope = val[scope_mask(val)]
    candidates = []
    for base in bases_for(kind):
        predict_base = base_fit(base, fit, base_cols)
        base_fit_pred, base_val = predict_base(fit), predict_base(val)
        residual = pd.Series(fit.yield_kg_ha.to_numpy(float) - base_fit_pred, index=fit.index)
        position = val.index.get_indexer(val_scope.index)
        candidates.append({'base': base, 'features': None, 'learner': 'none', 'params': None, 'shrink': 0.0, 'epochs': None,
                           'score': harvest_scores(val_scope, base_val[position], weighting)})
        for name, columns in sets.items():
            corrections = []
            for alpha in RIDGE:
                corrections.append(('ridge', alpha, None, PerHorizon('ridge', alpha, columns).fit(fit, residual).predict(val)))
            for params in HGB:
                corrections.append(('hgb', params, None, PerHorizon('hgb', params, columns).fit(fit, residual).predict(val)))
            for config in MLP_GRID:
                epochs, pred = mlp_inner(config, columns, fit, val, residual, val.yield_kg_ha.to_numpy(float) - base_val, device)
                corrections.append(('mlp', config, epochs, pred))
            for learner, params, epochs, correction in corrections:
                for shrink in SHRINK[1:]:
                    pred = base_val + shrink * correction
                    candidates.append({'base': base, 'features': name, 'learner': learner, 'params': params, 'shrink': shrink,
                                       'epochs': epochs, 'score': harvest_scores(val_scope, pred[position], weighting)})
    candidates.sort(key=lambda c: c['score'])
    if log is not None:
        log.extend({**c, 'params': json.dumps(c['params'], default=list)} for c in candidates[:log_limit])
    return candidates[0], {'val_years': [int(y) for y in val_years], 'fit_last_year': int(val_years[0] - 2), 'candidates': len(candidates)}


def fit_predict(choice, train, target, kind, device, seeds=FINAL_SEEDS, extra_sets=None, loss=None):
    """Refit the chosen candidate on the whole fold training set and predict `target`."""
    import numpy as np
    import pandas as pd
    base_cols, sets = feature_sets(kind)
    sets = {**sets, **(extra_sets or {})}
    predict_base = base_fit(choice['base'], train, base_cols)
    base_target = predict_base(target)
    if choice['learner'] == 'none' or choice['shrink'] == 0:
        return base_target, None
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - predict_base(train), index=train.index)
    columns = sets[choice['features']]
    if choice['learner'] in ('ridge', 'hgb'):
        correction = PerHorizon(choice['learner'], choice['params'], columns).fit(train, residual).predict(target)
        return base_target + choice['shrink'] * correction, None
    config = {**choice['params'], 'hidden': tuple(choice['params']['hidden'])}
    if loss:
        config['loss'] = loss
    model = v7.GpuMLP(config, columns, device, MLP_SPEC['batch'], False).fit(train, residual, max(int(choice['epochs']), 1), seeds=seeds)
    per_seed = base_target + choice['shrink'] * model.predict_seeds(target)
    return per_seed.mean(axis=0), {'per_seed': per_seed, 'model': model, 'seed_log': model.seed_log}


def fixed_baselines(train, target, kind, scope, v7_info, device):
    """Reference rules and the v7 configurations, refitted on the same fold."""
    import numpy as np
    import pandas as pd
    base_cols, sets = v7.feature_sets(kind)
    out = {}
    for name, column in REF_COLUMNS.items():
        if column in target:
            out[name] = reference_predictor(column, train)(target)
    history = v7.History(base_cols).fit(train)
    out['history'] = history.predict(target)
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - history.predict(train), index=train.index)
    frozen = v7_info['hyperparameters']
    champion_sources, champion_model = v7_info['champion'].split('+')
    if champion_model in ('ridge', 'hgb'):
        out[f'v7_{champion_sources}_{champion_model}'] = out['history'] + PerHorizon(champion_model, frozen[f'{champion_sources}|{champion_model}'],
                                                                                     sets[champion_sources]).fit(train, residual).predict(target)
    if kind == 'annual':
        out['v7_site_ridge'] = out['history'] + PerHorizon('ridge', frozen['site|ridge'], sets['site']).fit(train, residual).predict(target)
    if scope.get('v7_regional_mlp'):
        spec = frozen['site|mlp']
        config = {**spec['config'], 'hidden': tuple(spec['config']['hidden'])}
        model = v7.GpuMLP(config, sets['site'], device, frozen['batch'], frozen['amp']).fit(train, residual, spec['epochs'])
        out['v7_regional_mlp'] = out['history'] + model.predict(target)
    return out


def run_scope(name, device, pools=None, folds=FOLDS, extra_sets=None, tag=None, attach=None):
    import pandas as pd
    scope = SCOPES[name]
    crop = scope['crop']
    kind = 'coffee' if crop.startswith('coffee') else 'annual'
    frame = load_development(crop)
    if attach:
        frame = attach(frame)
    states = scope['states']
    scope_mask = (lambda f: f.state_code.isin(states)) if states else (lambda f: f.state_code == f.state_code)
    weighting = 'state50' if states and len(states) > 1 else 'harvest'
    v7_info = json.loads((v7.OUT / f'validation/{crop}/cohort.json').read_text(encoding='utf-8'))
    folder = OUT / (tag or name)
    folder.mkdir(parents=True, exist_ok=True)
    outputs, selections, log, timing = [], [], [], []
    for pool in pools or scope['pools']:
        pool_frame = frame if pool == 'national' else frame[scope_mask(frame)]
        for year in folds:
            started = time.perf_counter()
            train, target = fold_split(pool_frame, frame, year, scope_mask)
            fold_sets = extra_sets(year) if callable(extra_sets) else extra_sets
            fold_log = []
            choice, info = select(train, scope_mask, kind, weighting, device, extra_sets=fold_sets, log=fold_log)
            prediction, mlp = fit_predict(choice, train, target, kind, device, extra_sets=fold_sets)
            keep = ['sample_id', 'municipality_code', 'state_code', 'harvest_year', 'horizon_days', 'spatial_holdout', 'yield_kg_ha']
            meta = target[keep].copy()
            outputs.append(meta.assign(candidate=f'v8_{pool}', predicted_kg_ha=prediction))
            if mlp:
                for i, seed_pred in enumerate(mlp['per_seed']):
                    outputs.append(meta.assign(candidate=f'v8_{pool}_seed{i}', predicted_kg_ha=seed_pred))
            if pool == (pools or scope['pools'])[0]:
                for baseline, pred in fixed_baselines(train, target, kind, scope, v7_info, device).items():
                    outputs.append(meta.assign(candidate=baseline, predicted_kg_ha=pred))
            selections.append({'pool': pool, 'fold': year, **{k: (json.dumps(v, default=list) if k == 'params' else v) for k, v in choice.items()},
                               **info, 'train_harvests': int(train.sample_id.nunique()), 'seconds': round(time.perf_counter() - started, 1)})
            log.extend({'pool': pool, 'fold': year, **row} for row in fold_log)
            print(f'[{name}] pool={pool} fold={year} choice={choice["base"]}/{choice["features"]}/{choice["learner"]} shrink={choice["shrink"]} '
                  f'inner={choice["score"]:.1f} ({time.perf_counter() - started:.0f}s)', flush=True)
    table = pd.concat(outputs, ignore_index=True)
    table.to_parquet(folder / 'predictions.parquet')
    write_csv(folder / 'selection.csv', selections)
    write_csv(folder / 'inner_top15.csv', log)
    return table, selections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scopes', default='soy_mgmt,maize2_mgmt,maize2_national,coffee_arabica,coffee_total_mg,coffee_conilon')
    args = parser.parse_args()
    device, diag = gpu.require_cuda()
    step = gpu.training_step_check(device)
    print('CUDA', diag['device'], step, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for name in args.scopes.split(','):
        started = time.perf_counter()
        run_scope(name, device)
        print(f'[{name}] done in {time.perf_counter() - started:.0f}s', flush=True)
    save_json(OUT / f'run_{args.scopes.replace(",", "-")[:60]}.json', {'cuda': diag, 'training_step_check': step, 'scopes': args.scopes.split(',')})


if __name__ == '__main__':
    from data.ingest.train_v8 import main as entry
    entry()
