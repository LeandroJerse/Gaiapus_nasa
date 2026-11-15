"""JSON service of the management comparison: request in, result out, deterministic and serialisable."""
import copy
import json
import unittest

from data.models.crop_simulation import service as svc
from test_simulation_comparison import members


def request(**kw):
    base = {
        'contract': 'management-comparison-request.v1', 'location_id': 'BR:ibge:5107925', 'parameter_set_id': 'gaiapus-fao56-br-v1',
        'soil': {'profile_id': 'AD4-hypothesis', 'capacity_mm': 55.0}, 'initial_storage_fraction': 0.55, 'reference_id': 'a', 'area_ha': 10.0,
        'alternatives': [
            {'alternative_id': 'a', 'name': 'Rainfed', 'crops': [{'crop_id': 'soy', 'sowing_rule': '10-05', 'year_offset': 0}, {'crop_id': 'maize2', 'sowing_rule': '01-28', 'year_offset': 1}]},
            {'alternative_id': 'b', 'name': 'Irrigated maize', 'crops': [{'crop_id': 'soy', 'sowing_rule': '10-05', 'year_offset': 0}, {'crop_id': 'maize2', 'sowing_rule': '01-28', 'year_offset': 1}],
             'irrigation': [{'crop_id': 'maize2', 'days_after_sowing': 60, 'net_mm': 20.0}, {'crop_id': 'maize2', 'days_after_sowing': 75, 'net_mm': 20.0}], 'irrigation_efficiency': 0.8}],
        'yield_sources': {'common': {'soy': {'origin': 'historical_reference', 'kg_ha': 3500.0, 'reference': 'synthetic series', 'validation_status': 'reference_not_a_forecast', 'period': '2019-2023', 'scale': 'municipality'},
                                     'maize2': {'origin': 'historical_reference', 'kg_ha': 6000.0, 'reference': 'synthetic series', 'validation_status': 'reference_not_a_forecast'}}},
        'budgets': {'common': {'soy': {'currency': 'BRL', 'price_per_unit': 120.0, 'price_unit': 'bag', 'unit_kg': 60.0, 'price_reference': 'user', 'price_date': '2026-11-01', 'cost_per_ha': 5000.0, 'cost_basis': 'user total'},
                               'maize2': {'currency': 'BRL', 'price_per_unit': 60.0, 'price_unit': 'bag', 'unit_kg': 60.0, 'price_reference': 'user', 'price_date': '2026-11-01', 'cost_per_ha': 3000.0, 'cost_basis': 'user total',
                                          'irrigation_cost_per_m3': 1.0}}}}
    base.update(kw)
    return base


def run(req=None, **kw):
    return svc.run_request(req or request(), members(), forcing_source='synthetic test forcing', **kw)


class Service(unittest.TestCase):
    def test_result_is_plain_json_identified_and_deterministic(self):
        one, two = run(), run()
        text = json.dumps(one, allow_nan=False, sort_keys=True)
        self.assertEqual(text, json.dumps(two, allow_nan=False, sort_keys=True))
        self.assertEqual(one['contract'], 'management-comparison-result.v1')
        self.assertRegex(one['comparison_id'], r'^mc_[0-9a-f]{24}$')
        self.assertEqual(one['comparison_id'], 'mc_' + one['identity']['sha256'][:24])
        self.assertEqual(one['identity']['request_sha256'], svc.request_digest(request()))
        self.assertEqual(one['location_id'], 'BR:ibge:5107925')
        self.assertEqual(one['evidence_status'], 'physical_experimental')
        self.assertEqual(one['forcing']['source'], 'synthetic test forcing')
        self.assertEqual(one['coverage'], {'members': 6, 'candidates': 6})
        self.assertNotIn('trajectories', text)                       # summaries only; daily series are a separate artefact
        self.assertLess(len(text), 120_000)
        self.assertNotIn('generated_at', one['identity'])

    def test_the_identity_changes_with_the_request_or_the_forcing_and_not_with_key_order(self):
        base = run()
        shuffled = json.loads(json.dumps(request(), sort_keys=True))
        self.assertEqual(run(shuffled)['comparison_id'], base['comparison_id'])
        other = request(); other['alternatives'][1]['irrigation'][0]['net_mm'] = 25.0
        self.assertNotEqual(run(other)['comparison_id'], base['comparison_id'])
        drier = svc.run_request(request(), members(wet=lambda y: .5), forcing_source='synthetic test forcing')
        self.assertNotEqual(drier['comparison_id'], base['comparison_id'])

    def test_water_money_and_origins_are_reported_per_alternative_and_harvest(self):
        out = run()
        a, b = out['alternatives']['a'], out['alternatives']['b']
        self.assertEqual(a['water']['irrigation_gross_mm']['mean'], 0)
        self.assertEqual(b['water']['irrigation_gross_mm']['mean'], 50.0)
        self.assertLessEqual(out['differences']['b']['water']['maize2.deficit_mm']['max'], 1e-9)
        hb = b['harvests']['maize2']
        self.assertEqual(hb['yield']['origin'], 'historical_reference')
        self.assertEqual(hb['yield']['management_effect'], 'not_estimated')
        self.assertEqual(hb['yield']['mean_kg_ha'], a['harvests']['maize2']['yield']['mean_kg_ha'])
        self.assertEqual(hb['money'], {'currency': 'BRL', 'revenue_per_ha': '6000.00', 'margin_per_ha': '2500.00', 'cost_per_ha': '3500.00', 'total_revenue': '60000.00', 'total_margin': '25000.00'})
        self.assertEqual(out['differences']['b']['money']['maize2']['margin_per_ha']['mean'], -500.0)
        self.assertEqual(out['yield_message'], 'same_reference_yield_management_effect_on_harvest_not_estimated')
        self.assertIsNone(out['ranking'])
        self.assertEqual(a['rotation']['margin_per_ha_mean'], 2000.0 + 3000.0)
        self.assertEqual(out['hypotheses']['initial_storage'], {'fraction': 0.55, 'basis': 'hypothesis_not_measured'})
        self.assertEqual(out['alternatives']['b']['config']['irrigation_efficiency'], 0.8)

    def test_without_economics_the_water_comparison_is_still_returned(self):
        req = request(); del req['yield_sources']; del req['budgets']; req['area_ha'] = None
        out = run(req)
        self.assertEqual(out['alternatives']['a']['harvests']['soy']['unavailable'], ['yield_source_missing', 'budget_missing'])
        self.assertIsNone(out['alternatives']['a']['rotation']['margin_per_ha_mean'])
        self.assertIn('maize2.deficit_mm', out['differences']['b']['water'])

    def test_invalid_requests_come_back_as_structured_errors_never_as_numbers(self):
        cases = {'INVALID_PARAMETER': [lambda r: r.update(reference_id='zz'), lambda r: r['alternatives'][1].update(irrigation_efficiency=0), lambda r: r.update(initial_storage_fraction=1.5),
                                       lambda r: r['soil'].update(capacity_mm=-1), lambda r: r.update(contract='other'), lambda r: r['alternatives'].append(copy.deepcopy(r['alternatives'][0])),
                                       lambda r: r['budgets']['common']['soy'].update(currency='R$'), lambda r: r['yield_sources']['common']['soy'].update(kg_ha=-5),
                                       lambda r: r.update(unknown_field=1), lambda r: r['alternatives'][0].update(unknown=2)],
                 'RESOURCE_LIMIT': [lambda r: r.update(alternatives=[dict(r['alternatives'][0], alternative_id=f'x{i}') for i in range(5)])],
                 'CROP_UNSUPPORTED': [lambda r: r['alternatives'][0]['crops'][0].update(crop_id='coffee')]}
        for code, mutations in cases.items():
            for i, mutate in enumerate(mutations):
                req = request(); mutate(req)
                out = svc.handle(req, members(), forcing_source='synthetic test forcing')
                self.assertEqual(out.get('error', {}).get('code'), code, f'{code} #{i}: {json.dumps(out)[:200]}')
                self.assertNotIn('alternatives', out)

    def test_members_are_cut_from_a_daily_series_without_inventing_days(self):
        import datetime as dt
        day0 = dt.date(2014, 1, 1)
        weather = {(day0 + dt.timedelta(days=i)).isoformat(): {'rain_mm': 1.0, 'et0_mm': 4.0} for i in range(365 * 4)}
        weather[dt.date(2016, 3, 1).isoformat()]['et0_mm'] = None
        got = svc.members_from_daily(weather, [2014, 2015, 2016, 2017], start_month_day='09-01', days=330)
        self.assertEqual(sorted(got), [2014, 2015, 2016])                # 2017 runs past the series: not a member
        self.assertEqual(got[2014][0].date, dt.date(2014, 9, 1)); self.assertEqual(len(got[2014]), 330)
        self.assertIsNone(next(d for d in got[2015] if d.date == dt.date(2016, 3, 1)).et0_mm)   # missing stays missing; the engine excludes the member


if __name__ == '__main__':
    unittest.main()


class RealSorriso(unittest.TestCase):
    """Real inputs (cached NASA POWER, IBGE PAM, Conab 2023/24). Skipped where the caches are not in the checkout."""

    @classmethod
    def setUpClass(cls):
        try:
            from data.ingest import management_comparison_case as case
            cls.request, cls.result = case.run()
        except FileNotFoundError:
            cls.result = None

    def setUp(self):
        if self.result is None:
            self.skipTest('real caches of Sorriso not available')

    def test_eighteen_real_seasons_and_the_same_reference_yield_for_both_managements(self):
        r = self.result
        self.assertEqual(r['coverage'], {'members': 18, 'candidates': 18})
        self.assertEqual(r['forcing']['kind'], 'historical_seasons_replayed')
        a, b = r['alternatives']['sequeiro'], r['alternatives']['irrigado']
        for crop in ('soy', 'maize2'):
            self.assertEqual(a['harvests'][crop]['yield']['mean_kg_ha'], b['harvests'][crop]['yield']['mean_kg_ha'])
            self.assertEqual(b['harvests'][crop]['yield']['origin'], 'historical_reference')
            self.assertEqual(b['harvests'][crop]['yield']['management_effect'], 'not_estimated')
            self.assertIsNone(b['harvests'][crop]['yield']['p10_kg_ha'])
        self.assertEqual(r['yield_message'], 'same_reference_yield_management_effect_on_harvest_not_estimated')

    def test_irrigation_changes_water_and_is_a_partial_budget_without_its_cost(self):
        r = self.result
        water = r['differences']['irrigado']['water']
        self.assertEqual(water['irrigation_gross_mm']['mean'], 50.0)
        self.assertLessEqual(water['maize2.deficit_mm']['max'], 1e-9)       # never a larger deficit
        self.assertLess(water['maize2.deficit_mm']['mean'], 0)
        self.assertEqual(water['soy.deficit_mm']['mean'], 0)                 # the soy before the irrigation is untouched
        self.assertGreaterEqual(water['drainage_mm']['min'], -1e-9)         # part of the water applied drains
        maize = r['alternatives']['irrigado']['harvests']['maize2']
        self.assertEqual(maize['margin_kind'], 'partial_budget_not_net_margin')
        self.assertIn('irrigation water and energy cost not informed', maize['cost_basis'])
        self.assertEqual(r['alternatives']['sequeiro']['harvests']['maize2']['margin_kind'], 'margin_over_declared_costs')
        self.assertIsNone(r['area_ha']); self.assertIsNone(maize['production_kg'])
        self.assertIn('area_missing', maize['unavailable'])
