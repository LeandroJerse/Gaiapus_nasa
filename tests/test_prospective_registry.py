"""Prospective registry v9: first issuance only, eligibility, provenance and tamper-evident chain."""
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock


class FakeProvider:
    def provenance(self, crop, code, first, last, years):
        return [{'source': 'fake', 'period': [str(first), str(last)], 'retrieved_at': '2026-11-14T03:31:00+00:00'}]


def item(code='5107925', year=2027, cutoff=30, status='ready', estimate=3500.0):
    response = {'status': status, 'crop': 'soy', 'municipality_code': code, 'harvest_year': year, 'cutoff_days_used': cutoff,
                'milestone': f'day_{cutoff}', 'observed_through': '2026-11-03', 'sowing_date': '2026-10-05',
                'sowing_basis': 'hypothetical_fixed_calendar_per_state', 'model': {'scope': 'national', 'version': 'research_v7'},
                'coverage': {}, 'estimate_kg_ha': estimate, 'warnings': []}
    trace = {'row': {'ref_lag_mean_kg_ha': 3400.0, 'horizon_days': cutoff}, 'window': (date(2026, 10, 5), date(2026, 11, 3)),
             'history_years': [2021, 2022, 2023, 2024, 2025]}
    return response, trace


class RegistryTests(unittest.TestCase):
    def setUp(self):
        from data.serving import prospective_registry as module
        self.module = module
        self.folder = Path(tempfile.mkdtemp())
        protocol = {'target': 'PAM first release', 'models': [
            {'model_id': 'ref_lag_mean:soy', 'crop': 'soy', 'role': 'simple_reference', 'scope_states': None, 'column': 'ref_lag_mean_kg_ha'},
            {'model_id': 'late:soy', 'crop': 'soy', 'role': 'simple_reference', 'scope_states': None, 'column': 'ref_lag_mean_kg_ha',
             'registered_at': '2999-01-01T00:00:00+00:00'}]}
        (self.folder / 'protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
        self.patches = [mock.patch.object(module, 'REGISTRY', self.folder / 'registry'),
                        mock.patch.object(module, 'PROTOCOL', self.folder / 'protocol.json')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.folder)

    def test_records_once_with_provenance_and_valid_chain(self):
        today = date.today()
        manifest = self.module.register(today, [item(), item(code='5107909'), item(status='before_first_cutoff')], FakeProvider(), 'operational')
        self.assertEqual(manifest['records_added'], 2)
        self.assertEqual(manifest['skipped']['not_ready'], 1)
        again = self.module.register(today, [item(estimate=9999.0)], FakeProvider(), 'operational')
        self.assertEqual((again['records_added'], again['skipped']['already_recorded']), (0, 1))
        self.assertEqual(self.module.verify(), 2)
        lines = (self.folder / 'registry/records' / f'{today}.jsonl').read_text(encoding='utf-8').splitlines()
        record = json.loads(lines[0])
        self.assertEqual(record['decision_at'], today.isoformat())
        self.assertEqual(record['predictions']['ref_lag_mean:soy']['estimate_kg_ha'], 3400.0)
        self.assertNotIn('late:soy', record['predictions'])  # registered after the issue time
        self.assertEqual(record['inputs'][0]['retrieved_at'], '2026-11-14T03:31:00+00:00')

    def test_tampering_breaks_the_chain(self):
        today = date.today()
        self.module.register(today, [item(), item(code='5107909')], FakeProvider(), 'operational')
        path = self.folder / 'registry/records' / f'{today}.jsonl'
        lines = path.read_text(encoding='utf-8').splitlines()
        record = json.loads(lines[0])
        record['predictions']['ref_lag_mean:soy']['estimate_kg_ha'] = 1.0
        path.write_text('\n'.join([self.module.canonical(record), lines[1]]) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.module.verify()

    def test_released_harvests_past_dates_and_retrospective_runs_are_refused(self):
        today = date.today()
        manifest = self.module.register(today, [item(year=2024)], FakeProvider(), 'operational')  # PAM 2024 already out
        self.assertEqual((manifest['records_added'], manifest['skipped']['pam_released']), (0, 1))
        with self.assertRaises(ValueError):
            self.module.register(today - timedelta(days=1), [item()], FakeProvider(), 'operational')
        with self.assertRaises(ValueError):
            self.module.register(today, [item()], FakeProvider(), 'retrospective')


if __name__ == '__main__':
    unittest.main()
