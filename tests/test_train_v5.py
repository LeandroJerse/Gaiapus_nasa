import unittest


def frame(years=range(2002, 2017), municipalities=30, seed=0, trend=60.0):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    for year in years:
        for m in range(municipalities):
            a, b = rng.normal(size=2)
            rows.append({'sample_id': f'{m}:{year}', 'municipality_code': f'51{m:05d}', 'harvest_year': year,
                         'a': a, 'b': b, 'gap': None if m % 7 == 0 else a,
                         'yield_kg_ha': 2500 + trend * (year - 2002) + 300 * a * (b > 0) - 200 * b + rng.normal(scale=30)})
    return pd.DataFrame(rows)


def summary(model, temporal, spatial, share=1.0):
    return {'sources': 'weather', 'model': model, 'seed_win_share': share,
            'mae': {'temporal_validation': temporal, 'spatiotemporal_validation': spatial}}


class TrainV5Tests(unittest.TestCase):
    def test_inner_split_holds_the_last_training_years(self):
        from data.ingest.train_v5 import inner_split
        fit, stop = inner_split(frame())
        self.assertEqual(sorted(stop.harvest_year.unique()), [2014, 2015, 2016])
        self.assertEqual(fit.harvest_year.max(), 2013)

    def test_usable_drops_constant_and_empty_columns_from_training_only(self):
        from data.ingest.train_v5 import usable
        data = frame()
        data['constant'] = 1.0
        data['empty'] = None
        self.assertEqual(usable(data, ['a', 'constant', 'empty', 'b']), ['a', 'b'])

    def test_torch_mlp_is_deterministic_imputes_and_beats_the_mean(self):
        import numpy as np
        from data.ingest.train_v5 import TorchMLP
        train, later = frame(), frame(years=range(2019, 2022), seed=1)
        config = {'hidden': (16, 8), 'weight_decay': 1e-4, 'dropout': 0.0}
        first = TorchMLP(config, ['a', 'b', 'gap'], residual=True).fit(train, 60, seeds=(1, 2))
        again = TorchMLP(config, ['a', 'b', 'gap'], residual=True).fit(train, 60, seeds=(1, 2))
        np.testing.assert_allclose(first.predict(later), again.predict(later), rtol=1e-5)
        self.assertEqual(first.predict_seeds(later).shape, (2, len(later)))
        truth = later.yield_kg_ha.to_numpy()
        self.assertLess(np.abs(first.predict(later) - truth).mean(), 0.5 * np.abs(train.yield_kg_ha.mean() - truth).mean())

    def test_early_stopping_reads_only_training_rows(self):
        from data.ingest.train_v5 import TorchMLP
        config = {'hidden': (16, 8), 'weight_decay': 1e-4, 'dropout': 0.2}
        epoch, score = TorchMLP(config, ['a', 'b'], residual=False).best_epoch(frame(), seed=3, max_epochs=40, patience=10)
        self.assertTrue(1 <= epoch <= 40 and score > 0)

    def test_champion_is_a_baseline_unless_the_mlp_wins_everywhere_with_stable_seeds(self):
        from data.ingest.train_v5 import choose_champion
        ridge, hgb = summary('ridge', 300, 400), summary('hgb', 320, 380)
        self.assertEqual(choose_champion([ridge, hgb, summary('mlp', 290, 410)])['model'], 'ridge')
        self.assertEqual(choose_champion([ridge, hgb, summary('mlp', 290, 390, share=0.4)])['model'], 'ridge')
        self.assertEqual(choose_champion([ridge, hgb, summary('mlp', 290, 390, share=0.8)])['model'], 'mlp')
        self.assertEqual(choose_champion([ridge, hgb, summary('mlp_trend_residual', 310, 300)])['model'], 'ridge')

    def test_sealed_test_scoring_refuses_a_second_run(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        from data.ingest import train_v5
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'manifest.json').write_text(json.dumps({'test_evaluated': True}), encoding='utf-8')
            with mock.patch.object(train_v5, 'MODELS', Path(folder)), self.assertRaises(SystemExit):
                train_v5.evaluate_test()


if __name__ == '__main__':
    unittest.main()
