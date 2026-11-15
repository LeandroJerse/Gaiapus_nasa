"""Validate the unpacked v8 package on CPU only: hashes, real cases and invalid inputs.

Run from the package folder:  CUDA_VISIBLE_DEVICES= python -I validate_cpu.py
"""
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '')


def preprocessing_checks(cases):
    """Feature order, median imputation and the state fallback of the reference base, per scope."""
    import random
    from yield_model import YieldModel
    out = {}
    for case in {c['scope']: c for c in cases}.values():
        model = YieldModel(HERE / 'models' / case['scope'])
        row = dict(case['features'], municipality_code=case['municipality_code'])
        base = model.predict(row)
        items = list(row.items())
        random.Random(0).shuffle(items)
        out[f'{case["scope"]}:feature_order_independent'] = abs(model.predict(dict(items)) - base) < 1e-9
        residual = model.spec['residual']
        if residual and residual['learner'] == 'mlp':
            name = residual['features'][0]
            median = residual['imputer_median'][0]
        elif residual:
            spec = residual['by_horizon'][str(int(row['horizon_days']))]
            name, median = spec['features'][0], spec['imputer_median'][0]
        else:
            name = None
        if name and name != 'horizon_days' and name in row:
            same = abs(model.predict(dict(row, **{name: None})) - model.predict(dict(row, **{name: median}))) < 1e-6
            out[f'{case["scope"]}:missing_value_uses_training_median'] = same
        if model.spec['base']['kind'] == 'reference':
            column = model.spec['base']['column']
            state = case['municipality_code'][:2]
            fallback = model.spec['base']['fallback_by_state'].get(state, model.spec['base']['fallback_kg_ha'])
            out[f'{case["scope"]}:reference_fallback_by_state'] = abs(
                model.predict(dict(row, **{column: None})) - model.predict(dict(row, **{column: fallback}))) < 1e-6
    return out


def main():
    import torch
    from serve_v8 import estimate, scopes
    report = {'cuda_available': torch.cuda.is_available(), 'python': sys.version.split()[0], 'torch': torch.__version__}
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    bad = [name for name, digest in manifest['files'].items() if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest]
    report['hash_mismatches'] = bad
    cases = json.loads((HERE / 'cases.json').read_text(encoding='utf-8'))
    worst = 0.0
    for case in cases:
        out = estimate(case['scope'], case['municipality_code'], case['features'])
        if out['status'] != 'ready':
            raise AssertionError(f'case not ready: {case["scope"]} {case["municipality_code"]} {out}')
        worst = max(worst, abs(out['predicted_kg_ha'] - case['expected_kg_ha']))
    report['cases'] = len(cases)
    report['cases_max_abs_kg_ha'] = worst  # predictions are rounded to 0.1 kg/ha
    first = cases[0]
    scope, code, features = first['scope'], first['municipality_code'], first['features']
    missing_name = next(k for k in features if k != 'horizon_days')
    outside = dict(features, **{missing_name: 1e6})
    checks = {
        'invalid_scope': estimate('banana', code, features)['status'] == 'invalid_scope',
        'municipality_not_covered': estimate(scope, '9999999', features)['status'] == 'municipality_not_covered',
        'invalid_cutoff': estimate(scope, code, dict(features, horizon_days=7))['status'] == 'invalid_cutoff',
        'inputs_incomplete': estimate(scope, code, {k: v for k, v in features.items() if k != missing_name})['status'] == 'inputs_incomplete',
        'out_of_range_warns': bool(estimate(scope, code, outside).get('warnings')),
    }
    report['invalid_input_checks'] = checks
    report['preprocessing_checks'] = preprocessing_checks(cases)
    report['scopes'] = sorted(scopes())
    report['passed'] = (not bad and worst <= 0.051 and all(checks.values()) and all(report['preprocessing_checks'].values())
                        and not report['cuda_available'])
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
