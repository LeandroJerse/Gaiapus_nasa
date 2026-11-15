"""Comparison of 2-4 management alternatives on the same climate members (synthetic forcing; diagnostic only)."""
import math
import unittest
from datetime import date, timedelta

from data.models.crop_simulation import comparison as cmp
from data.models.crop_simulation.ensemble import RecurringCrop
from data.models.crop_simulation.types import ForcingDay, SimulationInputError, SoilProfile, SoilState

SOY = (RecurringCrop('soy', '10-05', 0),)
ROTATION = (RecurringCrop('soy', '10-05', 0), RecurringCrop('maize2', '01-28', 1))


def forcing(year, wet=1.0, days=330):
    """From 15 September of `year`: wet until March, dry afterwards; deterministic."""
    start = date(year, 9, 15)
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        rainy = day.month in (10, 11, 12, 1, 2, 3)
        out.append(ForcingDay(day, (14.0 * wet if i % 4 == 0 else 0.0) if rainy else (3.0 if i % 19 == 0 else 0.0), 4.5 + .5 * math.sin(i / 40)))
    return out


def members(years=range(2015, 2021), wet=lambda y: 0.6 + (y % 5) * .15):
    return {y: forcing(y, wet(y)) for y in years}


def alt(alternative_id, crops=ROTATION, **kw):
    return cmp.ManagementAlternative(alternative_id=alternative_id, name=kw.pop('name', alternative_id), crops=crops, **kw)


def request(alternatives, reference_id='a', **kw):
    return cmp.ComparisonRequest(reference_id=reference_id, alternatives=tuple(alternatives), parameter_set_id='gaiapus-fao56-br-v1',
                                 soil=SoilProfile(55.0, 'AD4-hypothesis'), initial_state=SoilState(30.0), **kw)


IRRIGATED = dict(irrigation=(cmp.IrrigationEvent('maize2', 60, 20.0), cmp.IrrigationEvent('maize2', 75, 20.0)), irrigation_efficiency=.8)


class Calendar(unittest.TestCase):
    def test_recurring_irrigation_follows_the_sowing_of_each_member_year(self):
        a = alt('c', **IRRIGATED)
        by_year = {y: cmp.resolve_irrigation(a, y, 'gaiapus-fao56-br-v1', 'reject') for y in (2015, 2019, 2020)}
        self.assertEqual(sorted(by_year[2015]), [date(2016, 3, 28), date(2016, 4, 12)])  # 2016 is a leap year: 28 Jan + 60 days
        self.assertEqual(sorted(by_year[2019]), [date(2020, 3, 28), date(2020, 4, 12)])
        self.assertEqual(sorted(by_year[2020]), [date(2021, 3, 29), date(2021, 4, 13)])
        result = cmp.run_comparison(request([alt('a'), a]), members())
        for year, t in result.runs['c'].trajectories.items():
            applied = {r['date']: r['irrigation_net_mm'] for r in t.rows if r['irrigation_net_mm'] > 0}
            self.assertEqual(sorted(applied), [d.isoformat() for d in sorted(cmp.resolve_irrigation(a, year, 'gaiapus-fao56-br-v1', 'reject'))])
            for r in t.rows:
                if r['irrigation_net_mm'] > 0:
                    self.assertEqual((r['crop_id'], r['days_after_sowing'] in (60, 75)), ('maize2', True))

    def test_rotation_crosses_december_with_continuous_soil_water(self):
        result = cmp.run_comparison(request([alt('a'), alt('b', **IRRIGATED)]), members())
        for run in result.runs.values():
            for t in run.trajectories.values():
                days = [date.fromisoformat(r['date']) for r in t.rows]
                self.assertTrue(all((b - a).days == 1 for a, b in zip(days, days[1:])))
                for prev, row in zip(t.rows, t.rows[1:]):
                    self.assertEqual(row['storage_start_mm'], prev['storage_mm'])
                self.assertEqual({r['crop_id'] for r in t.rows}, {None, 'soy', 'maize2'})

    def test_events_outside_the_cycle_or_for_a_crop_not_in_the_plan_are_refused(self):
        for bad in (cmp.IrrigationEvent('maize2', 120, 10.0), cmp.IrrigationEvent('maize2', -1, 10.0), cmp.IrrigationEvent('coffee', 10, 10.0)):
            with self.assertRaises(SimulationInputError, msg=repr(bad)):
                cmp.run_comparison(request([alt('a'), alt('b', irrigation=(bad,))]), members())
        with self.assertRaises(SimulationInputError):
            cmp.run_comparison(request([alt('a'), alt('b', crops=SOY, irrigation=(cmp.IrrigationEvent('maize2', 10, 10.0),))]), members())


class Validation(unittest.TestCase):
    def test_two_to_four_alternatives_with_unique_stable_ids_and_a_reference(self):
        ok = [alt('a'), alt('b', **IRRIGATED), alt('c', crops=SOY), alt('d', crops=(RecurringCrop('soy', '10-20', 0), RecurringCrop('maize2', '02-12', 1)))]
        self.assertEqual(list(cmp.run_comparison(request(ok), members()).runs), ['a', 'b', 'c', 'd'])
        for bad in ([alt('a')], ok + [alt('e')], [alt('a'), alt('a')], [alt('b'), alt('c')], [alt('a'), alt('B c')]):
            with self.assertRaises(SimulationInputError, msg=[x.alternative_id for x in bad]):
                cmp.run_comparison(request(bad), members())

    def test_invalid_numbers_are_refused_before_any_run(self):
        for kw in (dict(irrigation_efficiency=0), dict(irrigation_efficiency=1.2), dict(irrigation_efficiency=math.nan), dict(runoff_fraction=-.1),
                   dict(max_net_mm_per_day=0), dict(max_gross_mm_per_season=-5), dict(max_gross_mm_per_season=math.inf),
                   dict(irrigation=(cmp.IrrigationEvent('maize2', 10, -1.0),)), dict(irrigation=(cmp.IrrigationEvent('maize2', 10, math.nan),)),
                   dict(irrigation=(cmp.IrrigationEvent('maize2', 10, True),))):
            with self.assertRaises(SimulationInputError, msg=kw):
                cmp.run_comparison(request([alt('a'), alt('b', **kw)]), members())

    def test_overlapping_crops_are_refused_not_drawn(self):
        late = (RecurringCrop('soy', '11-20', 0), RecurringCrop('maize2', '01-28', 1))
        with self.assertRaises(SimulationInputError) as ctx:
            cmp.run_comparison(request([alt('a'), alt('b', crops=late)]), members())
        self.assertEqual(ctx.exception.code, 'OVERLAPPING_CROPS')

    def test_fewer_than_three_common_members_is_not_a_comparison(self):
        with self.assertRaises(SimulationInputError) as ctx:
            cmp.run_comparison(request([alt('a'), alt('b')]), members(range(2015, 2017)))
        self.assertEqual(ctx.exception.code, 'INSUFFICIENT_COVERAGE')


class Pairing(unittest.TestCase):
    def test_identical_management_gives_zero_differences(self):
        result = cmp.run_comparison(request([alt('a'), alt('b')]), members())
        for metric, stat in result.differences['b'].items():
            self.assertEqual((stat['min'], stat['max'], stat['mean']), (0, 0, 0), metric)

    def test_order_of_the_alternatives_does_not_change_any_result(self):
        x = [alt('a'), alt('b', **IRRIGATED), alt('c', crops=SOY)]
        one = cmp.run_comparison(request(x), members()); two = cmp.run_comparison(request([x[2], x[0], x[1]]), members())
        self.assertEqual(one.differences, two.differences)
        self.assertEqual({k: v.summary for k, v in one.runs.items()}, {k: v.summary for k, v in two.runs.items()})
        self.assertEqual(one.forcing_sha256, two.forcing_sha256)
        self.assertEqual({k: v.config_sha256 for k, v in one.runs.items()}, {k: v.config_sha256 for k, v in two.runs.items()})

    def test_differences_are_paired_per_member_then_summarised_with_mean_and_median(self):
        result = cmp.run_comparison(request([alt('a'), alt('b', **IRRIGATED)]), members())
        a, b = result.runs['a'].metrics, result.runs['b'].metrics
        deltas = [b['maize2.deficit_mm'][y] - a['maize2.deficit_mm'][y] for y in result.member_ids]
        stat = result.differences['b']['maize2.deficit_mm']
        self.assertAlmostEqual(stat['mean'], sum(deltas) / len(deltas), places=12)
        self.assertEqual(stat['n'], 6)
        self.assertEqual((stat['min'], stat['max']), (min(deltas), max(deltas)))
        self.assertTrue(all(d <= 1e-9 for d in deltas))  # irrigation never raises the deficit of the irrigated crop
        self.assertIn('p50', stat); self.assertIn('mean', result.runs['a'].summary['maize2.deficit_mm'])
        self.assertEqual(result.differences_kind, 'paired_per_member_then_summarised')
        self.assertNotIn('a', result.differences)

    def test_a_member_missing_in_one_alternative_is_excluded_from_all_with_the_reason(self):
        leap = alt('b', crops=(RecurringCrop('soy', '10-05', 0), RecurringCrop('maize2', '02-29', 1)))
        result = cmp.run_comparison(request([alt('a'), leap, alt('c', crops=SOY)], leap_policy='exclude'), members(range(2003, 2021)))
        self.assertEqual(result.member_ids, [2003, 2007, 2011, 2015, 2019])  # seasons whose following February has a 29th
        self.assertEqual(result.excluded[2004], {'b': 'INVALID_DATE'})
        self.assertEqual(result.coverage, {'members': 5, 'candidates': 18})
        self.assertEqual(sorted(result.runs['a'].metrics['soy.deficit_mm']), result.member_ids)

    def test_every_alternative_sees_the_same_weather_on_the_same_calendar_day(self):
        shifted = alt('b', crops=(RecurringCrop('soy', '10-20', 0), RecurringCrop('maize2', '02-12', 1)))
        result = cmp.run_comparison(request([alt('a'), shifted]), members())
        for year in result.member_ids:
            ra, rb = result.runs['a'].trajectories[year].rows, result.runs['b'].trajectories[year].rows
            self.assertEqual([(r['date'], r['precipitation_mm'], r['et0_mm']) for r in ra], [(r['date'], r['precipitation_mm'], r['et0_mm']) for r in rb])
            self.assertEqual(ra[0]['storage_start_mm'], rb[0]['storage_start_mm'])
        self.assertEqual(result.runs['b'].harvests['soy']['sowing_rule'], '10-20')


class Water(unittest.TestCase):
    def test_water_is_never_created_and_the_balance_closes(self):
        result = cmp.run_comparison(request([alt('a'), alt('b', **IRRIGATED)]), members())
        for name, run in result.runs.items():
            for year, t in run.trajectories.items():
                self.assertLess(t.totals['max_abs_daily_residual_mm'], 1e-9)
                net = sum(r['irrigation_net_mm'] for r in t.rows)
                self.assertAlmostEqual(run.metrics['irrigation_net_mm'][year], net, places=12)
                self.assertAlmostEqual(run.metrics['irrigation_gross_mm'][year], net / (.8 if name == 'b' else 1), places=9)
                balance = 30.0 + t.totals['precipitation_mm'] + net - t.totals['runoff_mm'] - t.totals['drainage_mm'] - t.totals['eta_mm'] - t.totals['storage_end_mm']
                self.assertAlmostEqual(balance, 0, places=8)
        self.assertTrue(all(v == 0 for v in result.runs['a'].metrics['irrigation_net_mm'].values()))

    def test_daily_capacity_and_available_water_limit_what_is_applied_and_the_shortfall_is_reported(self):
        limited = alt('b', irrigation=(cmp.IrrigationEvent('maize2', 60, 30.0), cmp.IrrigationEvent('maize2', 75, 30.0)), irrigation_efficiency=.8,
                      max_net_mm_per_day=20.0, max_gross_mm_per_season=37.5)
        result = cmp.run_comparison(request([alt('a'), limited]), members())
        run = result.runs['b']
        for year in result.member_ids:
            self.assertAlmostEqual(run.metrics['irrigation_net_mm'][year], 30.0, places=9)   # 20 on the first day, then 37.5 gross - 25 gross = 10 net
            self.assertAlmostEqual(run.metrics['irrigation_gross_mm'][year], 37.5, places=9)
            self.assertAlmostEqual(run.metrics['irrigation_unmet_net_mm'][year], 30.0, places=9)
        self.assertIn('irrigation_limited_by_capacity_or_available_water', run.warnings)
        self.assertNotIn('irrigation_limited_by_capacity_or_available_water', result.runs['a'].warnings)

    def test_harvest_metrics_are_per_crop_and_absent_crops_are_absent_not_zero(self):
        result = cmp.run_comparison(request([alt('a'), alt('c', crops=SOY)]), members())
        self.assertEqual(list(result.runs['a'].harvests), ['soy', 'maize2'])
        self.assertEqual(list(result.runs['c'].harvests), ['soy'])
        self.assertNotIn('maize2.deficit_mm', result.runs['c'].metrics)
        self.assertNotIn('maize2.deficit_mm', result.differences['c'])
        self.assertEqual(result.not_comparable['c'], ['maize2.deficit_mm', 'maize2.eta_mm', 'maize2.etc_mm', 'maize2.irrigation_gross_mm', 'maize2.reproductive_stress_days', 'maize2.stress_days', 'maize2.water_satisfaction'])
        sat = result.runs['a'].metrics['soy.water_satisfaction']
        self.assertTrue(all(0 < v <= 1 for v in sat.values()))
        self.assertEqual(result.evidence_status, 'physical_experimental')
        self.assertEqual(result.yield_response, 'not_estimated_by_the_water_engine')


if __name__ == '__main__':
    unittest.main()
