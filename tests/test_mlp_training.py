import unittest


def synthetic_frame(years=range(2002, 2017), codes=('3100001', '3100002', '5100001', '5100002'), seed=0):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    for year in years:
        for code in codes:
            a, b = rng.normal(size=2)
            rows.append({'sample_id': f'{code}-{year}', 'municipality_code': code, 'harvest_year': year,
                         'spatial_holdout': False, 'a': a, 'b': b, 'gap': None if year % 5 == 0 else a,
                         'yield_kg_ha': 3000 + 400 * a - 250 * b + rng.normal(scale=20)})
    return pd.DataFrame(rows)


class MlpTrainingTests(unittest.TestCase):
    def test_inner_split_is_temporal_and_inside_training(self):
        from data.ingest.train_mlp_v4 import inner_temporal_split
        frame = synthetic_frame()
        fit, stop = inner_temporal_split(frame, years=3)
        self.assertEqual(sorted(stop.harvest_year.unique()), [2014, 2015, 2016])
        self.assertLess(fit.harvest_year.max(), stop.harvest_year.min())
        self.assertEqual(len(fit) + len(stop), len(frame))

    def test_reserved_test_and_embargo_rows_are_rejected(self):
        from data.ingest.train_mlp_v4 import check_reserved
        check_reserved(synthetic_frame())
        with self.assertRaises(ValueError):
            check_reserved(synthetic_frame(years=range(2020, 2023)))

    def test_training_rows_must_end_in_2016_without_holdout(self):
        from data.ingest.train_mlp_v4 import check_training_isolation
        frame = synthetic_frame()
        check_training_isolation(frame)
        with self.assertRaises(ValueError):
            check_training_isolation(synthetic_frame(years=range(2010, 2018)))
        frame.loc[0, 'spatial_holdout'] = True
        with self.assertRaises(ValueError):
            check_training_isolation(frame)

    def test_bundle_is_deterministic_learns_and_imputes(self):
        import numpy as np
        from data.ingest.train_mlp_v4 import fit_bundle, predict_bundle
        train = synthetic_frame()
        later = synthetic_frame(years=range(2019, 2022), seed=1)
        config = {'hidden': (8,), 'alpha': 0.1}
        first = fit_bundle(train, ['a', 'b', 'gap'], config, epochs=150, seeds=(1, 2))
        again = fit_bundle(train, ['a', 'b', 'gap'], config, epochs=150, seeds=(1, 2))
        per_seed, mean = predict_bundle(first, later)
        self.assertEqual(per_seed.shape, (2, len(later)))
        np.testing.assert_allclose(mean, predict_bundle(again, later)[1])
        self.assertTrue(np.isfinite(mean).all())
        mlp_mae = np.abs(mean - later.yield_kg_ha).mean()
        mean_mae = np.abs(train.yield_kg_ha.mean() - later.yield_kg_ha).mean()
        self.assertLess(mlp_mae, 0.5 * mean_mae)

    def test_trend_residual_bundle_extrapolates_the_trend(self):
        import numpy as np
        from data.ingest.train_mlp_v4 import fit_bundle, predict_bundle
        train = synthetic_frame()
        later = synthetic_frame(years=range(2019, 2022), seed=1)
        for frame in (train, later):
            frame['yield_kg_ha'] += 80 * (frame.harvest_year - 2002)
        config = {'hidden': (8,), 'alpha': 0.1}
        plain = predict_bundle(fit_bundle(train, ['a', 'b'], config, epochs=150, seeds=(1, 2)), later)[1]
        residual = predict_bundle(fit_bundle(train, ['a', 'b'], config, epochs=150, seeds=(1, 2), trend=True), later)[1]
        truth = later.yield_kg_ha.to_numpy()
        self.assertLess(np.abs(residual - truth).mean(), 0.5 * np.abs(plain - truth).mean())

    def test_early_stopping_uses_only_given_rows(self):
        from data.ingest.train_mlp_v4 import best_epoch
        train = synthetic_frame()
        epoch, score = best_epoch(train, ['a', 'b'], {'hidden': (8,), 'alpha': 0.1}, seed=3, max_epochs=120, patience=30)
        self.assertTrue(1 <= epoch <= 120)
        self.assertGreater(score, 0)

    def test_promotion_requires_both_scopes_and_seed_stability(self):
        from data.ingest.train_mlp_v4 import promote
        self.assertTrue(promote(temporal=(300, 350), spatial=(400, 450), seed_win_share=0.9))
        self.assertFalse(promote(temporal=(300, 350), spatial=(460, 450), seed_win_share=0.9))
        self.assertFalse(promote(temporal=(360, 350), spatial=(400, 450), seed_win_share=0.9))
        self.assertFalse(promote(temporal=(300, 350), spatial=(400, 450), seed_win_share=0.5))

    def test_cluster_bootstrap_resamples_whole_groups(self):
        from data.ingest.train_mlp_v4 import cluster_bootstrap_ci
        low, high = cluster_bootstrap_ci([5.0] * 6, ['a', 'a', 'b', 'b', 'c', 'c'])
        self.assertEqual((low, high), (5.0, 5.0))
        low, high = cluster_bootstrap_ci([-10, -10, 10, 10], ['a', 'a', 'b', 'b'])
        self.assertEqual((low, high), (-10.0, 10.0))


if __name__ == '__main__':
    unittest.main()
