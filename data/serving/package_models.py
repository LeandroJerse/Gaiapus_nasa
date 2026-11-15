"""Package the model chosen in validation for every crop, with parity checks, into dist/.

For each v7 cohort the champion of the frozen rule is exported in a portable format
(Ridge/HGB as JSON, MLP as JSON + state_dict, references as JSON) from the last
walk-forward fold (harvest 2021, fitted on harvests up to 2019). Soy in MG + MT gets the
guide 30 regional model. Every artifact is reloaded with data/serving/yield_model.py and
compared with the walk-forward predictions it must reproduce.
"""
import json
import shutil
from datetime import datetime, timezone

from data.ingest import train_v7 as v7
from data.ingest.water_balance_run import ROOT, save_json, sha
from data.serving.yield_model import YieldModel

DIST = ROOT / 'dist/gaiapus-models-v7'
FINAL_YEAR = 2021
REGIONAL = ROOT / 'data/frozen/regional_rule_soy_mg_mt_g30.json'
REFERENCE_COLUMNS = {'ref_lag_mean': 'ref_lag_mean_kg_ha', 'ref_last_available': 'ref_last_available_kg_ha',
                     'ref_same_phase': 'ref_same_phase_kg_ha', 'ref_biennial_mean': 'ref_biennial_mean_kg_ha',
                     'ref_cycle_mean': 'ref_cycle_mean_kg_ha'}


def hgb_spec(features, pipeline):
    prep, model = pipeline.steps[0][1], pipeline.steps[-1][1]
    imputer, scaler = prep.steps[0][1], prep.steps[1][1]
    if getattr(model, 'is_categorical_', None) is not None and model.is_categorical_ is not None and model.is_categorical_.any():
        raise ValueError('Categorical splits are not supported by the portable format')
    trees = []
    for iteration in model._predictors:
        nodes = iteration[0].nodes
        trees.append({'feature_idx': nodes['feature_idx'].tolist(), 'threshold': nodes['num_threshold'].tolist(),
                      'missing_go_to_left': nodes['missing_go_to_left'].astype(bool).tolist(), 'left': nodes['left'].tolist(),
                      'right': nodes['right'].tolist(), 'is_leaf': nodes['is_leaf'].astype(bool).tolist(), 'value': nodes['value'].tolist()})
    return {'features': list(features), 'imputer_median': imputer.statistics_.tolist(), 'scaler_mean': scaler.mean_.tolist(),
            'scaler_scale': scaler.scale_.tolist(), 'baseline': float(model._baseline_prediction.ravel()[0]), 'trees': trees}


def export(name, sources, model, folder):
    """Write the artifact for `sources+model` of cohort `name`; return its path stem."""
    frame, kind = v7.load_cohort(name)
    base, sets = v7.feature_sets(kind)
    stem = folder / f'{name}_{sources}_{model}_{FINAL_YEAR}'
    if model == 'reference':
        train, _ = v7.fold(frame, FINAL_YEAR)
        save_json(stem.with_suffix('.json'), {'format': 'gaiapus-reference-v1', 'column': REFERENCE_COLUMNS[sources],
                                              'fallback_kg_ha': float(train.yield_kg_ha.mean()),
                                              'cutoffs': sorted(int(h) for h in frame.horizon_days.unique()),
                                              'fallback_basis': 'mean yield of the training harvests when the reference is missing'})
        return stem
    if model in ('ridge', 'mlp'):
        source = v7.MODELS / f'{name}_{sources}_{model}_{FINAL_YEAR}'
        for suffix in ('.json', '.pt'):
            if source.with_suffix(suffix).exists():
                shutil.copy2(source.with_suffix(suffix), stem.with_suffix(suffix))
        return stem
    info = json.loads((v7.OUT / f'validation/{name}/cohort.json').read_text(encoding='utf-8'))
    params = info['hyperparameters'][f'{sources}|hgb']
    train, _ = v7.fold(frame, FINAL_YEAR)
    history = v7.History(base).fit(train)
    import pandas as pd
    residual = pd.Series(train.yield_kg_ha.to_numpy(float) - history.predict(train), index=train.index)
    fitted = v7.PerHorizon('hgb', params, sets[sources]).fit(train, residual)
    save_json(stem.with_suffix('.json'), {
        'format': 'gaiapus-hgb-v1', 'params': params,
        'history_by_horizon': {str(h): v7.linear_spec(f, m) for h, (f, m) in history.models.items()},
        'residual_by_horizon': {str(h): hgb_spec(f, m) for h, (f, m) in fitted.models.items()}})
    return stem


def parity(name, sources, model, stem, states=None):
    """Max absolute difference between the reloaded artifact and the walk-forward predictions for 2021."""
    import pandas as pd
    frame, _ = v7.load_cohort(name)
    predictions = pd.read_parquet(v7.OUT / f'predictions_{name}.parquet')
    rows = predictions[(predictions.sources == sources) & (predictions.model == model) & (predictions.harvest_year == FINAL_YEAR)]
    if states:
        rows = rows[rows.state_code.isin(states)]
    lookup = frame.set_index(['sample_id', 'horizon_days'])
    loaded = YieldModel(stem)
    differences = []
    for record in rows.itertuples():
        row = lookup.loc[(record.sample_id, record.horizon_days)].to_dict()
        row['horizon_days'] = int(record.horizon_days)
        row = {k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()}
        differences.append(abs(loaded.predict(row) - record.predicted_kg_ha))
    return {'rows_checked': len(differences), 'max_abs_diff_kg_ha': max(differences) if differences else None}


def main():
    if DIST.exists():
        shutil.rmtree(DIST / 'models', ignore_errors=True)
    folder = DIST / 'models'
    folder.mkdir(parents=True, exist_ok=True)
    regional = json.loads(REGIONAL.read_text(encoding='utf-8'))
    entries = []
    for name in v7.COHORTS:
        info = json.loads((v7.OUT / f'validation/{name}/cohort.json').read_text(encoding='utf-8'))
        sources, model = info['champion'].split('+')
        scopes = [('national', sources, model, None)]
        if name == 'soy' and regional['decision']['selected'] != 'history+history':
            r_sources, r_model = regional['decision']['selected'].split('+')
            scopes.append(('MG+MT', r_sources, r_model, ['31', '51']))
        for scope, s, m, states in scopes:
            stem = export(name, s, m, folder)
            check = parity(name, s, m, stem, states)
            tolerance = 0.5 if m == 'mlp' else 1e-6  # GPU-trained MLP reloaded on CPU
            if check['max_abs_diff_kg_ha'] is None or check['max_abs_diff_kg_ha'] > tolerance:
                raise ValueError(f'Parity failed for {name} {scope}: {check}')
            files = {p.name: sha(p) for p in sorted(folder.glob(stem.name + '.*'))}
            entries.append({'cohort': name, 'scope': scope, 'states': states, 'model': f'{s}+{m}', 'artifact': stem.name,
                            'files': files, 'parity': {**check, 'tolerance_kg_ha': tolerance},
                            'validation_status': {'published': 'validated'}.get(info['status'], info['status'])})
            print(name, scope, f'{s}+{m}', check, flush=True)
    import numpy
    import sklearn
    import torch
    save_json(DIST / 'models_manifest.json', {
        'created_at': datetime.now(timezone.utc).isoformat(), 'version': 'research_v7', 'fold': f'harvest {FINAL_YEAR}, fitted on harvests up to {FINAL_YEAR - 2}',
        'entries': entries, 'regional_rule': {'file': str(REGIONAL.relative_to(ROOT)), 'sha256': sha(REGIONAL)},
        'runtime': {'python': '>=3.11', 'numpy': numpy.__version__, 'torch_cpu': torch.__version__.split('+')[0],
                    'note': 'Ridge, HGB and references need only numpy; MLP also needs torch (CPU is enough)'},
        'trained_with': {'sklearn': sklearn.__version__, 'torch': torch.__version__},
        'operational_release': 'not_released', 'test_2022_2024_evaluated': False})


if __name__ == '__main__':
    from data.serving.package_models import main as entry
    entry()
