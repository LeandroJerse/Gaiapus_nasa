"""Minimal CPU entry point of the experimental v8 package (no project imports besides yield_model.py)."""
import json
from pathlib import Path

from yield_model import YieldModel

HERE = Path(__file__).resolve().parent


def scopes():
    return json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))['scopes']


def estimate(scope, municipality_code, features):
    """Return {'status', 'predicted_kg_ha', 'warnings', ...}; never raises for invalid user input."""
    known = scopes()
    if scope not in known:
        return {'status': 'invalid_scope', 'message': f'unknown scope {scope!r}; available: {sorted(known)}'}
    model = YieldModel(HERE / 'models' / scope)
    features = {**features, 'municipality_code': municipality_code}  # state fallback of the reference base
    if municipality_code not in model.spec['coverage']:
        return {'status': 'municipality_not_covered', 'message': f'{municipality_code} is outside the {scope} coverage'}
    try:
        horizon = int(features.get('horizon_days'))
    except (TypeError, ValueError):
        return {'status': 'invalid_cutoff', 'message': 'horizon_days is required'}
    if horizon not in model.cutoffs:
        return {'status': 'invalid_cutoff', 'message': f'cutoff {horizon} not supported; supported: {model.cutoffs}'}
    required = model.required_features(horizon)
    absent = [f for f in required if f not in features]
    if absent:
        return {'status': 'inputs_incomplete', 'missing': absent}
    warnings = []
    for name, (low, high) in model.spec['feature_ranges'].items():
        value = features.get(name)
        if name in required and value is not None and value == value and not low <= value <= high and name != 'harvest_year':
            warnings.append({'feature': name, 'value': value, 'training_range': [low, high]})
    return {'status': 'ready', 'scope': scope, 'municipality_code': municipality_code, 'horizon_days': horizon,
            'predicted_kg_ha': round(model.predict(features), 1), 'warnings': warnings,
            'validation_status': model.spec['validation_status'], 'model_status': 'experimental'}
