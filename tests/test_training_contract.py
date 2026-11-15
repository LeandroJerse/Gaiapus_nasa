import importlib.util
import unittest


class TrainingContractTests(unittest.TestCase):
    def test_features_exclude_outcomes_and_holdout_never_trains(self):
        self.assertIsNotNone(importlib.util.find_spec('data.ingest.training_baseline'))
        from data.ingest.training_baseline import FEATURES, training_mask
        forbidden = {'yield_kg_ha', 'production_t', 'area_harvested_ha', 'area_planted_ha', 'sample_id', 'municipality_code'}
        self.assertFalse(set(FEATURES) & forbidden)
        rows = [{'split_time': 'train', 'spatial_holdout': False},
                {'split_time': 'train', 'spatial_holdout': True},
                {'split_time': 'test', 'spatial_holdout': False}]
        self.assertEqual([training_mask(r) for r in rows], [True, False, False])


if __name__ == '__main__':
    unittest.main()
