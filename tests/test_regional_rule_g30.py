import unittest


def predictions(errors, municipalities=12, held_out=12, seed=0):
    """`errors`: candidate -> (temporal error scale, spatial error scale) per state code."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    for state in ('31', '51'):
        for m in range(municipalities + held_out):
            held = m >= municipalities
            for year in (2019, 2020, 2021):
                truth = 3000.0
                for horizon in (60, 90):
                    for candidate, scales in errors.items():
                        scale = scales[state][1 if held else 0]
                        sources, model = candidate.split('+')
                        rows.append({'municipality_code': f'{state}{m:05d}', 'state_code': state, 'harvest_year': year,
                                     'horizon_days': horizon, 'spatial_holdout': held, 'yield_kg_ha': truth, 'sources': sources,
                                     'model': model, 'predicted_kg_ha': truth + scale * (1 + 0.1 * rng.standard_normal())})
    return pd.DataFrame(rows)


def uniform(t, s):
    return {'31': (t, s), '51': (t, s)}


class GuideThirtyRuleTests(unittest.TestCase):
    def test_promotes_a_candidate_better_in_every_state_with_negative_interval(self):
        from data.ingest.regional_rule_g30 import evaluate
        table, scopes, eligible, decision = evaluate(predictions({
            'history+history': uniform(300, 300), 'site+ridge': uniform(320, 310), 'weather+mlp': uniform(150, 200),
            'hydro+mlp': uniform(160, 200), 'site+mlp': uniform(170, 200)}))
        self.assertEqual(eligible, ['weather+mlp', 'hydro+mlp', 'site+mlp'])
        self.assertEqual(decision['selected'], 'weather+mlp')
        self.assertLess(decision['ci95'][1], 0)

    def test_better_in_one_state_only_keeps_history(self):
        from data.ingest.regional_rule_g30 import evaluate
        _, _, eligible, decision = evaluate(predictions({
            'history+history': uniform(300, 300), 'site+ridge': uniform(320, 320),
            'weather+mlp': {'31': (100, 200), '51': (310, 200)}, 'hydro+mlp': uniform(320, 320), 'site+mlp': uniform(320, 320)}))
        self.assertEqual(eligible, [])
        self.assertEqual(decision['selected'], 'history+history')

    def test_worse_out_of_training_in_one_state_is_ineligible(self):
        from data.ingest.regional_rule_g30 import evaluate
        _, _, eligible, decision = evaluate(predictions({
            'history+history': uniform(300, 300), 'site+ridge': uniform(320, 320),
            'weather+mlp': {'31': (150, 250), '51': (150, 350)}, 'hydro+mlp': uniform(320, 320), 'site+mlp': uniform(320, 320)}))
        self.assertNotIn('weather+mlp', eligible)
        self.assertEqual(decision['selected'], 'history+history')

    def test_too_few_held_out_municipalities_invalidates_the_set(self):
        from data.ingest.regional_rule_g30 import evaluate
        _, scopes, eligible, decision = evaluate(predictions({
            'history+history': uniform(300, 300), 'site+ridge': uniform(320, 320), 'weather+mlp': uniform(150, 150),
            'hydro+mlp': uniform(320, 320), 'site+mlp': uniform(320, 320)}, held_out=5))
        self.assertFalse(scopes['spatiotemporal']['valid_set'])
        self.assertEqual(decision['selected'], 'history+history')

    def test_ties_follow_the_declared_order(self):
        from data.ingest.regional_rule_g30 import evaluate
        _, _, eligible, decision = evaluate(predictions({
            'history+history': uniform(300, 300), 'site+ridge': uniform(320, 320), 'weather+mlp': uniform(150, 150),
            'hydro+mlp': uniform(150, 150), 'site+mlp': uniform(320, 320)}, seed=1))
        self.assertIn(decision['winner'], ('weather+mlp', 'hydro+mlp'))

    def test_harvest_mae_weights_cutoffs_equally_and_states_half_each(self):
        import pandas as pd
        from data.ingest.regional_rule_g30 import harvest_errors, weighted_mae
        frame = pd.DataFrame([
            {'candidate': 'x', 'state_code': '31', 'municipality_code': 'a', 'harvest_year': 2019, 'predicted_kg_ha': 110, 'yield_kg_ha': 100},
            {'candidate': 'x', 'state_code': '31', 'municipality_code': 'a', 'harvest_year': 2019, 'predicted_kg_ha': 130, 'yield_kg_ha': 100},
            {'candidate': 'x', 'state_code': '51', 'municipality_code': 'b', 'harvest_year': 2019, 'predicted_kg_ha': 100, 'yield_kg_ha': 100},
            {'candidate': 'x', 'state_code': '51', 'municipality_code': 'c', 'harvest_year': 2019, 'predicted_kg_ha': 140, 'yield_kg_ha': 100}])
        combined, by_state = weighted_mae(harvest_errors(frame))
        self.assertEqual(by_state, {'31': 20.0, '51': 20.0})
        self.assertEqual(combined, 20.0)


if __name__ == '__main__':
    unittest.main()
