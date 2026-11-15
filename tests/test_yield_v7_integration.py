import json
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]


class YieldIntegrationTests(unittest.TestCase):
    def test_request_errors_are_distinct(self):
        from data.serving.yield_request import check_yield_request
        coverage = {'soy': {'5107925'}, 'maize2': {'5107925'}}
        cases = [
            ('soy', '3550308', 'municipality_not_covered'),
            ('unknown', '5107925', 'invalid_crop'),
        ]
        schema = json.loads((ROOT / 'contracts/yield-error.schema.json').read_text(encoding='utf-8'))
        for crop, code, reason in cases:
            error = check_yield_request(crop, code, coverage)
            self.assertEqual(error['error']['code'], reason)
            jsonschema.validate(error, schema)
            fixture = ROOT / 'contracts/examples' / f'yield-error-{reason}.json'
            self.assertEqual(error, json.loads(fixture.read_text(encoding='utf-8')))
        self.assertIsNone(check_yield_request('soy', '5107925', coverage))
        self.assertEqual(check_yield_request('soy', '1', coverage)['error']['code'], 'invalid_municipality_code')

    def test_sorriso_examples_use_v7(self):
        for crop in ('soy', 'maize2'):
            file = ROOT / 'contracts/examples' / f'yield-{crop}-5107925-day75.json'
            response = json.loads(file.read_text(encoding='utf-8'))
            self.assertEqual(response['model']['version'], 'research_v7_national_walk_forward')
            self.assertEqual(response['status'], 'ready')
            self.assertEqual(response['cutoff_days_used'], 75)
