"""Export the frozen v8 choices as portable experimental artifacts and package them for CPU.

Each scope in data/frozen/v8_frozen_choices.json is refitted (CUDA for MLPs) on the pool
harvests <= 2023 outside the spatial holdout, the same fit scored once on harvest 2025, and
written in the 'gaiapus-v8-v1' format (JSON + state_dict, no pickles). The artifact is
reloaded with data/serving/yield_model.py on CPU and compared with the in-process
predictions; real cases (Sorriso, Uberlandia, MG coffee) and coverage/feature ranges go in
the package. The live job keeps its v7 models: nothing here changes the serving catalog.
"""
import json
import shutil
import zipfile
from datetime import datetime, timezone

from data.ingest import gpu
from data.ingest import train_v7 as v7
from data.ingest.evaluate_2025_v8 import FROZEN, scope_mask, training_frame
from data.serving.package_models import hgb_spec
from data.ingest.train_v6 import PerHorizon
from data.ingest.train_v8 import MLP_SPEC, MODELS, REF_COLUMNS, SCOPES, History, feature_sets, reference_predictor
from data.ingest.water_balance_run import ROOT, save_json, sha
from data.serving.yield_model import YieldModel

DIST = ROOT / 'dist/gaiapus-models-v8'
ZIP = ROOT / 'dist/release/gaiapus-models-v8-experimental.zip'
# Sorriso and Uberlandia; Patrocinio and Tres Pontas (MG coffee); Linhares and Sao Mateus (ES conilon).
CASES = {'soy': ['5107925', '3170206'], 'maize2': ['5107925', '3170206'], 'coffee_arabica': ['3148004', '3169406', '3170206'],
         'coffee_total_mg': ['3148004', '3169406', '3170206'], 'coffee_conilon': ['3203205', '3204906']}
TOLERANCE = {'ridge': 1e-6, 'hgb': 1e-6, 'none': 1e-9, 'mlp': 0.05}  # kg/ha; MLP: float32 CPU vs CUDA kernels


def fit_artifact(name, entry, train, kind, device):
    """Fit the frozen choice on `train`; return (spec, weights or None, in-process predictor)."""
    import numpy as np
    import pandas as pd
    choice = entry['choice']
    base_cols, sets = feature_sets(kind)
    cutoffs = sorted(int(h) for h in train.horizon_days.unique())
    mean = float(train.yield_kg_ha.mean())
    if choice['base'] == 'history':
        history = History(base_cols).fit(train)
        if history.fallback:
            raise ValueError(f'{name}: cutoffs {sorted(history.fallback)} have no usable history feature; not exportable as Ridge')
        base_spec = {'kind': 'history', 'history_by_horizon': {str(h): v7.linear_spec(f, m) for h, (f, m) in history.models.items()}}
        predict_base = history.predict
    elif choice['base'] == 'mean':
        base_spec = {'kind': 'mean', 'value_kg_ha': mean}
        predict_base = lambda rows: np.full(len(rows), mean)
    else:
        column = REF_COLUMNS[choice['base']]
        predict_base = reference_predictor(column, train)
        base_spec = {'kind': 'reference', 'column': column, 'fallback_kg_ha': predict_base.pool_mean,
                     'fallback_by_state': predict_base.state_mean}
    spec = {'format': 'gaiapus-v8-v1', 'scope': name, 'crop': entry.get('crop') or SCOPES[name]['crop'], 'cutoffs': cutoffs, 'base': base_spec,
            'shrink': float(choice['shrink']), 'residual': None}
    if choice['learner'] == 'none' or float(choice['shrink']) == 0:
        return spec, None, predict_base
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - predict_base(train), index=train.index)
    columns = sets[choice['features']]
    if choice['learner'] in ('ridge', 'hgb'):
        params = float(choice['params']) if choice['learner'] == 'ridge' else choice['params']
        fitted = PerHorizon(choice['learner'], params, columns).fit(train, residual)
        export = v7.linear_spec if choice['learner'] == 'ridge' else hgb_spec
        spec['residual'] = {'learner': choice['learner'], 'params': params, 'feature_set': choice['features'],
                            'by_horizon': {str(h): export(f, m) for h, (f, m) in fitted.models.items()}}
        return spec, None, lambda rows: predict_base(rows) + spec['shrink'] * fitted.predict(rows)
    config = {**choice['params'], 'hidden': tuple(choice['params']['hidden'])}
    model = v7.GpuMLP(config, columns, device, MLP_SPEC['batch'], False).fit(train, residual, max(int(choice['epochs']), 1),
                                                                           seeds=tuple(MLP_SPEC['final_seeds']))
    imputer, scaler = model.transformer.steps[0][1], model.transformer.steps[1][1]
    spec['residual'] = {'learner': 'mlp', 'feature_set': choice['features'], 'features': model.features,
                        'imputer_median': imputer.statistics_.tolist(), 'scaler_mean': scaler.mean_.tolist(),
                        'scaler_scale': scaler.scale_.tolist(), 'residual_scale': model.scale, 'hidden': list(config['hidden']),
                        'dropout': config['dropout'], 'weight_decay': config['weight_decay'], 'learning_rate': config['learning_rate'],
                        'epochs': model.epochs, 'seeds': [s['seed'] for s in model.seed_log], 'loss': 'smooth_l1'}
    weights = {f'seed{i}.{k}': v.detach().cpu() for i, net in enumerate(model.networks) for k, v in net.state_dict().items()}
    return spec, weights, lambda rows: predict_base(rows) + spec['shrink'] * model.predict(rows)


def clean(value):
    return None if value is None or value != value else (float(value) if isinstance(value, (int, float)) else value)


def export_all():
    import numpy as np
    import torch
    device, diag = gpu.require_cuda()
    frozen = json.loads(FROZEN.read_text(encoding='utf-8'))
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / 'models').mkdir(parents=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    manifest = {'built_at': datetime.now(timezone.utc).isoformat(), 'status': 'experimental', 'cuda_training': diag['device'],
                'frozen_choices_sha256': sha(FROZEN), 'training': 'pool harvests <= 2023 outside the spatial holdout (retrospective sources)',
                'live_job': 'unchanged (v7 catalog); v8 artifacts are not wired into serving', 'scopes': {}}
    cases = []
    for name, entry in frozen['choices'].items():
        scope = SCOPES[name]
        crop = scope['crop']
        kind = 'coffee' if crop.startswith('coffee') else 'annual'
        mask = scope_mask(name)
        frame = training_frame(crop)
        pool = frame if entry['pool'] == 'national' else frame[mask(frame)]
        train = pool[~pool.spatial_holdout]
        spec, weights, predict = fit_artifact(name, entry, train, kind, device)
        scoped = frame[mask(frame)]
        spec['coverage'] = sorted(scoped.municipality_code.unique().tolist())
        model_features = YieldModel.__new__(YieldModel)
        model_features.spec, model_features.format = spec, spec['format']
        needed = sorted({f for h in spec['cutoffs'] for f in model_features._v8_required(h)})
        spec['feature_ranges'] = {f: [float(train[f].min()), float(train[f].max())] for f in needed if f in train and train[f].notna().any()}
        spec['validation_status'] = entry['development_status']
        stem = DIST / 'models' / name
        save_json(stem.with_suffix('.json'), spec)
        if weights is not None:
            torch.save(weights, stem.with_suffix('.pt'))
        for suffix in ('.json', '.pt'):
            if stem.with_suffix(suffix).exists():
                shutil.copy2(stem.with_suffix(suffix), MODELS / f'{name}{suffix}')
        loaded = YieldModel(stem)
        check = scoped[scoped.harvest_year == scoped.harvest_year.max()]
        expected = predict(check)
        reloaded = np.array([loaded.predict({k: clean(v) for k, v in row.items()}) for row in check.to_dict('records')])
        difference = float(np.max(np.abs(reloaded - expected)))
        tolerance = TOLERANCE[spec['residual']['learner'] if spec['residual'] else 'none']
        if difference > tolerance:
            raise AssertionError(f'{name}: CPU artifact differs from the CUDA fit by {difference} kg/ha (tolerance {tolerance})')
        chosen = [c for c in CASES[crop] if c in set(check.municipality_code)] or sorted(check.municipality_code.unique())[:2]
        for code in chosen:
            rows = check[check.municipality_code == code]
            for row in rows.to_dict('records'):
                features = {f: clean(row.get(f)) for f in [*loaded.required_features(int(row['horizon_days'])), 'horizon_days']}
                cases.append({'scope': name, 'municipality_code': code, 'harvest_year': int(row['harvest_year']),
                              'horizon_days': int(row['horizon_days']), 'features': features,
                              'expected_kg_ha': loaded.predict(features), 'observed_kg_ha': float(row['yield_kg_ha'])})
        manifest['scopes'][name] = {'crop': crop, 'pool': entry['pool'], 'choice': {k: entry['choice'][k] for k in ('base', 'features', 'learner', 'params', 'shrink', 'epochs')},
                                    'development_status': entry['development_status'], 'parity_rows': len(check),
                                    'parity_max_abs_kg_ha': difference, 'parity_tolerance_kg_ha': tolerance,
                                    'municipalities': len(spec['coverage']), 'cutoffs': spec['cutoffs']}
        print(name, manifest['scopes'][name]['choice'], f'parity {difference:.2e}', flush=True)
    save_json(DIST / 'cases.json', cases)
    for helper in ('data/serving/yield_model.py', 'data/serving/v8_package/serve_v8.py', 'data/serving/v8_package/validate_cpu.py',
                   'data/serving/v8_package/README.md'):
        shutil.copy2(ROOT / helper, DIST / helper.rsplit('/', 1)[1])
    manifest['files'] = {p.relative_to(DIST).as_posix(): sha(p) for p in sorted(DIST.rglob('*')) if p.is_file()}
    save_json(DIST / 'manifest.json', manifest)
    ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(DIST.rglob('*')):
            if path.is_file():
                archive.write(path, f'gaiapus-models-v8/{path.relative_to(DIST).as_posix()}')
    save_json(ZIP.with_suffix('.sha256.json'), {'file': ZIP.name, 'sha256': sha(ZIP)})
    print('package', ZIP, sha(ZIP), flush=True)
    return manifest


if __name__ == '__main__':
    from data.ingest import export_v8 as module
    module.export_all()
