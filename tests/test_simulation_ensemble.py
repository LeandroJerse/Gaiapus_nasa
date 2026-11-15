"""Historical ensembles and paired comparisons (synthetic forcing; diagnostic only)."""
import unittest
from datetime import date, timedelta


def m():
    from data.models.crop_simulation import ensemble, types
    return ensemble, types


def member_forcing(year, wet=1.0, et0=5.0):
    """Forcing for a soy season sown 10-05 of `year`, with a 20-day warm-up."""
    from data.models.crop_simulation.types import ForcingDay
    start = date(year, 9, 15)
    return [ForcingDay(start + timedelta(days=i), (12.0 * wet) if i % 4 == 0 else 0.0, et0) for i in range(20 + 115)]


def request(**kw):
    ens, types = m()
    args = dict(crops=(ens.RecurringCrop('soy', '10-05', 0),), parameter_set_id='gaiapus-fao56-br-v1', soil=types.SoilProfile(55.0, 'AD4-hypothesis'),
                initial_state=types.SoilState(30.0), management=types.ManagementSpec())
    args.update(kw)
    return ens.EnsembleRequest(**args)


def members(years, wet=lambda y: 1.0):
    return {y: member_forcing(y, wet(y)) for y in years}


class EnsembleTests(unittest.TestCase):
    def test_runs_one_member_per_season_and_summarises(self):
        ens, _ = m()
        r = ens.run_ensemble(request(), members(range(2001, 2011), wet=lambda y: 0.5 + (y - 2001) / 10))
        self.assertEqual(r.member_ids, list(range(2001, 2011)))
        self.assertEqual(r.excluded, {})
        s = r.summary['crop_deficit_mm']
        self.assertEqual(s['n'], 10)
        self.assertLessEqual(s['p10'], s['p50']); self.assertLessEqual(s['p50'], s['p90'])
        self.assertEqual(r.summary['frequency_kind'], 'historical_frequency_not_probability')
        rep = r.representative
        self.assertEqual(rep['rule_id'], 'nearest-to-percentile-crop-deficit-v1')
        self.assertTrue(set(rep['members'].values()) <= set(r.member_ids))

    def test_missing_member_is_excluded_with_reason_not_filled(self):
        ens, types = m()
        ms = members(range(2001, 2006)); bad = list(ms[2003]); bad[50] = types.ForcingDay(bad[50].date, None, 5.0); ms[2003] = bad
        r = ens.run_ensemble(request(), ms)
        self.assertNotIn(2003, r.member_ids)
        self.assertEqual(r.excluded[2003], 'FORCING_INCOMPLETE')
        self.assertEqual(r.summary['crop_deficit_mm']['n'], 4)

    def test_leap_rule_member_excluded_explicitly(self):
        ens, _ = m()
        from data.models.crop_simulation.types import ForcingDay
        req = request(crops=(ens.RecurringCrop('soy', '02-29', 0),), leap_policy='exclude')
        f = lambda y: [ForcingDay(date(y, 2, 1) + timedelta(days=i), 5.0, 4.0) for i in range(160)]
        r = ens.run_ensemble(req, {2023: f(2023), 2024: f(2024)})
        self.assertEqual(r.member_ids, [2024])
        self.assertEqual(r.excluded[2023], 'INVALID_DATE')

    def test_scenario_never_mutates_the_input_forcing(self):
        ens, _ = m()
        ms = members(range(2001, 2004)); snapshot = {k: list(v) for k, v in ms.items()}
        base = ens.run_ensemble(request(), ms)
        dry = ens.run_ensemble(request(scenario=ens.Scenario(rain_multiplier=0.8)), ms)
        self.assertEqual(ms, snapshot)
        self.assertEqual(dry.scenario, {'rain_multiplier': 0.8, 'et0_multiplier': 1.0})
        self.assertGreaterEqual(dry.summary['crop_deficit_mm']['p50'], base.summary['crop_deficit_mm']['p50'])
        with self.assertRaises(Exception):
            ens.Scenario(rain_multiplier=-0.1)

    def test_member_limit(self):
        ens, types = m()
        with self.assertRaises(types.SimulationInputError) as ctx:
            ens.run_ensemble(request(), members(range(1981, 2031)))
        self.assertEqual(ctx.exception.code, 'RESOURCE_LIMIT')


class PairedTests(unittest.TestCase):
    def fake(self, values):
        ens, _ = m()
        return ens.EnsembleResult.from_metrics({'crop_deficit_mm': values})

    def test_identical_alternatives_give_zero_deltas(self):
        ens, _ = m()
        r = ens.run_ensemble(request(), members(range(2001, 2008)))
        c = ens.compare_paired(r, r)
        self.assertTrue(all(v == 0 for v in c.deltas['crop_deficit_mm'].values()))
        self.assertEqual(c.summary['crop_deficit_mm']['p50'], 0)

    def test_quantiles_of_differences_are_not_differences_of_quantiles(self):
        ens, _ = m()
        a = self.fake({2001: 10.0, 2002: 20.0, 2003: 30.0, 2004: 40.0, 2005: 50.0})
        b = self.fake({2001: 50.0, 2002: 40.0, 2003: 30.0, 2004: 20.0, 2005: 10.0})
        c = ens.compare_paired(a, b)
        self.assertEqual(c.naive_p50_difference['crop_deficit_mm'], 0.0)  # P50(B) − P50(A)
        self.assertEqual(c.summary['crop_deficit_mm']['p50'], 0.0)
        self.assertEqual(c.summary['crop_deficit_mm']['p10'], -32.0)  # spread of paired deltas is not zero
        self.assertEqual(c.summary['crop_deficit_mm']['p90'], 32.0)

    def test_different_years_use_explicit_intersection(self):
        ens, types = m()
        a = self.fake({y: 1.0 for y in range(2001, 2011)})
        b = self.fake({y: 2.0 for y in range(2003, 2013)})
        c = ens.compare_paired(a, b)
        self.assertEqual(c.member_ids, list(range(2003, 2011)))
        self.assertEqual(c.excluded, {2001: 'missing_in_b', 2002: 'missing_in_b', 2011: 'missing_in_a', 2012: 'missing_in_a'})
        with self.assertRaises(types.SimulationInputError) as ctx:
            ens.compare_paired(self.fake({2001: 1.0, 2002: 1.0}), self.fake({2002: 1.0, 2003: 1.0}))
        self.assertEqual(ctx.exception.code, 'INSUFFICIENT_COVERAGE')


if __name__ == '__main__':
    unittest.main()
