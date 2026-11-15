"""v9 candidates for the prospective registry (protocol data/frozen/training_v9_protocol.json).

For every crop x scope: nested selection with train_v8.select on all published development rows
(internal validation 2023-2024, internal fit <= 2021), then the best candidate of each learner
family (ridge, hgb, mlp) is refitted on all rows, exported in the portable format, checked on
CPU against the in-process fit and appended to the prospective protocol as a candidate. Approval
can only come from the prospective scores; nothing here changes the served catalog.
"""
import argparse
import json

from data.ingest import gpu
from data.ingest import train_v7 as v7
from data.ingest.cohort_v8 import validate
from data.ingest.export_v8 import TOLERANCE, clean, fit_artifact
from data.ingest.train_v8 import select
from data.ingest.water_balance_run import ROOT, save_json, sha, write_csv

PROTOCOL = json.loads((ROOT / 'data/frozen/training_v9_protocol.json').read_text(encoding='utf-8'))
OUT = ROOT / 'dist/prospective_v9/candidates'
LOG = ROOT / 'data/processed/training_v9'
FAMILIES = ('ridge', 'hgb', 'mlp')


def development(crop):
    """All published rows; rows whose lag was not yet released at their decision (cutoff + 1) are excluded, not repaired."""
    import pandas as pd
    from datetime import timedelta
    from data.ingest.audit_v8 import lag_available
    frame, kind = v7.load_cohort(crop)
    data = pd.concat([frame, v7.load_test(crop)], ignore_index=True)
    if kind == 'annual':
        decision = pd.to_datetime(data.feature_cutoff) + timedelta(days=1)
        late = [count > 0 and not lag_available(int(year) - 2, when.date())
                for count, year, when in zip(data.yield_lag_count, data.harvest_year, decision)]
        excluded = int(sum(late))
        data = data[~pd.Series(late, index=data.index)].reset_index(drop=True)
    else:
        excluded = 0
    EXCLUDED[crop] = excluded
    return validate(data, crop, last_year=2024), kind


EXCLUDED = {}


def parse(candidate):
    params = candidate['params']
    if isinstance(params, str):
        params = json.loads(params) if params not in ('', 'null') else None
    return {'base': candidate['base'], 'features': candidate['features'], 'learner': candidate['learner'],
            'params': params, 'shrink': float(candidate['shrink']), 'epochs': candidate['epochs']}


def run(crop, scope, states, pool, device):
    import numpy as np
    from data.serving.yield_model import YieldModel
    data, kind = development(crop)
    mask = (lambda f: f.state_code.isin(states)) if states else (lambda f: f.state_code == f.state_code)
    train = data if pool == 'national' else data[mask(data)]
    weighting = 'state50' if states and len(states) > 1 else 'harvest'
    log = []
    best, info = select(train, mask, kind, weighting, device, log=log, log_limit=None)
    base_only = min((c for c in log if c['learner'] == 'none'), key=lambda c: c['score'])
    tag = f'{crop}_{scope.replace("+", "")}'
    rows, entries = [], []
    scoped = data[mask(data)]
    check = scoped[scoped.harvest_year == scoped.harvest_year.max()]
    for family in FAMILIES:
        family_best = min((c for c in log if c['learner'] == family), key=lambda c: c['score'])
        choice = parse(family_best)
        name = f'{tag}_{family}'
        spec, weights, predict = fit_artifact(name, {'choice': choice, 'crop': crop}, train, kind, device)
        spec['coverage'] = sorted(scoped.municipality_code.unique().tolist())
        spec['validation_status'] = 'prospective_candidate'
        spec['selection'] = {'inner_score': family_best['score'], 'base_only_inner_score': base_only['score'], **info,
                             'weighting': weighting, 'pool': pool, 'training_rows': len(train),
                             'rows_excluded_lag_unpublished': EXCLUDED.get(crop, 0)}
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / name
        save_json(stem.with_suffix('.json'), spec)
        if weights is not None:
            import torch
            torch.save(weights, stem.with_suffix('.pt'))
        loaded = YieldModel(stem)
        expected = predict(check)
        got = np.array([loaded.predict({**{k: clean(v) for k, v in r.items()}}) for r in check.to_dict('records')])
        parity = float(np.max(np.abs(got - expected)))
        tolerance = TOLERANCE[spec['residual']['learner'] if spec['residual'] else 'none']
        if parity > tolerance:
            raise AssertionError(f'{name}: CPU artifact differs by {parity}')
        files = {p.name: sha(p) for p in (stem.with_suffix('.json'), stem.with_suffix('.pt')) if p.exists()}
        family_name = f'{choice["base"]} + {choice["features"]} + {family}'
        entries.append({'model_id': f'v9:{crop}:{scope}:{family}', 'crop': crop, 'role': 'candidate', 'scope_states': list(states) if states else None,
                        'artifact': stem.with_suffix('.json').relative_to(ROOT).as_posix(), 'sha256': files[f'{name}.json'],
                        'weights_sha256': files.get(f'{name}.pt'), 'family': family_name})
        rows.append({'crop': crop, 'scope': scope, 'family': family, **choice, 'params': json.dumps(choice['params'], default=list),
                     'inner_score': round(family_best['score'], 1), 'base_only_inner_score': round(base_only['score'], 1),
                     'overall_best': family_best['score'] == best['score'], 'parity_kg_ha': parity})
        print(name, family_name, f"inner {family_best['score']:.1f} (base only {base_only['score']:.1f}) parity {parity:.1e}", flush=True)
    return rows, entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--crops', default=','.join(PROTOCOL['scopes']))
    parser.add_argument('--register', action='store_true', help='append the exported candidates to the prospective protocol')
    args = parser.parse_args()
    device, _ = gpu.require_cuda()
    gpu.training_step_check(device)
    LOG.mkdir(parents=True, exist_ok=True)
    for crop in args.crops.split(','):
        if (LOG / f'candidates_{crop}.json').exists():
            print(crop, 'already trained; kept', flush=True)
            continue
        rows, entries = [], []
        for scope, states, pool in PROTOCOL['scopes'][crop]:
            r, e = run(crop, scope, states, pool, device)
            rows += r
            entries += e
        write_csv(LOG / f'selection_{crop}.csv', [{**r, 'rows_excluded_lag_unpublished': EXCLUDED.get(crop, 0)} for r in rows])
        save_json(LOG / f'candidates_{crop}.json', entries)
    if args.register:
        from data.serving.prospective_registry import add_model
        for crop in PROTOCOL['scopes']:
            for entry in json.loads((LOG / f'candidates_{crop}.json').read_text(encoding='utf-8')):
                add_model(entry, 'v9 candidate (data/frozen/training_v9_protocol.json), frozen at registration')


if __name__ == '__main__':
    from data.ingest.train_v9 import main as entry
    entry()
