import json
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = {'version': 'research_v6', 'type': 'ridge', 'sources': ['history'], 'artifact_sha256': 'a' * 64, 'trained_through_harvest': 2019}
VALIDATION = {c: {'years': [2019, 2020, 2021], 'cutoff_days': c, 'mae_kg_ha': 260.0, 'bias_kg_ha': -30.0,
                  'label_families': 900, 'municipalities': 300, 'scope': 'temporal'} for c in (30, 60, 90)}
LIMIT = {'max_supported_lead_days': {'soy': 7, 'maize2': 7}, 'archive': 'archive', 'verification': 'same provider',
         'reference': 'climatology', 'rule': {'max_storage_mae_share_of_capacity': 0.15}}
BY_LEAD = [{'lead_days': k, 'storage_mae_mm': 2.0 + k, 'reference_storage_mae_mm': 4.0 + k, 'stress_accuracy': 0.95,
            'coverage': 0.99, 'passed': True} for k in range(1, 8)]
ISSUE = {'issued_at': '2026-11-14T00:00:00+00:00', 'available_at': '2026-11-14T08:05:24+00:00',
         'retrieved_at': '2026-11-14T12:00:00+00:00', 'issue_time_source': 'metadata'}


def validator(name):
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads((ROOT / 'contracts' / name).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def yield_answer(day, crop='soy'):
    from data.serving.products import yield_response
    return yield_response(municipality_code='5107925', crop=crop, harvest_year=2021, sowing_date=date(2020, 10, 5),
                          days_after_sowing=day, supported_cutoffs=(30, 60, 90), estimate_kg_ha=3512.34,
                          observed_through='2020-12-03', model=MODEL, validation_by_cutoff=VALIDATION,
                          coverage={'forcing_days_expected': 60, 'forcing_days_present': 60, 'sources': ['NASA POWER']})


def water_answer(horizon, crop_day=40, forecast_days=15):
    from data.serving.products import water_response
    observed = [{'rain_mm': 5.0, 'et0_mm': 4.5}] * crop_day
    forecast = [{'rain_mm': 0.0 if k % 3 else 12.0, 'et0_mm': 5.0} for k in range(forecast_days)]
    return water_response(municipality_code='5107925', latitude=-12.7, longitude=-55.8, crop='soy', sowing_date=date(2026, 10, 5),
                          decision_at=f'{date.fromordinal(date(2026, 10, 5).toordinal() + crop_day)}T12:00:00+00:00',
                          horizon_days=horizon, observed=observed, forecast=forecast, issue=ISSUE, limit=LIMIT,
                          backtest_by_lead=BY_LEAD, reference=[{'rain_mm': 6.0, 'et0_mm': 4.8}] * forecast_days)


class ServingContractTests(unittest.TestCase):
    def test_yield_uses_the_largest_validated_cutoff_not_after_the_day(self):
        from data.serving.products import cutoff_for_day
        self.assertEqual([cutoff_for_day(d, (30, 60, 90)) for d in (10, 30, 59, 60, 200)], [None, 30, 30, 60, 90])
        answer = yield_answer(75)
        self.assertEqual((answer['status'], answer['cutoff_days_used'], answer['validation']['cutoff_days']), ('ready', 60, 60))
        validator('yield-estimate.schema.json').validate(answer)

    def test_yield_before_first_cutoff_has_no_number(self):
        answer = yield_answer(20)
        self.assertEqual((answer['status'], answer['estimate_kg_ha'], answer['validation']), ('before_first_cutoff', None, None))
        validator('yield-estimate.schema.json').validate(answer)

    def test_yield_schema_rejects_a_number_without_validation(self):
        from jsonschema import ValidationError
        answer = {**yield_answer(20), 'estimate_kg_ha': 3000.0}
        with self.assertRaises(ValidationError):
            validator('yield-estimate.schema.json').validate(answer)

    def test_water_projection_flags_days_beyond_the_validated_lead(self):
        answer = water_answer(10)
        self.assertEqual(answer['status'], 'partially_validated')
        self.assertEqual([d['validated_lead'] for d in answer['days']], [True] * 7 + [False] * 3)
        self.assertEqual(answer['days'][0]['date'], '2026-11-14')
        self.assertIsNone(answer['yield_effect'])
        self.assertEqual(len(answer['reference_days']), 10)
        validator('water-projection.schema.json').validate(answer)

    def test_water_projection_inside_the_validated_lead_is_ready(self):
        answer = water_answer(5)
        self.assertEqual((answer['status'], answer['horizon_days_served']), ('ready', 5))
        validator('water-projection.schema.json').validate(answer)

    def test_water_projection_stops_at_the_end_of_the_forecast_or_cycle(self):
        answer = water_answer(30, forecast_days=12)
        self.assertEqual(answer['horizon_days_served'], 12)
        self.assertTrue(any('Served 12 of 30' in w for w in answer['warnings']))
        late = water_answer(30, crop_day=110, forecast_days=20)
        self.assertEqual(late['horizon_days_served'], 5)
        validator('water-projection.schema.json').validate(late)

    def test_water_projection_rejects_incomplete_observations(self):
        from data.serving.products import water_response
        answer = water_response(municipality_code='5107925', latitude=-12.7, longitude=-55.8, crop='soy', sowing_date=date(2026, 10, 5),
                                decision_at='2026-11-14T12:00:00+00:00', horizon_days=5, observed=[{'rain_mm': None, 'et0_mm': 4.0}] * 40,
                                forecast=[{'rain_mm': 1.0, 'et0_mm': 4.0}] * 5, issue=ISSUE, limit=LIMIT, backtest_by_lead=BY_LEAD)
        self.assertEqual((answer['status'], answer['days']), ('not_supported', []))
        validator('water-projection.schema.json').validate(answer)

    def test_every_response_states_its_sowing_date_and_origin(self):
        from data.serving.products import yield_response
        for answer in (yield_answer(75), water_answer(5)):
            self.assertIn('sowing_date', answer)
            self.assertEqual(answer['sowing_basis'], 'hypothetical_fixed_calendar_per_state')
        reported = yield_response(municipality_code='5107925', crop='soy', harvest_year=2021, sowing_date=date(2020, 10, 20),
                                  days_after_sowing=60, supported_cutoffs=(30, 60), estimate_kg_ha=3400.0, observed_through='2020-12-18',
                                  model=MODEL, validation_by_cutoff=VALIDATION | {30: VALIDATION[60]}, sowing_basis='reported_by_user',
                                  coverage={'forcing_days_expected': 60, 'forcing_days_present': 60, 'sources': ['NASA POWER']})
        self.assertTrue(any('reported by the user' in w for w in reported['warnings']))
        validator('yield-estimate.schema.json').validate(reported)
        with self.assertRaises(ValueError):
            yield_response(**{**dict(municipality_code='1', crop='soy', harvest_year=2021, sowing_date=date(2020, 10, 5),
                                     days_after_sowing=60, supported_cutoffs=(60,), estimate_kg_ha=1.0, observed_through=None,
                                     model=MODEL, validation_by_cutoff=VALIDATION, coverage={}), 'sowing_basis': 'guessed'})

    def test_examples_in_the_repository_follow_the_schemas(self):
        folder = ROOT / 'contracts' / 'examples'
        schemas = {'yield-error': 'yield-error.schema.json', 'yield': 'yield-estimate.schema.json', 'water': 'water-projection.schema.json', 'field-shift': 'field-shift.schema.json'}
        for path in sorted(folder.glob('*.json')) if folder.exists() else []:
            name = next(v for k, v in schemas.items() if path.name.startswith(k))
            validator(name).validate(json.loads(path.read_text(encoding='utf-8')))

    def test_field_shift_example_is_simulated_and_has_no_yield_effect(self):
        path = ROOT / 'contracts' / 'examples' / 'field-shift-sorriso.json'
        if not path.exists():
            self.skipTest('Field Shift example not generated on this machine')
        answer = json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(answer['simulated'])
        self.assertIsNone(answer['yield_effect'])
        self.assertEqual(set(answer['scenarios']), {'reference_fallow', 'brachiaria'})
        self.assertTrue(all('origin' in v for v in answer['sowing'].values()))


if __name__ == '__main__':
    unittest.main()

