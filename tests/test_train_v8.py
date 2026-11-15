import unittest


def cohort(years=range(2003, 2022), municipalities=30, seed=0):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(municipalities):
        state = '31' if m % 2 else '51'
        history = {}
        for year in years:
            shock = rng.normal()
            value = 3000 + 30 * (year - 2003) + 250 * shock + rng.normal(scale=30)
            history[year] = value
            past = [history[year - k] for k in (2, 3, 4) if year - k in history]
            for horizon in (30, 60):
                rows.append({'sample_id': f'{state}{m:05d}:soy:{year}', 'municipality_code': f'{state}{m:05d}', 'state_code': state,
                             'harvest_year': year, 'horizon_days': horizon, 'spatial_holdout': m % 7 == 0, 'yield_kg_ha': value,
                             'yield_lag2to4_mean_kg_ha': sum(past) / len(past) if past else None, 'yield_lag_count': len(past),
                             'ref_lag_mean_kg_ha': sum(past) / len(past) if past else None, 'rain_sum_mm': shock * (horizon >= 60) + rng.normal(scale=0.1)})
    return pd.DataFrame(rows)


class TrainV8Tests(unittest.TestCase):
    def test_missing_reference_uses_the_same_state_mean_of_the_fold_training(self):
        import pandas as pd
        from data.ingest.train_v8 import reference_predictor
        fit = pd.DataFrame({'state_code': ['31', '31', '51'], 'yield_kg_ha': [3000.0, 3200.0, 4000.0]})
        rows = pd.DataFrame({'state_code': ['31', '51', '41', '31'], 'ref_lag_mean_kg_ha': [None, None, None, 2500.0]})
        pred = reference_predictor('ref_lag_mean_kg_ha', fit)(rows)
        self.assertEqual(list(pred), [3100.0, 4000.0, (3000 + 3200 + 4000) / 3, 2500.0])

    def test_history_falls_back_to_the_cutoff_mean_without_usable_features(self):
        import pandas as pd
        from data.ingest.train_v8 import History
        data = cohort(years=range(2003, 2012))
        early = data.assign(yield_lag2to4_mean_kg_ha=None, yield_lag_count=0, harvest_year=2012)  # one year, no lags
        mixed = pd.concat([data[data.horizon_days == 30], early[early.horizon_days == 60]], ignore_index=True)
        model = History(['harvest_year', 'yield_lag2to4_mean_kg_ha', 'yield_lag_count']).fit(mixed)
        pred = model.predict(mixed)
        sixty = (mixed.horizon_days == 60).to_numpy()
        self.assertTrue((pred[sixty] == early[early.horizon_days == 60].yield_kg_ha.mean()).all())
        self.assertGreater(pred[~sixty].std(), 0)

    def test_harvest_level_scores_weight_cutoffs_and_states_equally(self):
        import pandas as pd
        from data.ingest.train_v8 import harvest_scores
        rows = pd.DataFrame({'sample_id': ['a', 'a', 'b', 'c'], 'state_code': ['31', '31', '51', '51'], 'yield_kg_ha': [100, 100, 100, 100]})
        pred = [110, 130, 100, 140]
        self.assertEqual(harvest_scores(rows, pred, 'state50'), (20 + 20) / 2)
        self.assertAlmostEqual(harvest_scores(rows, pred, 'harvest'), (20 + 0 + 40) / 3)

    def test_fold_and_inner_splits_respect_the_publication_gap(self):
        from data.ingest.train_v8 import fold_split, inner_split
        data = cohort()
        mask = lambda f: f.state_code.isin(['31', '51'])
        train, target = fold_split(data, data, 2019, mask)
        self.assertEqual(int(train.harvest_year.max()), 2017)
        self.assertFalse(train.spatial_holdout.any())
        self.assertEqual(set(target.harvest_year), {2019})
        self.assertTrue(target.spatial_holdout.any())  # held-out municipalities are scored, never fitted
        fit, val, years = inner_split(train)
        self.assertEqual(list(years), [2016, 2017])
        self.assertEqual(int(fit.harvest_year.max()), 2014)
        self.assertTrue(set(fit.sample_id).isdisjoint(val.sample_id))
        # every cutoff of a harvest stays in the same partition
        self.assertTrue((train.groupby('sample_id').horizon_days.nunique() == 2).all())

    def test_base_rules_use_only_the_fit_rows(self):
        from data.ingest.train_v8 import base_fit
        data = cohort()
        fit, target = data[data.harvest_year <= 2015], data[data.harvest_year == 2019].copy()
        predict = base_fit('ref_lag_mean', fit, ['harvest_year', 'yield_lag2to4_mean_kg_ha', 'yield_lag_count'])
        target.loc[target.index[0], 'ref_lag_mean_kg_ha'] = None
        values = predict(target)
        state = target.state_code.iloc[0]  # missing reference: mean of the same state in the fit rows, never the target
        self.assertAlmostEqual(values[0], fit[fit.state_code == state].yield_kg_ha.mean())
        self.assertEqual(values[1], target.ref_lag_mean_kg_ha.iloc[1])

    def test_inner_mlp_runs_on_cuda_and_returns_validation_corrections(self):
        import numpy as np
        import pandas as pd
        import torch
        if not torch.cuda.is_available():
            self.skipTest('CUDA not available')
        from data.ingest.gpu import require_cuda
        from data.ingest.train_v8 import base_fit, inner_split, mlp_inner
        data = cohort()
        fit, val, _ = inner_split(data[data.harvest_year <= 2017])
        base = base_fit('ref_lag_mean', fit, [])
        residual = pd.Series(fit.yield_kg_ha.to_numpy(float) - base(fit), index=fit.index)
        config = {'hidden': (32, 16), 'dropout': 0.0, 'weight_decay': 1e-4, 'learning_rate': 3e-3}
        epoch, pred = mlp_inner(config, ['rain_sum_mm', 'harvest_year'], fit, val, residual, val.yield_kg_ha.to_numpy(float) - base(val),
                                require_cuda()[0], max_epochs=30)
        self.assertEqual(len(pred), len(val))
        self.assertTrue(1 <= epoch <= 30 and np.isfinite(pred).all())

    def test_shrinkage_zero_returns_the_base(self):
        from data.ingest.train_v8 import fit_predict
        data = cohort()
        train, target = data[data.harvest_year <= 2017], data[data.harvest_year == 2019]
        choice = {'base': 'ref_lag_mean', 'features': 'F1_weather', 'learner': 'ridge', 'params': 10, 'shrink': 0.0, 'epochs': None}
        prediction, _ = fit_predict(choice, train, target, 'annual', None)
        self.assertTrue((prediction == target.ref_lag_mean_kg_ha.fillna(train.yield_kg_ha.mean()).to_numpy()).all())


if __name__ == '__main__':
    unittest.main()
