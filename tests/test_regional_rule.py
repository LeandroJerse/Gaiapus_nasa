import unittest


def predictions(gain, n_municipalities=60, spatial_penalty=0.0, seed=0):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(n_municipalities):
        held = m % 5 == 0
        for year in (2019, 2020, 2021):
            truth = 3000 + rng.normal(scale=200)
            national = truth + rng.normal(scale=300)
            regional = truth + rng.normal(scale=max(10.0, 300 - gain)) + (spatial_penalty if held else 0)
            for horizon in (60, 90):
                base = {'sample_id': f'{m}:{year}', 'municipality_code': f'51{m:05d}', 'state_code': '51', 'harvest_year': year,
                        'horizon_days': horizon, 'spatial_holdout': held, 'yield_kg_ha': truth}
                rows.append({**base, 'sources': 'site', 'model': 'ridge', 'predicted_kg_ha': national})
                rows.append({**base, 'sources': 'hydro', 'model': 'mlp', 'predicted_kg_ha': regional})
    return pd.DataFrame(rows)


class RegionalRuleTests(unittest.TestCase):
    def test_switches_only_with_clear_gain_enough_harvests_and_spatial_check(self):
        from data.ingest.regional_rule import candidate_table, decide
        clear = decide(candidate_table(predictions(gain=250)), 'site+ridge')
        self.assertTrue(clear['switched'])
        self.assertEqual(clear['regional_choice'], 'hydro+mlp')
        few = decide(candidate_table(predictions(gain=250, n_municipalities=20)), 'site+ridge')
        self.assertFalse(few['switched'])
        self.assertFalse(few['checks']['enough_harvests'])
        worse_outside = decide(candidate_table(predictions(gain=250, spatial_penalty=2000)), 'site+ridge')
        self.assertFalse(worse_outside['switched'])
        self.assertEqual(worse_outside['regional_choice'], 'site+ridge')

    def test_rows_outside_the_region_are_ignored(self):
        from data.ingest.regional_rule import candidate_table
        frame = predictions(gain=250)
        frame.loc[frame.municipality_code.str.endswith('1'), 'state_code'] = '43'
        self.assertEqual(set(candidate_table(frame).state_code), {'51'})

    def test_no_switch_when_national_is_already_best(self):
        from data.ingest.regional_rule import candidate_table, decide
        decision = decide(candidate_table(predictions(gain=-200)), 'site+ridge')
        self.assertFalse(decision['switched'])
        self.assertEqual(decision['regional_choice'], 'site+ridge')


if __name__ == '__main__':
    unittest.main()
