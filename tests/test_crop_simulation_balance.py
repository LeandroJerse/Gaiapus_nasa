"""Water conservation and continuous calendar of the crop simulation engine (v1).

H02, H04 and H05 reproduce findings of the 2026-11-15 audit of the Lara simulator (AUDITORIA.md §4).
All forcing here is synthetic and diagnostic; it is not a climate observation.
"""
import random
import unittest
from datetime import date, timedelta

TOL = 1e-8


def mods():
    from data.models.crop_simulation import engine, soil, types
    return engine, soil, types


def forcing(start, days, rain=lambda i: 0.0, et0=lambda i: 5.0):
    from data.models.crop_simulation.types import ForcingDay
    return [ForcingDay(start + timedelta(days=i), rain(i), et0(i)) for i in range(days)]


def plan_items(*pairs):
    from data.models.crop_simulation.types import CropPlanItem
    return [CropPlanItem(crop_id=c, parameter_set_id='gaiapus-fao56-br-v1', sowing_date=d) for c, d in pairs]


def run(items, days_forcing, storage=30.0, capacity=55.0, **mgmt):
    engine, soil, types = mods()
    return engine.simulate_daily(items, days_forcing, types.SoilProfile(capacity_mm=capacity, profile_id='test-AD4'),
                                 types.SoilState(storage_mm=storage), types.ManagementSpec(**mgmt))


class StepTests(unittest.TestCase):
    def test_simple_identity(self):
        _, soil, _ = mods()
        r = soil.step_day(storage=20.0, capacity=100.0, precipitation=10.0, irrigation=0.0, etc=3.0, p=0.8)
        self.assertAlmostEqual(r['eta_mm'], 3.0, delta=TOL)
        self.assertAlmostEqual(r['storage_mm'], 27.0, delta=TOL)
        self.assertLessEqual(abs(r['mass_balance_residual_mm']), TOL)

    def test_h05_extraction_limited_to_available_water(self):
        # Lara: ET0 20 mm/day with 5.25 mm stored gave ETa 6.45 mm, storage 0 and residual -1.20 mm.
        _, soil, _ = mods()
        # p spans the whole adjusted range: at 0.8 the stress threshold is small and Ks·ETc exceeds the water present.
        for p in (0.1, 0.5, 0.8):
            r = soil.step_day(storage=5.25, capacity=55.0, precipitation=0.0, irrigation=0.0, etc=20.0 * 1.15, p=p)
            self.assertLessEqual(r['eta_mm'], 5.25 + TOL, p)
            self.assertGreaterEqual(r['storage_mm'], 0.0)
            self.assertLessEqual(abs(r['mass_balance_residual_mm']), TOL, p)

    def test_edges_produce_no_nan_or_water(self):
        _, soil, _ = mods()
        cases = [dict(storage=0.0, precipitation=0.0, irrigation=0.0, etc=0.0), dict(storage=55.0, precipitation=400.0, irrigation=0.0, etc=0.0),
                 dict(storage=55.0, precipitation=0.0, irrigation=250.0, etc=9.0), dict(storage=0.0, precipitation=0.0, irrigation=0.0, etc=30.0)]
        for c in cases:
            r = soil.step_day(capacity=55.0, p=0.5, **c)
            for k, v in r.items():
                if isinstance(v, float):
                    self.assertTrue(v == v, k)
            self.assertTrue(0.0 <= r['storage_mm'] <= 55.0)
            self.assertLessEqual(abs(r['mass_balance_residual_mm']), TOL)
            self.assertGreaterEqual(r['drainage_mm'], 0.0)

    def test_runoff_fraction_conserves(self):
        _, soil, _ = mods()
        r = soil.step_day(storage=20.0, capacity=100.0, precipitation=20.0, irrigation=10.0, etc=5.0, p=0.5, runoff_fraction=0.25)
        self.assertEqual(r['runoff_mm'], 5.0)
        self.assertLessEqual(abs(r['mass_balance_residual_mm']), TOL)

    def test_invalid_inputs_rejected(self):
        _, soil, types = mods()
        for bad in (dict(storage=-1.0), dict(storage=56.0), dict(precipitation=-1.0), dict(etc=float('nan')), dict(p=1.0)):
            args = dict(storage=10.0, capacity=55.0, precipitation=0.0, irrigation=0.0, etc=3.0, p=0.5); args.update(bad)
            with self.assertRaises(types.SimulationInputError):
                soil.step_day(**args)


class TrajectoryTests(unittest.TestCase):
    def test_h04_missing_forcing_is_not_zero(self):
        _, _, types = mods()
        f = forcing(date(2020, 10, 5), 115)
        f[30] = types.ForcingDay(f[30].date, None, 5.0)
        with self.assertRaises(types.SimulationInputError) as ctx:
            run(plan_items(('soy', date(2020, 10, 5))), f)
        self.assertEqual(ctx.exception.code, 'FORCING_INCOMPLETE')
        g = forcing(date(2020, 10, 5), 115)
        del g[40]
        with self.assertRaises(types.SimulationInputError) as ctx:
            run(plan_items(('soy', date(2020, 10, 5))), g)
        self.assertEqual(ctx.exception.code, 'FORCING_INCOMPLETE')

    def test_forcing_must_cover_the_plan(self):
        _, _, types = mods()
        with self.assertRaises(types.SimulationInputError) as ctx:
            run(plan_items(('soy', date(2020, 10, 5))), forcing(date(2020, 10, 6), 200))
        self.assertEqual(ctx.exception.code, 'FORCING_INCOMPLETE')

    def test_h02_rain_between_crops_changes_the_next_crop(self):
        items = plan_items(('soy', date(2020, 10, 5)), ('maize2', date(2021, 3, 1)))
        start = date(2020, 10, 5); n = (date(2021, 3, 1) - start).days + 120
        gap = range((date(2021, 1, 28) - start).days, (date(2021, 3, 1) - start).days)
        dry = run(items, forcing(start, n), storage=10.0)
        wet = run(items, forcing(start, n, rain=lambda i: 300.0 / len(gap) if i in gap else 0.0), storage=10.0)
        self.assertEqual(len(dry.rows), n)
        fallow = [r for r in dry.rows if r['crop_id'] is None]
        self.assertEqual(len(fallow), len(gap))
        self.assertTrue(all(r['phase_code'] == 'fallow' for r in fallow))
        first_b = next(i for i, r in enumerate(dry.rows) if r['crop_id'] == 'maize2')
        self.assertGreater(wet.rows[first_b]['storage_mm'], dry.rows[first_b]['storage_mm'])
        self.assertLess(sum(r['et_deficit_mm'] for r in wet.rows[first_b:]), sum(r['et_deficit_mm'] for r in dry.rows[first_b:]))

    def test_state_is_carried_every_day_including_crop_changes(self):
        items = plan_items(('soy', date(2020, 10, 5)), ('maize2', date(2021, 1, 28)))
        t = run(items, forcing(date(2020, 9, 1), 320, rain=lambda i: 12.0 if i % 4 == 0 else 0.0), storage=25.0)
        prev = 25.0
        for r in t.rows:
            self.assertEqual(r['storage_start_mm'], prev)
            prev = r['storage_mm']
            self.assertEqual(r['capacity_mm'], 55.0)
            self.assertIsNone(r['root_depth_m'])
        change = next(i for i, r in enumerate(t.rows) if r['crop_id'] == 'maize2')
        self.assertEqual(t.rows[change - 1]['crop_id'], 'soy')
        # No reset to field capacity at a crop change.
        self.assertEqual(t.rows[change]['storage_start_mm'], t.rows[change - 1]['storage_mm'])
        self.assertEqual(t.rows[0]['phase_code'], 'fallow')  # warm-up before sowing is simulated, not skipped

    def test_cumulative_conservation_random_multiyear(self):
        rnd = random.Random(20261115)
        items = plan_items(('soy', date(2020, 10, 5)), ('maize2', date(2021, 2, 5)), ('soy', date(2021, 10, 5)), ('maize2', date(2022, 2, 5)))
        start = date(2020, 8, 1); n = (date(2022, 9, 1) - start).days
        rain = [rnd.choice([0.0, 0.0, 0.0, rnd.uniform(0, 80), rnd.uniform(0, 250)]) for _ in range(n)]
        et0 = [rnd.uniform(0, 12) for _ in range(n)]
        irr = {start + timedelta(days=i): rnd.uniform(0, 60) for i in range(0, n, 9)}
        t = run(items, forcing(start, n, rain=lambda i: rain[i], et0=lambda i: et0[i]), storage=17.0, irrigation_net_mm=irr, runoff_fraction=0.1)
        total_in = 17.0 + sum(r['precipitation_mm'] + r['irrigation_net_mm'] for r in t.rows)
        total_out = sum(r['runoff_mm'] + r['drainage_mm'] + r['eta_mm'] for r in t.rows) + t.rows[-1]['storage_mm']
        self.assertLessEqual(abs(total_in - total_out), 1e-6)
        self.assertTrue(all(abs(r['mass_balance_residual_mm']) <= TOL for r in t.rows))
        self.assertTrue(all(0.0 <= r['storage_mm'] <= 55.0 for r in t.rows))
        self.assertTrue(any(r['date'] == '2022-02-28' for r in t.rows))  # non-leap February handled
        self.assertEqual(t.totals['days'], n)

    def test_leap_year_and_cycle_across_years(self):
        t = run(plan_items(('soy', date(2023, 12, 1))), forcing(date(2023, 12, 1), 115))
        self.assertIn('2024-02-29', [r['date'] for r in t.rows])
        self.assertEqual(t.rows[-1]['days_after_sowing'], 114)

    def test_irrigation_gross_uses_efficiency(self):
        t = run(plan_items(('soy', date(2020, 10, 5))), forcing(date(2020, 10, 5), 115), irrigation_net_mm={date(2020, 10, 10): 20.0}, irrigation_efficiency=0.8)
        r = next(r for r in t.rows if r['date'] == '2020-10-10')
        self.assertEqual(r['irrigation_net_mm'], 20.0)
        self.assertAlmostEqual(r['irrigation_gross_mm'], 25.0, delta=TOL)
        _, _, types = mods()
        with self.assertRaises(types.SimulationInputError):
            run(plan_items(('soy', date(2020, 10, 5))), forcing(date(2020, 10, 5), 115), irrigation_net_mm={date(2019, 1, 1): 5.0})

    def test_trajectory_identity(self):
        t = run(plan_items(('soy', date(2020, 10, 5))), forcing(date(2020, 10, 5), 115))
        self.assertEqual(t.engine_version, 'crop-simulation-v1')
        self.assertEqual(t.parameter_set_id, 'gaiapus-fao56-br-v1')
        self.assertEqual(t.daily_order, ('runoff', 'infiltration+irrigation', 'drainage', 'stress', 'eta'))
        self.assertEqual(t.evidence_status, 'physical_experimental')


class FrozenBucketEquivalence(unittest.TestCase):
    """Same configuration as the frozen data/models/water_balance.py bucket, no new features: identical storage."""

    def test_soy_and_maize2_match_the_frozen_bucket(self):
        from data.models import water_balance as wb
        for crop, sow in (('soy', date(2020, 10, 5)), ('maize2', date(2021, 2, 1))):
            n = sum(wb.CROPS[crop]['phase_days'])
            f = forcing(sow, n, rain=lambda i: 14.0 if i % 5 == 0 else 0.0, et0=lambda i: 3.0 + (i % 7))
            ref = wb.simulate([{'day_utc': d.date.isoformat(), 'rain_mm': d.precipitation_mm, 'et0_mm': d.et0_mm} for d in f], crop, 55.0, 0.7)
            new = run(plan_items((crop, sow)), f, storage=55.0 * 0.7).rows
            self.assertEqual(len(ref), len(new))
            for a, b in zip(ref, new):
                self.assertEqual(a['phase'], b['phase_code'])
                self.assertAlmostEqual(a['kc'], b['kc'], delta=1e-12)
                self.assertAlmostEqual(a['storage_end_mm'], b['storage_mm'], delta=1e-9)
                self.assertAlmostEqual(a['eta_mm'], b['eta_mm'], delta=1e-9)
                self.assertAlmostEqual(a['ks'], b['stress_coefficient'], delta=1e-12)


if __name__ == '__main__':
    unittest.main()
