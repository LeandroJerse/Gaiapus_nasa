"""Frozen 'history' reference per served crop for the v9 prospective registry.

The prospective target is a harvest whose PAM does not exist yet, so every published harvest
(<= 2024 in the local cohorts) is development data here. One Ridge per cutoff on trend and
published lags (train_v8.History) is fitted on all municipalities of each cohort and exported
in the portable gaiapus-v8-v1 format (base history, no correction).
"""
import json

from data.ingest import train_v7 as v7
from data.ingest.train_v8 import History
from data.ingest.water_balance_run import ROOT, save_json, sha

OUT = ROOT / 'dist/prospective_v9/references'


def fit(name):
    import pandas as pd
    frame, kind = v7.load_cohort(name)
    data = pd.concat([frame, v7.load_test(name)], ignore_index=True)
    base, _ = v7.feature_sets(kind)
    model = History(base).fit(data)
    if model.fallback:
        raise ValueError(f'{name}: cutoffs {sorted(model.fallback)} without usable history features')
    spec = {'format': 'gaiapus-v8-v1', 'scope': 'national', 'crop': name, 'cutoffs': sorted(int(h) for h in model.models),
            'base': {'kind': 'history', 'history_by_horizon': {str(h): v7.linear_spec(f, m) for h, (f, m) in model.models.items()}},
            'shrink': 0.0, 'residual': None, 'coverage': sorted(data.municipality_code.unique().tolist()), 'feature_ranges': {},
            'validation_status': 'reference', 'trained_harvests': [int(data.harvest_year.min()), int(data.harvest_year.max())],
            'training_rows': len(data)}
    return spec, data, model


def main():
    import numpy as np
    from data.serving.yield_model import YieldModel
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in v7.COHORTS:
        spec, data, model = fit(name)
        path = OUT / f'history_{name}.json'
        save_json(path, spec)
        loaded = YieldModel(path.with_suffix(''))
        sample = data[data.harvest_year == data.harvest_year.max()].head(200)
        expected = model.predict(sample)
        got = np.array([loaded.predict({k: (None if v != v else v) for k, v in r.items()}) for r in sample.to_dict('records')])
        parity = float(np.max(np.abs(got - expected)))
        if parity > 1e-6:
            raise AssertionError(f'{name}: exported reference differs by {parity}')
        manifest[name] = {'file': path.name, 'sha256': sha(path), 'trained_harvests': spec['trained_harvests'],
                          'training_rows': spec['training_rows'], 'parity_max_abs_kg_ha': parity}
        print(name, manifest[name], flush=True)
    save_json(OUT / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    from data.ingest import prospective_refs_v9 as module
    module.main()
