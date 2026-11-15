import unittest
from unittest import mock


def cohort(years=range(2003, 2022), municipalities=40, horizons=(30, 60, 90), seed=0):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    level = rng.normal(3000, 300, municipalities)
    rows = []
    for m in range(municipalities):
        history = {}
        for year in years:
            shock = rng.normal()
            value = level[m] + 40 * (year - 2003) + 300 * shock + rng.normal(scale=20)
            history[year] = value
            past = [history[year - k] for k in (2, 3, 4) if year - k in history]
            for horizon in horizons:
                rows.append({'sample_id': f'{m}:{year}', 'municipality_code': f'41{m:05d}', 'harvest_year': year, 'horizon_days': horizon,
                             'spatial_holdout': m % 5 == 0, 'yield_kg_ha': value, 'yield_lag2to4_mean_kg_ha': sum(past) / len(past) if past else None,
                             'yield_lag_count': len(past), 'signal': shock if horizon >= 60 else rng.normal(), 'noise': rng.normal()})
    return pd.DataFrame(rows)


def cuda_or_skip(test):
    import torch
    if not torch.cuda.is_available():
        test.skipTest('CUDA not available on this machine')
    from data.ingest.gpu import require_cuda
    return require_cuda()[0]


class GpuTrainingTests(unittest.TestCase):
    def test_missing_cuda_stops_with_a_diagnostic(self):
        import torch
        from data.ingest import gpu
        with mock.patch.object(torch.cuda, 'is_available', return_value=False):
            with self.assertRaises(gpu.CudaUnavailable) as raised:
                gpu.require_cuda()
        self.assertIn('CUDA is required', str(raised.exception))

    def test_forward_backward_and_step_run_on_cuda(self):
        from data.ingest.gpu import training_step_check
        checks = training_step_check(cuda_or_skip(self))
        self.assertTrue(all(checks.values()), checks)

    def test_gpu_mlp_trains_on_cuda_learns_and_is_deterministic(self):
        import numpy as np
        import pandas as pd
        from data.ingest.train_v7 import ANNUAL_BASE, GpuMLP, History, fold
        device = cuda_or_skip(self)
        train, target = fold(cohort(), 2019)
        history = History(ANNUAL_BASE).fit(train)
        residual = pd.Series(train.yield_kg_ha - history.predict(train), index=train.index)
        config = {'hidden': (32, 16), 'dropout': 0.0, 'weight_decay': 1e-4}
        runs = [GpuMLP(config, [*ANNUAL_BASE, 'signal', 'noise'], device, batch=256).fit(train, residual, 40, seeds=(1, 2)) for _ in range(2)]
        np.testing.assert_allclose(runs[0].predict(target), runs[1].predict(target), rtol=1e-5, atol=1e-3)
        self.assertTrue(all(s['device'].startswith('cuda') and s['peak_vram_mb'] > 0 for s in runs[0].seed_log))
        truth = target.yield_kg_ha.to_numpy()
        late = (target.horizon_days >= 60).to_numpy()
        error = np.abs(history.predict(target) + runs[0].predict(target) - truth)
        self.assertLess(error[late].mean(), 0.6 * np.abs(history.predict(target) - truth)[late].mean())

    def test_portable_export_loads_in_a_fresh_cpu_process_with_parity(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        import pandas as pd
        from data.ingest.train_v7 import ANNUAL_BASE, GpuMLP, History, fold
        device = cuda_or_skip(self)
        train, target = fold(cohort(), 2019)
        history = History(ANNUAL_BASE).fit(train)
        residual = pd.Series(train.yield_kg_ha - history.predict(train), index=train.index)
        mlp = GpuMLP({'hidden': (32, 16), 'dropout': 0.2, 'weight_decay': 1e-4}, [*ANNUAL_BASE, 'signal'], device, batch=256).fit(train, residual, 20, seeds=(1, 2))
        row = target.iloc[5]
        expected = float(history.predict(target.iloc[[5]])[0] + mlp.predict(target.iloc[[5]])[0])
        with tempfile.TemporaryDirectory() as folder:
            mlp.export(Path(folder), 'model', history)
            payload = {k: (None if pd.isna(v) else (v.item() if hasattr(v, 'item') else v)) for k, v in row.items()}
            code = ('import json,sys; from data.serving.yield_model import YieldModel; '
                    'm = YieldModel(sys.argv[1]); print(m.predict(json.loads(sys.argv[2])))')
            result = subprocess.run([sys.executable, '-c', code, str(Path(folder) / 'model'), json.dumps(payload)],
                                    capture_output=True, text=True, env={**__import__('os').environ, 'CUDA_VISIBLE_DEVICES': ''})
            self.assertEqual(result.returncode, 0, result.stderr[-500:])
            self.assertAlmostEqual(float(result.stdout.strip()), expected, delta=0.5)

    def test_loader_rejects_bad_cutoff_and_missing_features(self):
        import json
        import tempfile
        from pathlib import Path
        from data.serving.yield_model import YieldModel
        spec = {'format': 'gaiapus-ridge-v1',
                'history_by_horizon': {'60': {'features': ['a'], 'imputer_median': [0.0], 'scaler_mean': [0.0], 'scaler_scale': [1.0], 'coef': [2.0], 'intercept': 100.0}},
                'residual_by_horizon': {'60': {'features': ['b'], 'imputer_median': [0.0], 'scaler_mean': [0.0], 'scaler_scale': [1.0], 'coef': [1.0], 'intercept': 0.0}}}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'm.json').write_text(json.dumps(spec), encoding='utf-8')
            model = YieldModel(Path(folder) / 'm')
            self.assertEqual(model.predict({'horizon_days': 60, 'a': 3.0, 'b': 4.0}), 110.0)
            with self.assertRaises(ValueError):
                model.predict({'horizon_days': 45, 'a': 3.0, 'b': 4.0})
            with self.assertRaises(ValueError):
                model.predict({'horizon_days': 60, 'a': 3.0})

    def test_rules(self):
        from data.ingest.train_v7 import choose_champion, publication_status
        row = lambda model, t, s, share=1.0, sources='weather': {'sources': sources, 'model': model, 'temporal': t, 'spatiotemporal': s, 'seed_win_share': share}
        history = row('history', 300, 310, sources='history')
        self.assertEqual(choose_champion([history, row('ridge', 290, 300), row('mlp', 280, 290, 0.8)])['model'], 'mlp')
        self.assertEqual(choose_champion([history, row('ridge', 290, 300), row('mlp', 280, 305, 0.8)])['model'], 'ridge')
        self.assertEqual(publication_status(False, history, history, -1), 'unavailable')
        self.assertEqual(publication_status(True, row('reference', 250, 260, sources='ref_same_phase'), history, -1), 'reference_only')
        self.assertEqual(publication_status(True, row('ridge', 290, 300), history, -5.0), 'validated')
        self.assertEqual(publication_status(True, row('ridge', 290, 300), history, 2.0), 'experimental')
        self.assertEqual(publication_status(True, row('ridge', 290, 400), history, -5.0), 'experimental')


if __name__ == '__main__':
    unittest.main()
