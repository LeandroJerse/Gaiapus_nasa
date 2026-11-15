"""Future-planning orchestrator (protocol future_simulation_v1). Synthetic weather; diagnostic only."""
import unittest
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
AS_OF = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)
ISSUED = datetime(2026, 11, 15, 12, tzinfo=UTC)


def fs():
    from data.serving import future_simulation
    return future_simulation


def weather(first, last, rain=lambda d: 8.0 if d.toordinal() % 4 == 0 and d.month in (10, 11, 12, 1, 2, 3) else 0.0, missing=()):
    from data.forecasting.weather_ensemble import DailyWeather, WeatherHistory
    days, d = [], first
    while d <= last:
        days.append(DailyWeather(d, None if d in missing else rain(d), 19.0, 31.0, 20.0, 2.0, 17.0, 96.0))
        d += timedelta(days=1)
    return WeatherHistory('BR:ibge:5107925', -12.6, 370.0, tuple(days), {'provider': 'synthetic', 'product': 'synthetic', 'sha256': 'h' * 64})


def place(state='51', observed=True, labels=None):
    from data.models.crop_simulation.types import SoilProfile
    f = fs()
    return f.PlaceSpec(location_id='BR:ibge:5107925', name='Sorriso', state_code=state,
                       history=weather(date(2001, 1, 1), date(2024, 12, 31)),
                       observed=weather(date(2026, 6, 1), date(2026, 8, 31)) if observed else None,
                       soil=SoilProfile(55.0, 'AD4-hypothesis'),
                       labels=labels if labels is not None else {('soy', y): 3000.0 + 10 * y % 7 for y in range(2001, 2025)})


class CalendarTests(unittest.TestCase):
    def test_soy_calendar_comes_from_the_state(self):
        f = fs()
        mt = f.plan_seasons('51', 'soy', [2027, 2028])
        self.assertEqual([i.sowing_date for i in mt.items], [date(2026, 10, 5), date(2027, 10, 5)])
        mg = f.plan_seasons('31', 'soy', [2027])
        self.assertEqual(mg.items[0].sowing_date, date(2026, 11, 5))  # MG is not MT

    def test_rotation_shifts_safrinha_after_soy_harvest_and_records_the_rule(self):
        p = fs().plan_seasons('51', 'soy>maize2', [2027])
        soy, maize = p.items
        self.assertEqual(maize.crop_id, 'maize2')
        self.assertEqual(maize.sowing_date, date(2027, 1, 28))  # MT calendar 01-05 overlaps soy harvest 2027-01-27
        self.assertIn('maize2_shifted_after_previous_harvest', [r['rule'] for r in p.rules])

    def test_unsupported_crops_have_reasons(self):
        f = fs()
        for crop, reason in [('rice', 'flooded_system_not_represented'), ('coffee_arabica', 'perennial_engine_missing'),
                             ('coffee_total_mg', 'unsupported'), ('maize1', 'unsupported_crop_engine'), ('soy', 'invalid_calendar')]:
            state = '99' if crop == 'soy' else '51'
            with self.assertRaises(f.SimulationInputError) as ctx:
                f.plan_seasons(state, crop, [2027])
            self.assertEqual(ctx.exception.code, reason, crop)


class RunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fs().run_future_simulation(place(), plan_id='soy>maize2', harvest_years=[2027, 2028], as_of=AS_OF, issued_at=ISSUED, seed=42)

    def test_identity_and_statuses(self):
        r = self.result['run']
        self.assertTrue(r['run_id'].startswith('sim_'))
        self.assertEqual(r['execution_status'], 'succeeded')
        self.assertEqual(r['output_mode'], 'simulation_conditional_on_historical_weather')
        self.assertEqual(r['scientific_status']['water_balance'], 'physical_experimental')
        self.assertEqual(r['as_of'], AS_OF.isoformat())
        self.assertEqual(r['issued_at'], ISSUED.isoformat())
        self.assertEqual([s['kind'] for s in r['weather_segments']], ['observed', 'climatological_scenario'])
        self.assertEqual(r['ensemble']['members'], 22)
        self.assertEqual(r['ensemble']['effective_source_years'], 22)

    def test_every_member_conserves_water_and_stays_in_bounds(self):
        for m in self.result['series']['members']:
            self.assertTrue(all(abs(x) <= 1e-8 for x in m['mass_balance_residual_mm']))
            self.assertTrue(all(0.0 <= s <= 55.0 for s in m['storage_mm']))
        self.assertIn('2028-02-29', self.result['series']['dates'])

    def test_seasons_are_reported_per_crop_and_harvest_year(self):
        seasons = {(s['crop_id'], s['harvest_year']): s for s in self.result['run']['seasons']}
        self.assertEqual(set(seasons), {('soy', 2027), ('soy', 2028), ('maize2', 2027), ('maize2', 2028)})
        s = seasons[('soy', 2027)]
        self.assertEqual((s['sowing_date'], s['expected_harvest_date']), ('2026-10-05', '2027-01-27'))
        self.assertEqual(s['season_label'], '2026/2027')
        q = s['quantiles']['crop_deficit_mm']
        self.assertLessEqual(q['p10'], q['p50']); self.assertLessEqual(q['p50'], q['p90'])
        for ev in s['events']:
            self.assertTrue(0.0 <= ev['share_of_members'] <= 1.0)
            self.assertIn(ev['event_id'], ('reproductive_stress_days_ge_10', 'crop_deficit_mm_ge_100', 'dry_spell_ge_15_days_in_season'))
        self.assertEqual(s['interval_method'], 'climate_ensemble_spread_only')

    def test_representative_member_is_a_real_member_and_bands_are_separate(self):
        r = self.result['run']; ids = [m['member_id'] for m in self.result['series']['members']]
        for k in ('central', 'dry', 'wet'):
            self.assertIn(r['representative'][k], ids)
        b = self.result['series']['bands']['storage_mm']
        self.assertEqual(len(b['p50']), len(self.result['series']['dates']))

    def test_yield_is_a_reference_that_never_reads_sealed_or_unpublished_labels(self):
        y = {(s['crop_id'], s['harvest_year']): s['yield'] for s in self.result['run']['seasons']}
        soy = y[('soy', 2027)]
        self.assertEqual(soy['status'], 'reference')
        self.assertEqual(soy['last_label_year'], 2021)  # 2022-2024 are the sealed test
        self.assertEqual(soy['history_horizon_years'], 6)
        self.assertEqual(soy['years_used'], [2017, 2018, 2019, 2020, 2021])
        self.assertIn('sealed_test_2022_2024_not_read', soy['exposure_flags'])
        self.assertEqual(soy['conditional_on_scenario'], False)
        self.assertEqual(y[('maize2', 2027)], {'status': 'unavailable', 'reason_code': 'source_unavailable', 'value_kg_ha': None})

    def test_initial_condition_sensitivity_is_reported(self):
        sens = self.result['run']['initial_condition']
        self.assertEqual(sens['storage_fraction'], 0.5)
        self.assertEqual(sorted(sens['sensitivity'].keys()), ['0.2', '0.9'])
        self.assertEqual(sens['warm_up'], {'start': '2026-06-01', 'end': '2026-08-31', 'source': 'observed'})


class ReproducibilityTests(unittest.TestCase):
    def test_same_request_same_run_id_and_checksums(self):
        f = fs()
        a = f.run_future_simulation(place(), plan_id='soy', harvest_years=[2027], as_of=AS_OF, issued_at=ISSUED, seed=1)
        b = f.run_future_simulation(place(), plan_id='soy', harvest_years=[2027], as_of=AS_OF, issued_at=ISSUED + timedelta(hours=5), seed=1)
        self.assertEqual(a['run']['run_id'], b['run']['run_id'])  # issued_at is not part of the scientific identity
        self.assertEqual(a['run']['checksums']['series_sha256'], b['run']['checksums']['series_sha256'])
        c = f.run_future_simulation(place(), plan_id='soy', harvest_years=[2027], as_of=AS_OF, issued_at=ISSUED, seed=1, max_members=10)
        self.assertNotEqual(a['run']['run_id'], c['run']['run_id'])

    def test_season_already_started_without_observations_is_missing_data(self):
        f = fs()
        with self.assertRaises(f.SimulationInputError) as ctx:
            f.run_future_simulation(place(observed=False), plan_id='soy', harvest_years=[2027], as_of=datetime(2026, 11, 15, tzinfo=UTC), issued_at=ISSUED, seed=1)
        self.assertEqual(ctx.exception.code, 'season_already_started_without_observations')


if __name__ == '__main__':
    unittest.main()


class StrengthenedTests(unittest.TestCase):
    def test_pam_not_yet_published_at_as_of_is_not_read(self):
        f = fs()
        labels = {('soy', y): 3000.0 + y for y in range(2001, 2026)}  # includes 2025 (not sealed)
        early = f.run_future_simulation(place(labels=labels), plan_id='soy', harvest_years=[2027], as_of=AS_OF, issued_at=ISSUED, seed=1, max_members=10)
        self.assertEqual(early['run']['seasons'][0]['yield']['last_label_year'], 2021)  # PAM 2025 only by 31 Oct 2026
        late = f.run_future_simulation(place(labels=labels, observed=False), plan_id='soy', harvest_years=[2028],
                                       as_of=datetime(2026, 11, 15, tzinfo=UTC), issued_at=ISSUED, seed=1, max_members=10)
        y = late['run']['seasons'][0]['yield']
        self.assertEqual(y['last_label_year'], 2025)
        self.assertEqual(y['years_used'], [2018, 2019, 2020, 2021, 2025])

    def test_dry_and_wet_members_are_the_extremes_of_crop_deficit(self):
        r = fs().run_future_simulation(place(), plan_id='soy', harvest_years=[2027, 2028], as_of=AS_OF, issued_at=ISSUED, seed=1)['run']
        total = {}
        for s in r['seasons']:
            for m, v in s['members'].items():
                total[m] = total.get(m, 0.0) + v['crop_deficit_mm']
        self.assertEqual(total[r['representative']['dry']], max(total.values()))
        self.assertEqual(total[r['representative']['wet']], min(total.values()))


class SerialisationTests(unittest.TestCase):
    def test_run_and_series_are_plain_json(self):
        import json
        r = fs().run_future_simulation(place(), plan_id='soy', harvest_years=[2027], as_of=AS_OF, issued_at=ISSUED, seed=1, max_members=10)
        json.dumps(r['run']); json.dumps(r['series'])
