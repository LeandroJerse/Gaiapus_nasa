"""Scientific result envelope: physical, statistical and economic results stay separate; status is never inflated."""
import json
import unittest
from datetime import date, timedelta


def build(economics=None, n=6):
    from data.models.crop_simulation import ensemble, types
    from data.models.crop_simulation.economics import EconomicsSpec
    from data.serving import simulation_product as sp
    req = ensemble.EnsembleRequest(crops=(ensemble.RecurringCrop('soy', '10-05', 0),), parameter_set_id='gaiapus-fao56-br-v1',
                                   soil=types.SoilProfile(55.0, 'AD4-hypothesis'), initial_state=types.SoilState(30.0), management=types.ManagementSpec())
    members = {y: [types.ForcingDay(date(y, 9, 15) + timedelta(days=i), 10.0 if i % 5 == 0 else 0.0, 4.5) for i in range(135)] for y in range(2001, 2001 + n)}
    manifests = {y: {'content_sha256': f'{y:064d}', 'variable': 'IMERG_PRECTOT'} for y in members}
    spec = EconomicsSpec('BRL', 120.0, 'bag', 60.0, 'user', '2026-11-01', 2500.0, 1500.0, 0.5) if economics else None
    return sp.build_envelope(location_id='BR:ibge:5107925', mode='historical_ensemble', request=req, members=members, forcing_manifests=manifests,
                             economics=spec, created_at='2026-11-15T12:00:00+00:00')


class EnvelopeTests(unittest.TestCase):
    def test_identity_and_versions(self):
        env = build()
        self.assertEqual(env['schema_version'], 'crop-simulation-result-v1')
        self.assertEqual(env['engine']['version'], 'crop-simulation-v1')
        self.assertEqual(env['engine']['parameter_set_id'], 'gaiapus-fao56-br-v1')
        self.assertEqual(env['engine']['daily_order'], ['runoff', 'infiltration+irrigation', 'drainage', 'stress', 'eta'])
        self.assertEqual(len(env['input_hash']), 64)
        self.assertEqual(env['input_hash'], build()['input_hash'])
        self.assertEqual(env['forcing']['members']['2001'], '0' * 60 + '2001')

    def test_status_is_experimental_and_yield_unavailable(self):
        env = build()
        self.assertEqual(env['status']['technical'], 'ready')
        self.assertEqual(env['status']['evidence'], 'physical_experimental')
        self.assertEqual(env['results']['yield'], {'status': 'unavailable', 'method': None, 'reason_code': 'NO_CALIBRATED_YIELD_METHOD'})
        self.assertNotIn('prospective_approved', json.dumps(env))

    def test_economics_without_yield_reports_cost_and_water_only(self):
        env = build(economics=True)
        econ = env['results']['economics']
        self.assertEqual(econ['currency'], 'BRL')
        self.assertTrue(all(m['margin_per_ha'] is None and m['revenue_per_ha'] is None for m in econ['members'].values()))
        self.assertTrue(all(m['total_cost_per_ha'] == 4000.0 for m in econ['members'].values()))
        self.assertIsNone(build()['results']['economics'])

    def test_physical_summary_members_and_limitations(self):
        env = build()
        phys = env['results']['physical']
        self.assertEqual(phys['summary']['crop_deficit_mm']['n'], 6)
        self.assertEqual(env['members']['included'], [str(y) for y in range(2001, 2007)])
        self.assertEqual(env['members']['excluded'], {})
        for code in ('SINGLE_BUCKET_NO_ROOT_PROFILE', 'SOIL_HYPOTHESIS', 'HISTORICAL_FREQUENCY_NOT_PROBABILITY', 'NOT_FIELD_VALIDATED'):
            self.assertIn(code, env['limitations'])
        self.assertNotIn('rows', json.dumps(env['results']))  # daily rows are fetched per member, not in the first payload

    def test_daily_member_rows_are_served_separately(self):
        from data.serving import simulation_product as sp
        env = build()
        rows = sp.member_daily(env, '2003')
        self.assertEqual(rows[0]['date'], '2003-09-15')
        self.assertIn('mass_balance_residual_mm', rows[0])
        with self.assertRaises(KeyError):
            sp.member_daily(env, '1999')


if __name__ == '__main__':
    unittest.main()
