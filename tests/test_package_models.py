import json
import tempfile
import unittest
from pathlib import Path


class PortableModelTests(unittest.TestCase):
    def test_hgb_json_reproduces_scikit_learn_including_missing_values(self):
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.pipeline import make_pipeline
        from data.ingest.train_v6 import transformer
        from data.serving.package_models import hgb_spec
        from data.serving.yield_model import _hgb
        rng = np.random.default_rng(0)
        frame = pd.DataFrame({'a': rng.normal(size=400), 'b': rng.normal(size=400), 'c': rng.normal(size=400)})
        frame.loc[::7, 'b'] = np.nan
        y = 3 * frame.a - np.where(frame.b.isna(), 2.0, frame.b) + rng.normal(scale=0.1, size=400)
        model = make_pipeline(transformer(), HistGradientBoostingRegressor(max_iter=60, max_leaf_nodes=7, loss='absolute_error',
                                                                           random_state=42, early_stopping=False)).fit(frame, y)
        spec = json.loads(json.dumps(hgb_spec(['a', 'b', 'c'], model)))
        expected = model.predict(frame)
        for i in range(0, 400, 13):
            row = {k: (None if pd.isna(v) else float(v)) for k, v in frame.iloc[i].items()}
            row = {k: (float('nan') if v is None else v) for k, v in row.items()}
            self.assertAlmostEqual(_hgb(spec, row), expected[i], places=9)

    def test_reference_format_uses_the_column_and_a_declared_fallback(self):
        from data.serving.yield_model import YieldModel
        with tempfile.TemporaryDirectory() as folder:
            spec = {'format': 'gaiapus-reference-v1', 'column': 'ref_lag_mean_kg_ha', 'fallback_kg_ha': 1500.0, 'cutoffs': [30, 60]}
            (Path(folder) / 'r.json').write_text(json.dumps(spec), encoding='utf-8')
            model = YieldModel(Path(folder) / 'r')
            self.assertEqual(model.predict({'horizon_days': 60, 'ref_lag_mean_kg_ha': 1800.0}), 1800.0)
            self.assertEqual(model.predict({'horizon_days': 30, 'ref_lag_mean_kg_ha': None}), 1500.0)
            with self.assertRaises(ValueError):
                model.predict({'horizon_days': 45, 'ref_lag_mean_kg_ha': 1800.0})
            with self.assertRaises(ValueError):
                model.predict({'horizon_days': 60})


if __name__ == '__main__':
    unittest.main()
