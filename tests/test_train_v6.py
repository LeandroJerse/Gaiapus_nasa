import unittest


def cohort(years=range(2002, 2022), municipalities=40, horizons=(30, 60, 90), seed=0):
    """Yield = municipal level + trend + a weather effect that only shows after day 60."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    level = rng.normal(3000, 300, municipalities)
    rows = []
    for m in range(municipalities):
        history = {}
        for year in years:
            shock = rng.normal()
            value = level[m] + 50 * (year - 2002) + 300 * shock + rng.normal(scale=20)
            history[year] = value
            past = [history[year - lag] for lag in (2, 3, 4) if year - lag in history]
            for horizon in horizons:
                rows.append({'sample_id': f'{m}:{year}', 'municipality_code': f'51{m:05d}', 'state_code': '51',
                             'harvest_year': year, 'horizon_days': horizon, 'spatial_holdout': m % 5 == 0, 'mvp': m < 2,
                             'feature_cutoff': f'{year}-01-01', 'yield_kg_ha': value,
                             'yield_lag2to4_mean_kg_ha': sum(past) / len(past) if past else None, 'yield_lag_count': len(past),
                             'signal': shock if horizon >= 60 else rng.normal(), 'noise': rng.normal()})
    return pd.DataFrame(rows)


class TrainV6Tests(unittest.TestCase):
    def test_fold_respects_label_lag_and_spatial_holdout(self):
        from data.ingest.train_v6 import fold
        train, target = fold(cohort(), 2019)
        self.assertEqual(train.harvest_year.max(), 2017)
        self.assertFalse(train.spatial_holdout.any())
        self.assertEqual(set(target.harvest_year), {2019})
        self.assertTrue(target.spatial_holdout.any())

    def test_baseline_is_identical_across_horizons_of_a_harvest(self):
        from data.ingest.train_v6 import Baseline, fold
        train, target = fold(cohort(), 2019)
        predicted = target.assign(p=Baseline().fit(train).predict(target))
        self.assertTrue((predicted.groupby('sample_id').p.nunique() == 1).all())

    def test_residual_models_use_the_signal_only_where_it_exists(self):
        import numpy as np
        import pandas as pd
        from data.ingest.train_v6 import BASE, Baseline, PerHorizon, PooledMLP, fold
        frame = cohort()
        train, target = fold(frame, 2019)
        base = Baseline().fit(train)
        residual = pd.Series(train.yield_kg_ha - base.predict(train), index=train.index)
        features = [*BASE, 'signal', 'noise']
        truth = target.yield_kg_ha.to_numpy()
        base_error = np.abs(base.predict(target) - truth)
        for model in (PerHorizon('ridge', 10.0, features).fit(train, residual),
                      PooledMLP({'hidden': (32, 16), 'dropout': 0.0}, features).fit(train, residual, 40, seeds=(1, 2))):
            error = np.abs(base.predict(target) + model.predict(target) - truth)
            late, early = (target.horizon_days >= 60).to_numpy(), (target.horizon_days == 30).to_numpy()
            self.assertLess(error[late].mean(), 0.6 * base_error[late].mean())
            self.assertGreater(error[early].mean(), 0.8 * base_error[early].mean())

    def test_pooled_mlp_is_deterministic(self):
        import numpy as np
        import pandas as pd
        from data.ingest.train_v6 import BASE, Baseline, PooledMLP, fold
        train, target = fold(cohort(), 2019)
        residual = pd.Series(train.yield_kg_ha - Baseline().fit(train).predict(train), index=train.index)
        runs = [PooledMLP({'hidden': (32, 16), 'dropout': 0.2}, [*BASE, 'signal']).fit(train, residual, 5, seeds=(3,)).predict(target)
                for _ in range(2)]
        np.testing.assert_allclose(runs[0], runs[1], rtol=1e-5)

    def test_saved_artifacts_load_in_a_fresh_process(self):
        import subprocess
        import sys
        from pathlib import Path
        folder = Path(__file__).resolve().parents[1] / 'data/models/research_v6'
        paths = sorted(folder.glob('*_validation_2021.joblib'))
        if not paths:
            self.skipTest('No trained v6 artifacts on this machine')
        for path in (paths[0], paths[-1]):
            result = subprocess.run([sys.executable, '-c', 'import sys, joblib; b = joblib.load(sys.argv[1]); '
                                     'print(type(b["baseline"]).__module__)', str(path)],
                                    capture_output=True, text=True, cwd=folder.parents[2])
            self.assertEqual(result.returncode, 0, result.stderr[-400:])
            self.assertEqual(result.stdout.strip(), 'data.ingest.train_v6')

    def test_champion_and_horizon_rules(self):
        from data.ingest.train_v6 import choose_champion, supported_horizons
        row = lambda model, t, s, share=1.0: {'sources': 'weather', 'model': model, 'temporal': t, 'spatiotemporal': s, 'seed_win_share': share}
        self.assertEqual(choose_champion([row('baseline', 300, 310), row('hgb', 290, 330), row('mlp', 280, 335)])['model'], 'hgb')
        self.assertEqual(choose_champion([row('baseline', 300, 310), row('hgb', 290, 330), row('mlp', 280, 320)])['model'], 'mlp')
        self.assertEqual(choose_champion([row('baseline', 300, 310), row('mlp', 280, 300, share=0.6)])['model'], 'baseline')
        self.assertEqual(supported_horizons({30: 310.0, 60: 290.0, 90: 250.0}, {30: 300.0, 60: 300.0, 90: 300.0}), [60, 90])


if __name__ == '__main__':
    unittest.main()
