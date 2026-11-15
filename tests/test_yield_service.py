import json
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist/gaiapus-models-v7'


def ready():
    return (DIST / 'catalog.json').exists() and (ROOT / 'data/processed/training_v7/predictions_soy.parquet').exists()


class YieldServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ready():
            raise unittest.SkipTest('Packaged models and catalog not built on this machine')
        from jsonschema import Draft202012Validator, FormatChecker
        from data.serving.providers import Offline
        cls.catalog = json.loads((DIST / 'catalog.json').read_text(encoding='utf-8'))
        cls.offline = Offline()
        cls.schema = Draft202012Validator(json.loads((ROOT / 'contracts/yield-estimate.schema.json').read_text(encoding='utf-8')),
                                          format_checker=FormatChecker())
        cls.error_schema = Draft202012Validator(json.loads((ROOT / 'contracts/yield-error.schema.json').read_text(encoding='utf-8')))

    def ask(self, crop, code, as_of, **kwargs):
        from data.serving.yield_service import estimate_yield
        return estimate_yield(crop, code, as_of, catalog=self.catalog, models_dir=DIST / 'models', get_weather=self.offline.weather,
                              get_history=self.offline.history, **kwargs)

    def walk_forward(self, crop, sources, model, code, horizon):
        import pandas as pd
        table = pd.read_parquet(ROOT / f'data/processed/training_v7/predictions_{crop}.parquet')
        row = table[(table.sources == sources) & (table.model == model) & (table.municipality_code == code)
                    & (table.harvest_year == 2021) & (table.horizon_days == horizon)]
        return float(row.predicted_kg_ha.iloc[0])

    def test_soy_in_mt_uses_the_regional_mlp_and_reproduces_validation(self):
        answer = self.ask('soy', '5107925', date(2021, 1, 20))
        self.schema.validate(answer)
        self.assertEqual((answer['status'], answer['model']['name'], answer['model']['scope'], answer['cutoff_days_used']),
                         ('ready', 'site+mlp', 'MG+MT', 105))
        self.assertAlmostEqual(answer['estimate_kg_ha'], self.walk_forward('soy', 'site', 'mlp', '5107925', 105), delta=0.1)

    def test_soy_in_parana_uses_the_national_ridge(self):
        answer = self.ask('soy', '4100103', date(2021, 1, 20))
        self.schema.validate(answer)
        self.assertEqual((answer['model']['name'], answer['model']['scope']), ('site+ridge', 'national'))

    def test_maize_wheat_and_coffee_reproduce_validation(self):
        cases = [('maize2', '5107925', date(2021, 3, 10), 'site', 'ridge', 60),
                 ('wheat', '3129509', date(2021, 6, 15), 'site', 'hgb', 90),
                 ('coffee_arabica', '3100302', date(2020, 12, 5), 'site', 'hgb', 183)]
        for crop, code, as_of, sources, model, horizon in cases:
            answer = self.ask(crop, code, as_of)
            self.schema.validate(answer)
            self.assertEqual((answer['status'], answer['cutoff_days_used']), ('ready', horizon), crop)
            self.assertAlmostEqual(answer['estimate_kg_ha'], self.walk_forward(crop, sources, model, code, horizon), delta=0.1, msg=crop)
        coffee = self.ask('coffee_arabica', '3100302', date(2020, 12, 5))
        self.assertEqual((coffee['milestone'], coffee['sowing_basis']), ('post_flowering', 'coffee_production_year_start'))

    def test_scientific_status_and_history_reference_are_shown(self):
        expected = [('soy', '5107925', date(2021, 1, 20), 'not_confirmed_on_2022_2024_test'),   # regional MLP MG+MT
                    ('coffee_arabica', '3100302', date(2020, 12, 5), 'worse_than_history_on_2022_2024_test'),
                    ('coffee_conilon', '3203205', date(2020, 12, 5), 'confirmed_on_2022_2024_test')]
        for crop, code, as_of, status in expected:
            answer = self.ask(crop, code, as_of)
            self.schema.validate(answer)
            self.assertEqual(answer['schema_version'], '1.2.0')
            self.assertEqual(answer['model']['scientific_status'], status, crop)
            reference = answer['model']['historical_reference']
            self.assertEqual(reference['name'], 'history')
            self.assertIsNotNone(reference['test_2022_2024_mae_kg_ha'])
            self.assertEqual(answer['operational_release'], 'not_released')

    def test_reference_crops_answer_with_the_reference_and_say_so(self):
        answer = self.ask('maize1', '3170206', date(2021, 2, 20))
        self.schema.validate(answer)
        self.assertEqual(answer['model']['type'], 'reference')
        self.assertTrue(any('simple reference' in w for w in answer['warnings']))

    def test_season_statuses(self):
        before = self.ask('soy', '3170206', date(2020, 11, 20))
        outside = self.ask('maize2', '5107925', date(2021, 10, 1))
        for answer, status in ((before, 'before_first_cutoff'), (outside, 'outside_season')):
            self.schema.validate(answer)
            self.assertEqual(answer['status'], status)
            self.assertIsNone(answer['estimate_kg_ha'])

    def test_reported_sowing_date_is_used_and_flagged(self):
        answer = self.ask('soy', '5107925', date(2021, 1, 20), sowing_date=date(2020, 10, 20))
        self.schema.validate(answer)
        self.assertEqual((answer['sowing_date'], answer['sowing_basis'], answer['cutoff_days_used']), ('2020-10-20', 'reported_by_user', 90))
        self.assertTrue(any('reported by the user' in w for w in answer['warnings']))

    def test_request_errors_and_incomplete_inputs(self):
        for crop, code, reason in (('tomato', '5107925', 'invalid_crop'), ('soy', '51079', 'invalid_municipality_code'),
                                   ('soy', '3550308', 'municipality_not_covered')):
            answer = self.ask(crop, code, date(2021, 1, 20))
            self.error_schema.validate(answer)
            self.assertEqual(answer['error']['code'], reason)
        from data.serving.yield_service import estimate_yield

        def gappy(code, first, last):
            weather, elevation, location = self.offline.weather(code, first, last)
            weather = dict(weather)
            weather['2020-11-01'] = {**weather['2020-11-01'], 'rain_mm': None}
            return weather, elevation, location
        answer = estimate_yield('soy', '5107925', date(2021, 1, 20), catalog=self.catalog, models_dir=DIST / 'models',
                                get_weather=gappy, get_history=self.offline.history)
        self.schema.validate(answer)
        self.assertEqual(answer['status'], 'inputs_incomplete')


if __name__ == '__main__':
    unittest.main()
