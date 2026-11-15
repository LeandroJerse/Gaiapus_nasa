"""Historical analog-block weather ensemble for future planning (synthetic histories; diagnostic only)."""
import gzip
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
AS_OF = datetime(2026, 11, 15, 12, tzinfo=UTC)


def we():
    from data.forecasting import weather_ensemble
    return weather_ensemble


def history(first=date(2001, 1, 1), last=date(2024, 12, 31), missing=(), rain=lambda d: float(d.toordinal() % 7)):
    w = we()
    days, d = [], first
    while d <= last:
        p = None if d in missing else rain(d)
        days.append(w.DailyWeather(d, p, 18.0 + (d.toordinal() % 5), 30.0 + (d.toordinal() % 3), 20.0, 2.0, 17.0, 96.0))
        d += timedelta(days=1)
    return w.WeatherHistory('BR:ibge:5107925', -12.55, 365.0, tuple(days), {'provider': 'synthetic', 'sha256': 'x' * 64})


TARGET = dict(target_start=date(2026, 11, 16), target_end=date(2028, 7, 31))


class EligibilityTests(unittest.TestCase):
    def test_eligible_years_need_the_whole_window_inside_history(self):
        e = we().build_ensemble(history(), as_of=AS_OF, seed=1, **TARGET)
        # 2022-11-16 + 623 days = 2024-07-31 (last complete window)
        self.assertEqual(e.manifest['analog_years'], list(range(2001, 2023)))
        self.assertEqual(len(e.members), 22)
        self.assertEqual(e.manifest['coverage_class'], 'standard')

    def test_missing_value_excludes_the_year_with_reason(self):
        e = we().build_ensemble(history(missing={date(2010, 3, 3)}), as_of=AS_OF, seed=1, **TARGET)
        # 2010-03-03 lies in windows starting 2008-11-16 and 2009-11-16
        self.assertNotIn(2008, e.manifest['analog_years'])
        self.assertNotIn(2009, e.manifest['analog_years'])
        self.assertEqual(e.manifest['excluded_years']['2009'], 'missing_values')
        for m in e.members:
            self.assertTrue(all(d.precipitation_mm is not None for d in m.days))

    def test_coverage_classes(self):
        w = we()
        limited = w.build_ensemble(history(first=date(2010, 1, 1)), as_of=AS_OF, seed=1, **TARGET)
        self.assertEqual(limited.manifest['coverage_class'], 'limited')  # 2010..2022 = 13 years
        with self.assertRaises(w.SimulationInputError) as ctx:
            w.build_ensemble(history(first=date(2016, 1, 1)), as_of=AS_OF, seed=1, **TARGET)
        self.assertEqual(ctx.exception.code, 'insufficient_history')

    def test_nothing_after_as_of_is_used(self):
        w = we()
        e = w.build_ensemble(history(last=date(2026, 12, 31)), as_of=datetime(2012, 6, 1, tzinfo=UTC), seed=1,
                             target_start=date(2012, 6, 2), target_end=date(2013, 1, 31))
        self.assertTrue(all(d.source_observation_date <= date(2012, 6, 1) for m in e.members for d in m.days))
        with self.assertRaises(w.SimulationInputError) as ctx:
            w.build_ensemble(history(), as_of=AS_OF, seed=1, target_start=date(2026, 11, 10), target_end=date(2027, 7, 1))
        self.assertEqual(ctx.exception.code, 'invalid_calendar')

    def test_explicit_analog_years_are_respected_and_validated(self):
        w = we()
        e = w.build_ensemble(history(), as_of=AS_OF, seed=1, analog_years=[2005, 2003], **TARGET)
        self.assertEqual([m.member_id for m in e.members], ['a2003', 'a2005'])
        self.assertEqual(e.manifest['analog_years_requested'], [2003, 2005])
        with self.assertRaises(w.SimulationInputError):
            w.build_ensemble(history(), as_of=AS_OF, seed=1, analog_years=[2023], **TARGET)


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.h = history()
        self.e = we().build_ensemble(self.h, as_of=AS_OF, seed=7, **TARGET)
        self.by_date = {d.date: d for d in self.h.days}

    def test_simulated_dates_are_contiguous_and_include_feb_29_2028(self):
        for m in self.e.members:
            sim = [d.simulated_date for d in m.days]
            self.assertEqual(sim[0], TARGET['target_start']); self.assertEqual(sim[-1], TARGET['target_end'])
            self.assertTrue(all(b - a == timedelta(days=1) for a, b in zip(sim, sim[1:])))
            self.assertIn(date(2028, 2, 29), sim)

    def test_each_day_keeps_all_variables_of_one_source_day_and_totals_are_preserved(self):
        for m in self.e.members:
            src = [d.source_observation_date for d in m.days]
            self.assertTrue(all(b - a == timedelta(days=1) for a, b in zip(src, src[1:])))  # consecutive block, no reshuffle
            for d in m.days:
                o = self.by_date[d.source_observation_date]
                self.assertEqual((d.precipitation_mm, d.tmin_c, d.tmax_c), (o.precipitation_mm, o.tmin_c, o.tmax_c))
            self.assertEqual(sum(d.precipitation_mm for d in m.days), sum(self.by_date[s].precipitation_mm for s in src))

    def test_month_day_drift_is_recorded_and_at_most_one_day(self):
        self.assertLessEqual(self.e.manifest['calendar_mapping']['max_month_day_drift_days'], 1)
        self.assertEqual(self.e.manifest['calendar_mapping']['method'], 'day_offset_from_aligned_start')

    def test_et0_is_computed_per_day(self):
        self.assertTrue(all(d.et0_mm is not None and d.et0_mm > 0 for m in self.e.members for d in m.days))

    def test_reproducible_and_seeded_subsampling(self):
        w = we()
        again = w.build_ensemble(self.h, as_of=AS_OF, seed=7, **TARGET)
        self.assertEqual(again.manifest['content_sha256'], self.e.manifest['content_sha256'])
        a = w.build_ensemble(self.h, as_of=AS_OF, seed=1, max_members=8, **TARGET)
        b = w.build_ensemble(self.h, as_of=AS_OF, seed=2, max_members=8, **TARGET)
        self.assertEqual(len(a.members), 8)
        self.assertNotEqual([m.member_id for m in a.members], [m.member_id for m in b.members])
        self.assertEqual(a.manifest['seed'], 1)
        self.assertEqual(a.manifest['effective_source_years'], 8)

    def test_segments_label_the_future_as_climatological_scenario(self):
        seg = self.e.manifest['weather_segments']
        self.assertEqual(seg, [{'kind': 'climatological_scenario', 'start': '2026-11-16', 'end': '2028-07-31',
                                'origin': 'historical_analog_blocks_v1 over synthetic'}])


class CompositionTests(unittest.TestCase):
    def test_observed_prefix_then_scenario(self):
        w = we()
        e = w.build_ensemble(history(), as_of=AS_OF, seed=1, **TARGET)
        obs = [w.DailyWeather(date(2026, 10, 1) + timedelta(days=i), 1.0, 20.0, 31.0, 20.0, 2.0, 17.0, 96.0) for i in range(46)]
        c = w.compose_with_observed(e, w.WeatherHistory('BR:ibge:5107925', -12.55, 365.0, tuple(obs), {'provider': 'obs'}), as_of=AS_OF)
        m = c.members[0]
        self.assertEqual(m.days[0].simulated_date, date(2026, 10, 1))
        self.assertEqual(m.days[45].simulated_date, date(2026, 11, 15))
        self.assertEqual(m.days[46].simulated_date, date(2026, 11, 16))
        self.assertTrue(all(d.kind == 'observed' for d in m.days[:46]))
        self.assertEqual([s['kind'] for s in c.manifest['weather_segments']], ['observed', 'climatological_scenario'])

    def test_gap_or_future_observation_rejected(self):
        w = we()
        e = w.build_ensemble(history(), as_of=AS_OF, seed=1, **TARGET)
        gap = [w.DailyWeather(date(2026, 10, 1) + timedelta(days=i), 1.0, 20.0, 31.0, 20.0, 2.0, 17.0, 96.0) for i in range(40)]
        with self.assertRaises(w.SimulationInputError):
            w.compose_with_observed(e, w.WeatherHistory('x', -12.55, 365.0, tuple(gap), {}), as_of=AS_OF)
        late = [w.DailyWeather(date(2026, 10, 1) + timedelta(days=i), 1.0, 20.0, 31.0, 20.0, 2.0, 17.0, 96.0) for i in range(47)]
        with self.assertRaises(w.SimulationInputError):
            w.compose_with_observed(e, w.WeatherHistory('x', -12.55, 365.0, tuple(late), {}), as_of=AS_OF)


class PowerFileTests(unittest.TestCase):
    def test_power_json_sentinel_becomes_missing_and_file_is_hashed(self):
        w = we()
        keys = ['20240101', '20240102']
        payload = {'geometry': {'coordinates': [-55.9, -12.55, 365.0]}, 'properties': {'parameter': {
            'IMERG_PRECTOT': {keys[0]: 2.0, keys[1]: -999.0}, 'T2M_MIN': {k: 19.0 for k in keys}, 'T2M_MAX': {k: 31.0 for k in keys},
            'ALLSKY_SFC_SW_DWN': {k: 20.0 for k in keys}, 'WS2M': {k: 2.0 for k in keys}, 'T2MDEW': {k: 17.0 for k in keys}, 'PS': {k: 96.0 for k in keys}}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'power_5107925.json'; p.write_text(json.dumps(payload), encoding='utf-8')
            h = w.PowerPointFile(p).load('BR:ibge:5107925')
            self.assertEqual(h.days[0].precipitation_mm, 2.0)
            self.assertIsNone(h.days[1].precipitation_mm)
            self.assertEqual(len(h.source['sha256']), 64)
            self.assertEqual(h.source['product'], 'NASA POWER daily point, community AG (rain IMERG_PRECTOT)')
            self.assertEqual((h.latitude, h.elevation_m), (-12.55, 365.0))


if __name__ == '__main__':
    unittest.main()


class PassThroughTests(unittest.TestCase):
    def test_scenario_day_carries_all_variables_and_source_extras(self):
        w = we()
        days, d = [], date(2001, 1, 1)
        while d <= date(2024, 12, 31):
            days.append(w.DailyWeather(d, 1.0, 18.0, 30.0, 20.0, 2.5, 17.0, 96.0, extras={'rh2m_pct': 70.0 + d.day}))
            d += timedelta(days=1)
        h = w.WeatherHistory('US:fips:19169', 42.0, 300.0, tuple(days), {'provider': 'synthetic'})
        e = w.build_ensemble(h, as_of=AS_OF, seed=1, **TARGET)
        x = e.members[0].days[10]
        self.assertEqual((x.radiation_mj_m2, x.wind_m_s, x.dewpoint_c, x.pressure_kpa), (20.0, 2.5, 17.0, 96.0))
        self.assertEqual(x.extras, {'rh2m_pct': 70.0 + x.source_observation_date.day})

    def test_power_file_passes_rh2m_as_extra(self):
        w = we()
        keys = ['20240101']
        payload = {'geometry': {'coordinates': [-55.9, -12.55, 365.0]}, 'properties': {'parameter': {
            'IMERG_PRECTOT': {keys[0]: 2.0}, 'T2M_MIN': {keys[0]: 19.0}, 'T2M_MAX': {keys[0]: 31.0}, 'ALLSKY_SFC_SW_DWN': {keys[0]: 20.0},
            'WS2M': {keys[0]: 2.0}, 'T2MDEW': {keys[0]: 17.0}, 'PS': {keys[0]: 96.0}, 'RH2M': {keys[0]: -999.0}}}}
        with tempfile.TemporaryDirectory() as dd:
            p = Path(dd) / 'p.json'; p.write_text(json.dumps(payload), encoding='utf-8')
            self.assertEqual(w.PowerPointFile(p).load('x').days[0].extras, {'rh2m_pct': None})
