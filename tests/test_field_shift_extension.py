"""Field Shift v2: third historical strategy (soy -> long bare fallow), common windows, paired differences, package.

Engine invariants run on a deterministic synthetic forcing (a test fixture, never evidence). The reproduction of the
published A/B results runs on the cached NASA POWER series of Sorriso and is skipped where that cache is absent.
"""
import csv
import hashlib
import json
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from data.ingest import field_shift_case as fsc
from data.ingest import field_shift_v2 as v2
from data.models import field_shift_forcing as forcing
from data.models.water_balance import CROPS

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / 'data/processed/field_shift_v1'
V1_SHA = {'reference_fallow_by_year.csv': '9ef291bad7a1ef363d6a3623f3ab60d2936ac889c9063c21e7a480d2168d903c',
          'brachiaria_by_year.csv': '231e1cbfd5a07a029b43bd5630b111ea505b06fb32b53f3fad9f2bcbd3be2429',
          'reference_fallow_daily.csv': '74acd2ffb46df5cc6452c76b195900cd8e6d1da9d202d4ec3d9410cfacaffa79',
          'brachiaria_daily.csv': 'f06417ea80d464110b0fa41ac3d3080fb4ad2ea0f237bb4638f16e84759c9633',
          'sorriso_field_shift.json': '33477192d8b1acb8976305c3013a9a397cf8f3bba4160c641e3062f08d02c548'}


def synthetic(first=date(2015, 1, 1), last=date(2022, 12, 31)):
    """Wet season October-April, dry season May-September, with a leap day inside; deterministic."""
    rows, day, i = {}, first, 0
    while day <= last:
        wet = day.month in (10, 11, 12, 1, 2, 3, 4)
        rain = (18.0 if i % 3 == 0 else 0.0) if wet else (4.0 if i % 23 == 0 else 0.0)
        rows[day.isoformat()] = {'day_utc': day.isoformat(), 'rain_mm': rain, 'et0_mm': 4.2 + .8 * math.sin(i / 58.0)}
        day += timedelta(days=1); i += 1
    return rows


def real_weather():
    try:
        from data.ingest import cohort_v5 as v5
        from data.ingest.cohort_v6 import RAW
        return v5.daily_weather(fsc.CODE, root=RAW)[0]
    except FileNotFoundError:
        return None


class ThirdStrategy(unittest.TestCase):
    def setUp(self):
        self.weather = synthetic()

    def test_three_strategies_are_declared_and_the_first_two_keep_their_ids(self):
        self.assertEqual(v2.SCENARIOS, ('reference_fallow', 'brachiaria', 'soy_long_fallow'))

    def test_unknown_scenario_is_refused(self):
        with self.assertRaises(ValueError):
            fsc.simulate_year(self.weather, 2015, 'crotalaria', fsc.CENTRAL, 30.0)

    def test_long_fallow_keeps_soy_and_has_no_maize(self):
        capacity = CROPS['soy']['cad_mm']['AD4']
        a, _, dates = fsc.simulate_year(self.weather, 2016, 'reference_fallow', fsc.CENTRAL, .7 * capacity)
        c, _, dates_c = fsc.simulate_year(self.weather, 2016, 'soy_long_fallow', fsc.CENTRAL, .7 * capacity)
        self.assertEqual(dates, dates_c)
        soy_a = [r for r in a if r['land_cover'] == 'soy']; soy_c = [r for r in c if r['land_cover'] == 'soy']
        self.assertEqual(soy_a, soy_c)  # same sowing, same calendar, same start: the soy cycle is identical
        self.assertEqual(len(soy_c), fsc.SOY_DAYS)
        self.assertEqual({r['land_cover'] for r in c}, {'soy', 'bare'})
        after = [r for r in c if r['land_cover'] == 'bare']
        self.assertTrue(all(r['stage'] == 'off_season_bare' and r['kc'] == .3 and not r['soil_covered'] for r in after))
        self.assertEqual(len(c), len(a))

    def test_absent_maize_is_null_never_zero(self):
        years, _ = v2.run(self.weather, 'soy_long_fallow', fsc.CENTRAL)
        for y in years:
            self.assertIsNone(y['maize_sowing'])
            self.assertIsNone(y['maize_deficit_mm'])
            self.assertIsNone(y['maize_stress_days'])
            self.assertEqual(y['maize_status'], 'not_grown')
            self.assertIsNotNone(y['soy_deficit_mm'])
        mean = v2.summary(years)
        self.assertIsNone(mean['maize_deficit_mm'])
        self.assertIsNone(mean['maize_stress_days'])
        ref = v2.run(self.weather, 'reference_fallow', fsc.CENTRAL)[0]
        self.assertEqual({y['maize_status'] for y in ref}, {'grown'})
        self.assertTrue(all(y['maize_deficit_mm'] is not None for y in ref))

    def test_water_persists_between_years_and_starts_like_the_reference(self):
        capacity = CROPS['soy']['cad_mm']['AD4']
        for scenario in v2.SCENARIOS:
            years, series = v2.run(self.weather, scenario, fsc.CENTRAL)
            labels = list(series)
            first = series[labels[0]][0]
            wet = min(capacity, .7 * capacity + first['rain_mm'])
            self.assertLessEqual(first['storage_end_mm'], wet + 1e-9, scenario)
            for prev, nxt, y in zip(labels, labels[1:], years):
                self.assertEqual(y['storage_at_next_sowing_mm'], series[prev][-1]['storage_end_mm'])
                # the day after the last day of a year is the first day of the next one: no reset to 0.7 x capacity
                self.assertEqual((date.fromisoformat(series[nxt][0]['date']) - date.fromisoformat(series[prev][-1]['date'])).days, 1)

    def test_mass_is_conserved_before_rounding_and_storage_stays_in_bounds(self):
        capacity = CROPS['soy']['cad_mm']['AD4']
        for scenario in v2.SCENARIOS:
            storage, total = .7 * capacity, 0
            for year in (2015, 2016, 2017, 2018, 2019, 2020):
                trace = []
                rows, end, _ = fsc.simulate_year(self.weather, year, scenario, fsc.CENTRAL, storage, trace=trace)
                self.assertEqual(len(trace), len(rows))
                for item in trace:
                    self.assertAlmostEqual(item['mass_residual_mm'], 0, places=9)
                    self.assertTrue(0 <= item['storage_end_mm'] <= capacity + 1e-12)
                    self.assertTrue(min(item['rain_mm'], item['etc_mm'], item['eta_mm'], item['drainage_mm']) >= 0)
                    self.assertTrue(-1e-12 <= item['et_deficit_mm'])
                balance = storage + sum(i['rain_mm'] for i in trace) - sum(i['eta_mm'] + i['drainage_mm'] for i in trace) - end
                self.assertAlmostEqual(balance, 0, places=8)
                storage = end; total += len(rows)
            self.assertEqual(total, (date(2021, 10, 5) - date(2015, 10, 5)).days)

    def test_dates_are_unique_contiguous_and_cross_a_leap_day(self):
        _, series = v2.run(self.weather, 'soy_long_fallow', fsc.CENTRAL)
        days = [r['date'] for rows in series.values() for r in rows]
        self.assertEqual(len(days), len(set(days)))
        parsed = [date.fromisoformat(d) for d in days]
        self.assertTrue(all((b - a).days == 1 for a, b in zip(parsed, parsed[1:])))
        self.assertIn('2016-02-29', days); self.assertIn('2020-02-29', days)
        self.assertEqual(len(series['2015/2016']), 366); self.assertEqual(len(series['2016/2017']), 365)

    def test_common_windows_have_the_same_length_for_every_strategy(self):
        runs = {s: v2.run(self.weather, s, fsc.CENTRAL)[0] for s in v2.SCENARIOS}
        for i in range(6):
            self.assertEqual(len({runs[s][i]['year_days'] for s in v2.SCENARIOS}), 1)
            self.assertEqual(len({runs[s][i]['post_soy_days'] for s in v2.SCENARIOS}), 1)
            self.assertEqual(len({runs[s][i]['year_rain_mm'] for s in v2.SCENARIOS}), 1)
            # the own off-season window changes with the strategy, which is why it is not the comparison window
            self.assertGreater(runs['soy_long_fallow'][i]['off_season_days'], runs['reference_fallow'][i]['off_season_days'])
            for s in v2.SCENARIOS:
                y = runs[s][i]
                self.assertEqual(y['covered_days'] + y['bare_days'], y['year_days'])
        self.assertTrue(all(y['covered_days'] == fsc.SOY_DAYS for y in runs['soy_long_fallow']))

    def test_incomplete_weather_gives_fewer_valid_years_and_a_reason_not_a_truncated_number(self):
        short = {k: v for k, v in self.weather.items() if k < '2020-03-01'}
        years, series = v2.run(short, 'soy_long_fallow', fsc.CENTRAL)
        self.assertEqual([y['agricultural_year'] for y in years], ['2015/2016', '2016/2017', '2017/2018', '2018/2019'])
        self.assertIsNone(years[-1]['next_soy_establishment_deficit_mm'])
        self.assertEqual(years[-1]['next_soy_establishment_status'], 'next_season_not_simulated')
        self.assertEqual(list(series), [y['agricultural_year'] for y in years])


class PairedDifferences(unittest.TestCase):
    def test_pairs_only_common_valid_years_and_reports_n_mean_and_range(self):
        ref = [{'agricultural_year': '2015/2016', 'x': 10.0, 'm': 5.0}, {'agricultural_year': '2016/2017', 'x': 20.0, 'm': 7.0}, {'agricultural_year': '2017/2018', 'x': 30.0, 'm': None}]
        alt = [{'agricultural_year': '2016/2017', 'x': 26.0, 'm': None}, {'agricultural_year': '2017/2018', 'x': 31.0, 'm': None}, {'agricultural_year': '2018/2019', 'x': 99.0, 'm': 1.0}]
        out = v2.paired(alt, ref, ('x', 'm'))
        self.assertEqual(out['x'], {'n': 2, 'mean': 3.5, 'min': 1.0, 'max': 6.0, 'years': ['2016/2017', '2017/2018'], 'status': 'ok'})
        self.assertEqual(out['m'], {'n': 0, 'mean': None, 'min': None, 'max': None, 'years': [], 'status': 'not_applicable'})


class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / 'pkg'
        cls.manifest = v2.write_package(synthetic(), cls.out, weather_source='synthetic test forcing')
        cls.summary = json.loads((cls.out / 'summary.json').read_text(encoding='utf-8'))
        cls.series = json.loads((cls.out / 'series.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_summary_series_and_manifest_carry_one_package_id(self):
        pid = self.manifest['package_id']
        self.assertRegex(pid, r'^field-shift-v2-[0-9a-f]{12}$')
        self.assertEqual(self.summary['package_id'], pid)
        self.assertEqual(self.series['package_id'], pid)
        for name, meta in self.manifest['files'].items():
            data = (self.out / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), meta['sha256'], name)
            self.assertEqual(len(data), meta['bytes'], name)
        self.assertIn('summary.json', self.manifest['files']); self.assertIn('series.json', self.manifest['files'])

    def test_package_id_depends_on_inputs_code_and_parameters_not_on_the_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            again = v2.write_package(synthetic(), Path(tmp) / 'a', weather_source='synthetic test forcing')
            self.assertEqual(again['package_id'], self.manifest['package_id'])
            self.assertEqual(again['files']['summary.json']['sha256'], self.manifest['files']['summary.json']['sha256'])
            wetter = v2.write_package(forcing.transform_rain(synthetic(), 1.2), Path(tmp) / 'b', weather_source='synthetic test forcing')
            self.assertNotEqual(wetter['package_id'], self.manifest['package_id'])

    def test_a_package_directory_is_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            v2.write_package(synthetic(), self.out, weather_source='synthetic test forcing')

    def test_summary_validates_against_the_versioned_schema(self):
        import jsonschema
        schema = json.loads((ROOT / 'contracts/field-shift-v2.schema.json').read_text(encoding='utf-8'))
        jsonschema.validate(self.summary, schema)
        broken = json.loads(json.dumps(self.summary)); broken['scenarios']['soy_long_fallow']['mean']['maize_deficit_mm'] = 0
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(broken, schema)

    def test_summary_states_scope_references_and_what_is_not_simulated(self):
        s = self.summary
        self.assertEqual(s['schema_version'], '2.0.0')
        self.assertEqual(s['reference_scenario'], 'reference_fallow')
        self.assertEqual(list(s['scenarios']), list(v2.SCENARIOS))
        self.assertIsNone(s['yield_effect'])
        self.assertEqual(s['scenarios']['soy_long_fallow']['crops'], ['soy'])
        self.assertEqual(s['scenarios']['soy_long_fallow']['kind'], 'fewer_crops_management_strategy')
        for alt in ('brachiaria', 'soy_long_fallow'):
            block = s['comparisons'][f'{alt}_minus_reference_fallow']
            self.assertEqual(block['year_rain_mm']['mean'], 0)
            self.assertEqual(block['covered_days']['n'], 6)
        self.assertEqual(s['comparisons']['soy_long_fallow_minus_reference_fallow']['maize_deficit_mm']['status'], 'not_applicable')
        self.assertIn('range across the simulated years, not a calibrated predictive interval', s['comparisons_note'])

    def test_rain_sensitivity_recomputes_every_strategy_on_the_same_perturbed_sequence(self):
        rain = self.summary['sensitivity']['rain']
        self.assertEqual([p['multiplier'] for p in rain['points']], [0.8, 1.0, 1.2])
        self.assertEqual(rain['label'], 'sensitivity test on historical weather, not a climate forecast')
        base = next(p for p in rain['points'] if p['multiplier'] == 1.0)
        for s in v2.SCENARIOS:
            self.assertEqual(base['scenarios'][s]['mean'], self.summary['scenarios'][s]['mean'])
        dry, wet = rain['points'][0], rain['points'][2]
        for point in rain['points']:
            for sc in v2.SCENARIOS:
                self.assertEqual(len(point['scenarios'][sc]['by_year']), point['scenarios'][sc]['valid_years'])
        self.assertEqual(base['scenarios']['brachiaria']['by_year'], self.summary['scenarios']['brachiaria']['by_year'])
        for s in v2.SCENARIOS:
            self.assertAlmostEqual(dry['scenarios'][s]['mean']['year_rain_mm'], round(base['scenarios'][s]['mean']['year_rain_mm'] * .8, 2), delta=.02)
            self.assertAlmostEqual(wet['scenarios'][s]['mean']['year_rain_mm'], round(base['scenarios'][s]['mean']['year_rain_mm'] * 1.2, 2), delta=.02)
            self.assertIsNone(dry['scenarios']['soy_long_fallow']['mean']['maize_deficit_mm'])
        self.assertEqual(self.summary['sensitivity']['temperature']['status'], 'not_calculated')

    def test_series_has_the_three_strategies_on_the_same_dates(self):
        cols = self.series['scenarios']
        self.assertEqual(list(cols), list(v2.SCENARIOS))
        dates = cols['reference_fallow']['date']
        for s in v2.SCENARIOS:
            self.assertEqual(cols[s]['date'], dates)
            self.assertEqual({len(v) for v in cols[s].values()}, {len(dates)})
        self.assertEqual(self.series['rain_multiplier'], 1.0)


class PublishedReproduction(unittest.TestCase):
    """A and B of the published v1 case, recomputed by the evolved code from the same inputs and parameters."""

    @classmethod
    def setUpClass(cls):
        cls.weather = real_weather()

    def setUp(self):
        if self.weather is None:
            self.skipTest('NASA POWER cache of Sorriso not available in this checkout')

    def test_v1_files_are_untouched(self):
        for name, digest in V1_SHA.items():
            self.assertEqual(hashlib.sha256((V1 / name).read_bytes()).hexdigest(), digest, name)

    def test_reference_and_brachiaria_reproduce_v1_value_for_value(self):
        for scenario in ('reference_fallow', 'brachiaria'):
            years, series = fsc.run(self.weather, scenario, fsc.CENTRAL)
            with (V1 / f'{scenario}_by_year.csv').open(encoding='utf-8', newline='') as stream:
                published = list(csv.DictReader(stream))
            self.assertEqual(len(years), len(published))
            for mine, theirs in zip(years, published):
                self.assertEqual({k: str(v) if v is not None else '' for k, v in mine.items()}, theirs)
            with (V1 / f'{scenario}_daily.csv').open(encoding='utf-8', newline='') as stream:
                daily = list(csv.DictReader(stream))
            flat = [{'agricultural_year': y, **r} for y, rows in series.items() for r in rows]
            self.assertEqual(len(flat), len(daily))
            for mine, theirs in zip(flat, daily):
                self.assertEqual({k: str(v) for k, v in mine.items()}, theirs)
            # the v2 indicators extend the v1 ones without changing them
            extended = v2.run(self.weather, scenario, fsc.CENTRAL)[0]
            for old, new in zip(years, extended):
                self.assertEqual({k: new[k] for k in old}, old)

    def test_shifted_sowing_has_six_valid_years_with_the_cached_weather(self):
        for shift in (-15, 0, 15):
            years, _ = v2.run(self.weather, 'soy_long_fallow', {**fsc.CENTRAL, 'soy_shift_days': shift})
            self.assertEqual(len(years), 6)
            self.assertTrue(all(y['next_soy_establishment_deficit_mm'] is not None for y in years))


if __name__ == '__main__':
    unittest.main()
