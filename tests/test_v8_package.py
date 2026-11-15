"""The gaiapus-v8-v1 format and the CPU entry point of the experimental package."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def linear(features, coef, intercept):
    n = len(features)
    return {'features': features, 'imputer_median': [0.0] * n, 'scaler_mean': [0.0] * n, 'scaler_scale': [1.0] * n,
            'coef': coef, 'intercept': intercept}


def spec(residual=None, base=None):
    return {'format': 'gaiapus-v8-v1', 'scope': 'toy', 'crop': 'soy', 'cutoffs': [60],
            'base': base or {'kind': 'reference', 'column': 'ref_lag_mean_kg_ha', 'fallback_kg_ha': 3000.0},
            'shrink': 0.5, 'residual': residual, 'coverage': ['5107925'], 'feature_ranges': {'rain_mm': [0.0, 900.0]},
            'validation_status': 'experimental'}


class V8FormatTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.folder)

    def write(self, payload, name='toy'):
        (self.folder / f'{name}.json').write_text(json.dumps(payload), encoding='utf-8')
        return self.folder / name

    def test_reference_base_plus_shrunk_ridge(self):
        from data.serving.yield_model import YieldModel
        model = YieldModel(self.write(spec({'learner': 'ridge', 'by_horizon': {'60': linear(['rain_mm'], [2.0], 10.0)}})))
        self.assertEqual(model.required_features(60), ['rain_mm', 'ref_lag_mean_kg_ha'])
        row = {'horizon_days': 60, 'ref_lag_mean_kg_ha': 3500.0, 'rain_mm': 100.0}
        self.assertAlmostEqual(model.predict(row), 3500 + 0.5 * (2 * 100 + 10))
        self.assertAlmostEqual(model.predict(dict(row, ref_lag_mean_kg_ha=None)), 3000 + 0.5 * 210)
        with self.assertRaises(ValueError):
            model.predict(dict(row, horizon_days=75))

    def test_history_base_without_correction(self):
        from data.serving.yield_model import YieldModel
        base = {'kind': 'history', 'history_by_horizon': {'60': linear(['harvest_year'], [50.0], -97000.0)}}
        model = YieldModel(self.write(spec(None, base)))
        self.assertAlmostEqual(model.predict({'horizon_days': 60, 'harvest_year': 2020}), 50 * 2020 - 97000)

    def test_mlp_correction_matches_torch(self):
        import torch
        from data.serving.yield_model import YieldModel
        torch.manual_seed(0)
        net = torch.nn.Sequential(torch.nn.Linear(2, 4), torch.nn.ReLU(), torch.nn.Linear(4, 1))
        torch.save({f'seed0.{k}': v for k, v in net.state_dict().items()}, self.folder / 'toy.pt')
        residual = {'learner': 'mlp', 'features': ['rain_mm', 'horizon_days'], 'imputer_median': [0.0, 0.0], 'scaler_mean': [100.0, 60.0],
                    'scaler_scale': [50.0, 15.0], 'residual_scale': 200.0, 'hidden': [4], 'dropout': 0.0, 'seeds': [101]}
        model = YieldModel(self.write(spec(residual)))
        with torch.no_grad():
            raw = net(torch.tensor([[(150 - 100) / 50, 0.0]])).item()
        self.assertAlmostEqual(model.predict({'horizon_days': 60, 'ref_lag_mean_kg_ha': 3500.0, 'rain_mm': 150.0}), 3500 + 0.5 * raw * 200, places=4)


class ServeV8Tests(unittest.TestCase):
    def test_statuses(self):
        folder = Path(tempfile.mkdtemp())
        try:
            (folder / 'models').mkdir()
            payload = spec({'learner': 'ridge', 'by_horizon': {'60': linear(['rain_mm'], [2.0], 10.0)}})
            (folder / 'models' / 'toy.json').write_text(json.dumps(payload), encoding='utf-8')
            (folder / 'manifest.json').write_text(json.dumps({'scopes': {'toy': {}}}), encoding='utf-8')
            for name in ('serve_v8.py',):
                shutil.copy2(ROOT / 'data/serving/v8_package' / name, folder / name)
            shutil.copy2(ROOT / 'data/serving/yield_model.py', folder / 'yield_model.py')
            sys.path.insert(0, str(folder))
            try:
                import serve_v8
                row = {'horizon_days': 60, 'ref_lag_mean_kg_ha': 3500.0, 'rain_mm': 100.0}
                self.assertEqual(serve_v8.estimate('toy', '5107925', row)['status'], 'ready')
                self.assertEqual(serve_v8.estimate('nope', '5107925', row)['status'], 'invalid_scope')
                self.assertEqual(serve_v8.estimate('toy', '0000000', row)['status'], 'municipality_not_covered')
                self.assertEqual(serve_v8.estimate('toy', '5107925', dict(row, horizon_days=7))['status'], 'invalid_cutoff')
                self.assertEqual(serve_v8.estimate('toy', '5107925', {'horizon_days': 60, 'rain_mm': 1.0})['status'], 'inputs_incomplete')
                warned = serve_v8.estimate('toy', '5107925', dict(row, rain_mm=5000.0))
                self.assertEqual(warned['warnings'][0]['feature'], 'rain_mm')
            finally:
                sys.path.remove(str(folder))
                sys.modules.pop('serve_v8', None)
                sys.modules.pop('yield_model', None)
        finally:
            shutil.rmtree(folder)


if __name__ == '__main__':
    unittest.main()
