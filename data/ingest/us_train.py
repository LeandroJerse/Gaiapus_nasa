"""Frozen US research experiment: select on development, predict without targets, score once."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from data.ingest import gpu
from data.ingest.us_data import OUT, RAW, ROOT, PROTOCOL, PROTOCOL_PATH, save_json, sha, validate_prediction_inputs

HISTORY = ['hist_last', 'hist_mean3', 'hist_mean10', 'hist_trend', 'hist_count', 'hist_age', 'year']
BASELINES = ['last', 'mean3', 'trend', 'history_ridge']
MODELS = ROOT / 'data/models/research_us_v1'


def preprocessing():
    return Pipeline([('impute', SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)), ('scale', StandardScaler())])


def county_mae(frame, predictions):
    errors = pd.DataFrame({'fips': frame.fips.to_numpy(), 'error': np.abs(frame.yield_kg_ha.to_numpy(float) - predictions)})
    return float(errors.groupby('fips').error.mean().mean())


def split_development(frame):
    return frame[(frame.year <= 2015) & ~frame.spatial_holdout], frame[frame.year.isin([2017, 2018, 2019, 2020]) & ~frame.spatial_holdout]


def numeric_columns(frame):
    return [c for c in frame.columns if c.startswith('hist_') or c.startswith('w0_') or c.startswith('w1_') or c.startswith('w2_') or c in ('year', 'latitude', 'longitude')]


def verify_files(root, files):
    for name, expected in files.items():
        if not (Path(root) / name).is_file() or sha(Path(root) / name) != expected:
            raise ValueError(f'Frozen input changed: {name}')


def write_score_once(path, result):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def assert_test_unopened(folder):
    if any((Path(folder) / name).exists() for name in ('test_score.json', 'test_consumed.json')):
        raise ValueError('Test already consumed: no retraining or new score in this experiment')


def linear_fit(frame, features, alpha):
    p = Pipeline([('prep', preprocessing()), ('model', Ridge(alpha=alpha))])
    p.fit(frame[features], frame.yield_kg_ha.to_numpy(float))
    return p


def reference_predictions(frame, history_model, fallback):
    predictions = {'history_ridge': np.maximum(0., history_model.predict(frame[HISTORY]))}
    for name, col in [('last', 'hist_last'), ('mean3', 'hist_mean3'), ('trend', 'hist_trend')]:
        predictions[name] = np.maximum(0., frame[col].fillna(fallback).to_numpy(float))
    return predictions


def network(width, hidden, device, seed):
    import torch
    torch.manual_seed(seed)
    layers = []
    for size in hidden:
        layers += [torch.nn.Linear(width, size), torch.nn.ReLU(), torch.nn.Dropout(0.1)]
        width = size
    layers.append(torch.nn.Linear(width, 1))
    return torch.nn.Sequential(*layers).to(device)


def fit_mlp(fit, features, history_model, hidden, device, seeds, epochs=None, validation=None):
    import torch
    prep = preprocessing().fit(fit[features])
    x = torch.as_tensor(prep.transform(fit[features]), dtype=torch.float32, device=device)
    residual = fit.yield_kg_ha.to_numpy(float) - history_model.predict(fit[HISTORY])
    scale = float(np.std(residual)) or 1.
    y = torch.as_tensor(residual / scale, dtype=torch.float32, device=device)
    if validation is not None:
        vx = torch.as_tensor(prep.transform(validation[features]), dtype=torch.float32, device=device)
        vb = history_model.predict(validation[HISTORY])
    states, seed_log, chosen_epoch = [], [], epochs
    for seed in seeds:
        model = network(x.shape[1], hidden, device, seed)
        optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
        generator = torch.Generator(device=device).manual_seed(seed)
        best_score, best_state, best_epoch = float('inf'), None, 1
        start = time.perf_counter()
        torch.cuda.reset_peak_memory_stats(device)
        for epoch in range(1, (epochs or 250) + 1):
            model.train()
            order = torch.randperm(len(x), generator=generator, device=device)
            for index in range(0, len(x), 256):
                batch = order[index:index + 256]
                optimizer.zero_grad(set_to_none=True)
                loss = torch.nn.functional.mse_loss(model(x[batch]).squeeze(1), y[batch])
                loss.backward()
                optimizer.step()
            if validation is not None:
                model.eval()
                with torch.no_grad():
                    pred = np.maximum(0., vb + model(vx).squeeze(1).cpu().numpy() * scale)
                score = county_mae(validation, pred)
                if score < best_score:
                    best_score, best_epoch = score, epoch
                    best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                elif epoch - best_epoch >= 25:
                    break
        if validation is not None:
            model.load_state_dict(best_state)
            chosen_epoch = best_epoch
        torch.cuda.synchronize(device)
        seed_log.append({'seed': seed, 'epochs': int(chosen_epoch), 'elapsed_s': round(time.perf_counter() - start, 3), 'device': str(next(model.parameters()).device), 'peak_vram_mb': round(torch.cuda.max_memory_allocated(device) / 2**20, 2)})
        states.append({k: v.detach().cpu().numpy().copy() for k, v in model.state_dict().items()})
    return {'family': 'mlp', 'features': features, 'preprocessing': prep, 'history': history_model, 'residual_scale': scale, 'states': states, 'hidden': list(hidden), 'epochs': int(chosen_epoch), 'seed_log': seed_log}


def predict_bundle(bundle, frame):
    family = bundle['family']
    if family == 'ridge':
        return np.maximum(0., bundle['model'].predict(frame[bundle['features']]))
    x = bundle['preprocessing'].transform(frame[bundle['features']])
    base = bundle['history'].predict(frame[HISTORY])
    if family == 'hgb':
        return np.maximum(0., base + bundle['model'].predict(x))
    values = []
    for state in bundle['states']:
        h = x.astype(np.float32)
        for i in range(len(bundle['hidden']) + 1):
            layer = i * 3
            h = h @ state[f'{layer}.weight'].T + state[f'{layer}.bias']
            if i < len(bundle['hidden']):
                h = np.maximum(h, 0)
        values.append(h[:, 0])
    return np.maximum(0., base + np.mean(values, axis=0) * bundle['residual_scale'])


def predict_cuda(bundle, frame, device):
    import torch
    x = torch.as_tensor(bundle['preprocessing'].transform(frame[bundle['features']]), dtype=torch.float32, device=device)
    preds = []
    for seed, state in enumerate(bundle['states']):
        model = network(x.shape[1], bundle['hidden'], device, seed)
        model.load_state_dict({k: torch.from_numpy(v).to(device) for k, v in state.items()})
        model.eval()
        with torch.no_grad():
            preds.append(model(x).squeeze(1).cpu().numpy())
    return np.maximum(0., bundle['history'].predict(frame[HISTORY]) + np.mean(preds, axis=0) * bundle['residual_scale'])


def candidate_bundle(kind, parameter, fit, features, history_model, device, validation=None, epochs=None):
    if kind == 'ridge':
        return {'family': kind, 'features': features, 'model': linear_fit(fit, features, parameter)}
    if kind == 'hgb':
        prep = preprocessing().fit(fit[features])
        residual = fit.yield_kg_ha.to_numpy(float) - history_model.predict(fit[HISTORY])
        model = HistGradientBoostingRegressor(max_iter=200, max_leaf_nodes=parameter, min_samples_leaf=20, learning_rate=.05, early_stopping=False, random_state=42).fit(prep.transform(fit[features]), residual)
        return {'family': kind, 'features': features, 'preprocessing': prep, 'model': model, 'history': history_model}
    seeds = [42] if validation is not None else [101, 102, 103]
    return fit_mlp(fit, features, history_model, parameter, device, seeds, validation=validation, epochs=epochs)


def load_frames():
    manifest = json.loads((OUT / 'feature_manifest.json').read_text())
    if manifest['protocol_sha256'] != sha(PROTOCOL_PATH):
        raise ValueError('Protocol changed after features')
    verify_files(OUT, manifest['files'])
    if sha(RAW / 'development.jsonl.gz') != manifest['development_labels_sha256']:
        raise ValueError('Development labels changed')
    frames = {name: pd.read_csv(OUT / f'{name}.csv.gz', dtype={'fips': str}) for name in ['selection', 'final_fit', 'test_inputs']}
    validate_prediction_inputs(frames['test_inputs'])
    if (frames['final_fit'].year > 2020).any() or (frames['selection'].year > 2020).any():
        raise ValueError('Test target in fitting frame')
    return frames


def train_and_freeze():
    assert_test_unopened(OUT)
    device, info = gpu.require_cuda()
    checks = gpu.training_step_check(device)
    import torch
    torch.set_num_threads(2)
    frames = load_frames()
    MODELS.mkdir(parents=True, exist_ok=True)
    code_files = ['data/ingest/us_data.py', 'data/ingest/us_features.py', 'data/ingest/us_train.py', 'data/ingest/gpu.py']
    code_hashes = {p: sha(ROOT / p) for p in code_files}
    feature_hash = sha(OUT / 'feature_manifest.json')
    for scope, (state, crop) in PROTOCOL['scopes'].items():
        scope_out = OUT / scope
        scope_out.mkdir(exist_ok=True)
        freeze_path = scope_out / 'freeze.json'
        if freeze_path.exists():
            frozen = json.loads(freeze_path.read_text())
            verify_files(ROOT, frozen['files'])
            if frozen['code_hashes'] != code_hashes or frozen['feature_manifest_sha256'] != feature_hash:
                raise ValueError('Cannot resume after code or feature changes')
            print(scope, 'already frozen; verified', flush=True)
            continue
        parts = {k: v[(v.state == state) & (v.crop == crop)].copy() for k, v in frames.items()}
        fit, val = split_development(parts['selection'])
        final = parts['final_fit'][~parts['final_fit'].spatial_holdout]
        target = parts['test_inputs']
        if fit.fips.nunique() < 30 or val.year.nunique() < 3 or target.empty:
            raise ValueError(f'Insufficient cohort: {scope}')
        features = numeric_columns(fit)
        h = linear_fit(fit, HISTORY, 10.)
        ref = reference_predictions(val, h, float(fit.yield_kg_ha.mean()))
        scores = [{'model_id': key, 'family': 'reference', 'parameter': None, 'validation_mae': county_mae(val, p)} for key, p in ref.items()]
        winners = {}
        for kind, params in [('ridge', [10., 100.]), ('hgb', [7, 15]), ('mlp', [[32, 16], [64, 32]])]:
            best = None
            for parameter in params:
                bundle = candidate_bundle(kind, parameter, fit, features, h, device, validation=val if kind == 'mlp' else None)
                metric = county_mae(val, predict_bundle(bundle, val))
                row = {'model_id': kind, 'family': kind, 'parameter': parameter, 'epochs': bundle.get('epochs'), 'validation_mae': metric}
                scores.append(row)
                if best is None or metric < best['validation_mae']:
                    best = row
            winners[kind] = best
        selected = min(scores, key=lambda r: r['validation_mae'])['model_id']
        final_history = linear_fit(final, HISTORY, 10.)
        predictions = target[['sample_id', 'state', 'crop', 'fips', 'year', 'spatial_holdout', 'cutoff']].copy()
        for name, p in reference_predictions(target, final_history, float(final.yield_kg_ha.mean())).items():
            predictions[name] = p
        files = {p.relative_to(ROOT).as_posix(): sha(p) for p in [PROTOCOL_PATH, RAW / 'manifest.json', RAW / 'development.jsonl.gz', OUT / 'counties.json', OUT / 'feature_manifest.json', *[OUT / f'{n}.csv.gz' for n in frames]]}
        parity = {}
        reference_path = MODELS / f'{scope}_references.joblib'
        joblib.dump({'history': final_history, 'fallback': float(final.yield_kg_ha.mean()), 'features': HISTORY}, reference_path, compress=3)
        files[reference_path.relative_to(ROOT).as_posix()] = sha(reference_path)
        final_seed_logs = []
        for kind, choice in winners.items():
            bundle = candidate_bundle(kind, choice['parameter'], final, features, final_history, device, epochs=choice.get('epochs'))
            path = MODELS / f'{scope}_{kind}.joblib'
            joblib.dump(bundle, path, compress=3)
            loaded = joblib.load(path)  # Only artifacts generated by this process.
            cpu = predict_bundle(loaded, target)
            expected = predict_cuda(bundle, target, device) if kind == 'mlp' else predict_bundle(bundle, target)
            delta = float(np.max(np.abs(cpu - expected)))
            if delta > .05 or not np.isfinite(cpu).all():
                raise ValueError('CPU parity failed')
            parity[kind] = delta
            predictions[kind] = cpu
            files[path.relative_to(ROOT).as_posix()] = sha(path)
            if kind == 'mlp':
                final_seed_logs = bundle['seed_log']
                weights_path = MODELS / f'{scope}_mlp.pt'
                torch.save({f'seed{i}.{k}': torch.from_numpy(v) for i, st in enumerate(bundle['states']) for k, v in st.items()}, weights_path)
                files[weights_path.relative_to(ROOT).as_posix()] = sha(weights_path)
        prediction_path = scope_out / 'test_predictions.csv'
        predictions.to_csv(prediction_path, index=False)
        files[prediction_path.relative_to(ROOT).as_posix()] = sha(prediction_path)
        freeze = {'scope': scope, 'created_at': datetime.now(timezone.utc).isoformat(), 'selected_model': selected, 'selection': scores, 'best_by_family': winners, 'features': features, 'protocol_sha256': sha(PROTOCOL_PATH), 'code_hashes': code_hashes, 'feature_manifest_sha256': feature_hash, 'files': files, 'cpu_parity_kg_ha': parity, 'gpu': info, 'gpu_step_checks': checks, 'mlp_final_seeds': final_seed_logs, 'sizes': {'selection_fit': len(fit), 'validation': len(val), 'final_fit': len(final), 'test_predictions_without_labels': len(target)}, 'status': 'frozen_research_only'}
        save_json(freeze_path, freeze)
        print(json.dumps({'scope': scope, 'selected': selected, 'validation_mae': round(min(r['validation_mae'] for r in scores), 2), 'parity': parity, 'frozen': True}), flush=True)
    save_json(OUT / 'all_frozen.json', {'protocol_sha256': sha(PROTOCOL_PATH), 'scope_freezes': {scope: sha(OUT / scope / 'freeze.json') for scope in PROTOCOL['scopes']}, 'created_at': datetime.now(timezone.utc).isoformat(), 'test_labels_read': False})


def metrics(frame, prediction):
    truth = frame.yield_kg_ha.to_numpy(float)
    error = np.asarray(prediction) - truth
    m = county_mae(frame, prediction)
    return {'pairs': len(frame), 'counties': int(frame.fips.nunique()), 'mae_kg_ha': m, 'rmse_kg_ha': float(np.sqrt(np.mean(error**2))), 'bias_kg_ha': float(np.mean(error)), 'mae_pct_mean': 100 * m / float(truth.mean()) if truth.mean() else None, 'r2': float(1 - np.sum(error**2) / np.sum((truth - truth.mean())**2)) if len(truth) > 1 and np.std(truth) > 0 else None}


def paired_interval(frame, candidate, reference):
    differences = np.abs(frame[candidate] - frame.yield_kg_ha) - np.abs(frame[reference] - frame.yield_kg_ha)
    by_county = differences.groupby(frame.fips).mean().to_numpy(float)
    rng = np.random.default_rng(42)
    bootstrap = by_county[rng.integers(0, len(by_county), size=(2000, len(by_county)))].mean(axis=1)
    return {'delta_mae_kg_ha': float(by_county.mean()), 'ci95': [float(x) for x in np.quantile(bootstrap, [.025, .975])], 'family_control_upper': float(np.quantile(bootstrap, 1 - .05 / (5 * 4))), 'method': 'paired county-cluster bootstrap, fixed observed test years'}


def score_once():
    score_path = OUT / 'test_score.json'
    assert_test_unopened(OUT)
    all_frozen = json.loads((OUT / 'all_frozen.json').read_text())
    if all_frozen['protocol_sha256'] != sha(PROTOCOL_PATH):
        raise ValueError('Protocol changed')
    freezes = {}
    for scope, expected in all_frozen['scope_freezes'].items():
        path = OUT / scope / 'freeze.json'
        if sha(path) != expected:
            raise ValueError('Scope freeze changed')
        f = json.loads(path.read_text())
        verify_files(ROOT, f['files'])
        verify_files(ROOT, f['code_hashes'])
        if f['feature_manifest_sha256'] != sha(OUT / 'feature_manifest.json'):
            raise ValueError('Feature manifest changed')
        freezes[scope] = f
    # Only this stage opens the quarantined target values, after every scope predicts.
    raw_manifest = json.loads((RAW / 'manifest.json').read_text())
    if sha(RAW / 'sealed_test.jsonl.gz') != raw_manifest['files']['sealed_test.jsonl.gz']:
        raise ValueError('Test labels changed since collection')
    write_score_once(OUT / 'test_consumed.json', {'opened_at': datetime.now(timezone.utc).isoformat(), 'all_frozen_sha256': sha(OUT / 'all_frozen.json'), 'protocol_sha256': sha(PROTOCOL_PATH), 'rule': 'Even if scoring crashes, no retraining. Technical recovery must retain these exact predictions and protocol.'})
    labels = pd.read_json(RAW / 'sealed_test.jsonl.gz', lines=True, dtype={'fips': str})
    if labels.duplicated(['fips', 'crop', 'year']).any() or not labels.year.isin([2022, 2023, 2024]).all():
        raise ValueError('Invalid test label identities')
    output = {'scored_at': datetime.now(timezone.utc).isoformat(), 'protocol_sha256': sha(PROTOCOL_PATH), 'all_frozen_sha256': sha(OUT / 'all_frozen.json'), 'test_labels_sha256': sha(RAW / 'sealed_test.jsonl.gz'), 'evaluation_kind': 'retrospective_with_documented_partial_prior_label_exposure', 'operational_approved': False, 'scopes': {}}
    for scope, frozen in freezes.items():
        p = pd.read_csv(OUT / scope / 'test_predictions.csv', dtype={'fips': str})
        merged = p.merge(labels[['fips', 'crop', 'year', 'yield_kg_ha']], on=['fips', 'crop', 'year'], how='left', validate='one_to_one')
        valid = merged.dropna(subset=['yield_kg_ha']).copy()
        winner = frozen['selected_model']
        cohorts = {'seen_counties': valid[~valid.spatial_holdout], 'spatial_holdout': valid[valid.spatial_holdout]}
        result = {'selected_on_validation': winner, 'predicted_pairs': len(p), 'observed_pairs': len(valid), 'missing_labels': int(merged.yield_kg_ha.isna().sum()), 'subgroups': {}}
        for group, frame in cohorts.items():
            if frame.empty:
                result['subgroups'][group] = {'status': 'unavailable'}
                continue
            result['subgroups'][group] = {'metrics': {name: metrics(frame, frame[name].to_numpy()) for name in BASELINES + ['ridge', 'hgb', 'mlp']}, 'comparisons': {name: paired_interval(frame, winner, name) for name in BASELINES}, 'by_year': {str(year): {name: metrics(part, part[name].to_numpy()) for name in BASELINES + ['ridge', 'hgb', 'mlp']} for year, part in frame.groupby('year')}}
        passed = winner not in BASELINES
        for group, frame in cohorts.items():
            passed &= len(frame) >= 30
            if frame.empty:
                passed = False
                continue
            wm = county_mae(frame, frame[winner].to_numpy())
            for ref in BASELINES:
                rm = county_mae(frame, frame[ref].to_numpy())
                passed &= wm <= rm * (.95 if group == 'seen_counties' else 1.)
                if group == 'seen_counties':
                    passed &= result['subgroups'][group]['comparisons'][ref]['family_control_upper'] < 0
        result['status'] = 'retrospective_gain_confirmed_research_only' if passed else ('reference_selected' if winner in BASELINES else 'gain_not_confirmed')
        output['scopes'][scope] = result
    write_score_once(score_path, output)
    print(json.dumps({scope: {'selected': r['selected_on_validation'], 'status': r['status'], 'observed_pairs': r['observed_pairs']} for scope, r in output['scopes'].items()}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['train', 'score'])
    args = parser.parse_args()
    train_and_freeze() if args.action == 'train' else score_once()
