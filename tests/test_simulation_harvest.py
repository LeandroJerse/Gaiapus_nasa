"""Harvest outcome per management: yield origin, production, revenue and margin. Synthetic numbers; not results of any place."""
import math
import unittest

from data.models.crop_simulation import comparison as cmp
from data.models.crop_simulation import harvest as hv
from test_simulation_comparison import IRRIGATED, SOY, alt, members, request


def budget(**kw):
    args = dict(currency='BRL', price_per_unit=120.0, price_unit='bag', unit_kg=60.0, price_reference='user', price_date='2026-11-01',
                cost_per_ha=5000.0, cost_basis='user total cost per hectare', irrigation_cost_per_m3=0.0)
    args.update(kw)
    return hv.HarvestBudget(**args)


def reference(kg_ha=3500.0, **kw):
    return hv.YieldSource(origin='historical_reference', crop_id='soy', kg_ha=kg_ha, reference='synthetic municipal series', period='2019-2023',
                          scale='municipality', validation_status='reference_not_a_forecast', **kw)


class Prices(unittest.TestCase):
    def test_units_are_explicit_and_never_assumed(self):
        self.assertEqual(budget().price_per_kg, 2.0)
        self.assertEqual(budget(price_unit='kg', price_per_unit=2.5, unit_kg=None).price_per_kg, 2.5)
        self.assertEqual(budget(price_unit='t', price_per_unit=2000.0, unit_kg=None).price_per_kg, 2.0)
        self.assertAlmostEqual(budget(price_unit='bushel', price_per_unit=10.0, unit_kg=27.2155, currency='USD').price_per_kg, 10 / 27.2155)
        for bad in (dict(unit_kg=None), dict(price_unit='bushel', unit_kg=None), dict(price_unit='saco'), dict(currency='R$'), dict(price_per_unit=math.nan),
                    dict(cost_per_ha=-1.0), dict(unit_kg=0.0), dict(price_reference=''), dict(cost_per_kg=math.inf), dict(price_unit='kg', unit_kg=60.0)):
            with self.assertRaises(ValueError, msg=bad):
                budget(**bad)

    def test_missing_price_or_cost_is_not_zero(self):
        b = budget(price_per_unit=None)
        self.assertIsNone(b.price_per_kg)
        out = hv.outcome_single(reference(), b, area_ha=10.0, irrigation_gross_mm=0.0)
        self.assertEqual(out['production_kg'], 35000.0)
        self.assertIsNone(out['revenue_per_ha']); self.assertIsNone(out['margin_per_ha'])
        self.assertIn('price_missing', out['unavailable'])
        partial = hv.outcome_single(reference(), budget(cost_per_ha=None), area_ha=10.0, irrigation_gross_mm=0.0)
        self.assertEqual(partial['revenue_per_ha'], 7000.0)
        self.assertIsNone(partial['margin_per_ha']); self.assertIn('cost_missing', partial['unavailable'])
        incomplete = hv.outcome_single(reference(), budget(costs_complete=False), area_ha=10.0, irrigation_gross_mm=0.0)
        self.assertEqual(incomplete['margin_kind'], 'partial_budget_not_net_margin')


class WorkedExample(unittest.TestCase):
    """Plan section 8.4: 3,500 kg/ha, 10 ha, BRL 120 per declared 60 kg bag, cost BRL 5,000/ha."""

    def test_the_arithmetic_of_the_plan(self):
        out = hv.outcome_single(reference(), budget(), area_ha=10.0, irrigation_gross_mm=0.0)
        self.assertEqual((out['price_per_kg'], out['production_kg']), (2.0, 35000.0))
        self.assertEqual((out['revenue_per_ha'], out['total_revenue']), (7000.0, 70000.0))
        self.assertEqual((out['margin_per_ha'], out['total_margin']), (2000.0, 20000.0))
        self.assertEqual(out['break_even_kg_ha'], 2500.0)
        self.assertEqual(out['margin_kind'], 'margin_over_declared_costs')
        self.assertEqual(out['money'], {'currency': 'BRL', 'revenue_per_ha': '7000.00', 'margin_per_ha': '2000.00', 'cost_per_ha': '5000.00', 'total_revenue': '70000.00', 'total_margin': '20000.00'})

    def test_irrigation_adds_cost_and_no_harvest_gain_is_invented(self):
        dry = hv.outcome_single(reference(), budget(irrigation_cost_per_m3=1.0), area_ha=10.0, irrigation_gross_mm=0.0)
        wet = hv.outcome_single(reference(), budget(irrigation_cost_per_m3=1.0), area_ha=10.0, irrigation_gross_mm=30.0)
        self.assertEqual(wet['irrigation_cost_per_ha'], 300.0)
        self.assertEqual((dry['margin_per_ha'], wet['margin_per_ha']), (2000.0, 1700.0))
        self.assertEqual(wet['yield']['mean_kg_ha'], dry['yield']['mean_kg_ha'])
        self.assertEqual(wet['yield']['management_effect'], 'not_estimated')

    def test_costs_that_depend_on_the_quantity_change_the_break_even(self):
        out = hv.outcome_single(reference(), budget(cost_per_kg=0.5), area_ha=None, irrigation_gross_mm=0.0)
        self.assertEqual(out['cost_per_ha'], 5000.0 + 0.5 * 3500.0)
        self.assertAlmostEqual(out['break_even_kg_ha'], 5000.0 / (2.0 - 0.5))
        self.assertIsNone(out['production_kg']); self.assertIsNone(out['total_margin'])
        self.assertIn('area_missing', out['unavailable'])
        self.assertIsNone(hv.outcome_single(reference(), budget(cost_per_kg=2.0), area_ha=1.0, irrigation_gross_mm=0.0)['break_even_kg_ha'])  # never breaks even
        self.assertIsNone(hv.outcome_single(reference(), budget(price_per_unit=0.0), area_ha=1.0, irrigation_gross_mm=0.0)['break_even_kg_ha'])


class Sources(unittest.TestCase):
    def test_the_first_usable_origin_wins_and_the_reasons_of_the_others_are_kept(self):
        model = hv.YieldSource(origin='model', crop_id='soy', kg_ha=None, reference='soy_mg_mt', validation_status='research', unusable_reason='not_trained_for_pre_sowing_planning')
        user = hv.YieldSource(origin='user_hypothesis', crop_id='soy', kg_ha=3900.0, reference='informed by the grower', validation_status='user_hypothesis')
        chosen = hv.resolve_yield('soy', [model, reference(), user])
        self.assertEqual(chosen.origin, 'historical_reference')
        self.assertEqual(chosen.fallback_reasons, ('model: not_trained_for_pre_sowing_planning',))
        self.assertFalse(chosen.responds_to_management)
        none = hv.resolve_yield('soy', [model])
        self.assertEqual((none.origin, none.kg_ha), ('unavailable', None))
        out = hv.outcome_single(none, budget(), area_ha=10.0, irrigation_gross_mm=0.0)
        self.assertIsNone(out['yield']['mean_kg_ha']); self.assertIsNone(out['revenue_per_ha']); self.assertIsNone(out['production_kg'])
        self.assertEqual(out['cost_per_ha'], 5000.0)
        self.assertIn('yield_unavailable', out['unavailable'])
        with self.assertRaises(ValueError):
            hv.resolve_yield('soy', [hv.YieldSource(origin='historical_reference', crop_id='maize2', kg_ha=6000.0, reference='x', validation_status='reference_not_a_forecast')])
        for bad in (dict(kg_ha=-1.0), dict(kg_ha=math.nan), dict(origin='guess')):
            with self.assertRaises(ValueError, msg=bad):
                reference(**bad) if 'origin' not in bad else hv.YieldSource(origin='guess', crop_id='soy', kg_ha=1.0, reference='x', validation_status='x')

    def test_a_single_reference_has_no_interval_and_no_frequency_of_loss(self):
        out = hv.outcome_members(reference(), budget(), area_ha=10.0, irrigation_gross_mm={2015: 0.0, 2016: 0.0, 2017: 0.0})
        y = out['yield']
        self.assertEqual((y['mean_kg_ha'], y['p50_kg_ha'], y['p10_kg_ha'], y['p90_kg_ha']), (3500.0, None, None, None))
        self.assertEqual(y['dispersion_kind'], 'none_single_reference')
        self.assertEqual(y['members'], {'valid': 0, 'total': 3})
        self.assertIsNone(out['negative_margin_frequency'])
        self.assertEqual(out['margin']['dispersion_kind'], 'none')
        self.assertEqual(out['margin']['mean_per_ha'], 2000.0)
        self.assertIsNone(out['margin']['p10_per_ha'])

    def test_irrigation_cost_that_varies_by_member_is_labelled_as_cost_spread_only(self):
        out = hv.outcome_members(reference(), budget(irrigation_cost_per_m3=1.0), area_ha=10.0, irrigation_gross_mm={2015: 0.0, 2016: 30.0, 2017: 60.0})
        self.assertEqual(out['margin']['dispersion_kind'], 'irrigation_cost_only')
        self.assertEqual(out['margin']['mean_per_ha'], 1700.0)
        self.assertEqual((out['margin']['min_per_ha'], out['margin']['max_per_ha']), (1400.0, 2000.0))
        self.assertIsNone(out['negative_margin_frequency'])

    def test_per_member_yield_gives_mean_median_spread_and_frequency_with_denominator(self):
        src = hv.YieldSource(origin='experimental_model', crop_id='soy', kg_ha=None, per_member={2015: 2000.0, 2016: 3000.0, 2017: 4000.0, 2018: None}, reference='synthetic chain',
                             validation_status='experimental', responds_to_management=True)
        out = hv.outcome_members(src, budget(), area_ha=10.0, irrigation_gross_mm={2015: 0.0, 2016: 0.0, 2017: 0.0, 2018: 0.0})
        y = out['yield']
        self.assertEqual((y['mean_kg_ha'], y['p50_kg_ha']), (3000.0, 3000.0))
        self.assertEqual(y['members'], {'valid': 3, 'total': 4})
        self.assertEqual(y['dispersion_kind'], 'climate_members_not_a_predictive_interval')
        self.assertEqual(y['weights'], 'equal')
        self.assertEqual(out['negative_margin_frequency'], {'negative': 1, 'members': 3, 'kind': 'frequency_in_simulated_members_not_probability'})
        self.assertEqual(out['margin']['mean_per_ha'], 1000.0)
        self.assertEqual(out['production_kg'], 30000.0)
        self.assertEqual(out['yield']['management_effect'], 'not_estimated')  # a flag alone declares nothing; see test_simulation_harvest_review


class ComparedHarvests(unittest.TestCase):
    def test_same_reference_yield_for_every_management_water_and_cost_still_differ(self):
        result = cmp.run_comparison(request([alt('a'), alt('b', **IRRIGATED)]), members())
        sources = {'soy': reference(), 'maize2': hv.YieldSource(origin='historical_reference', crop_id='maize2', kg_ha=6000.0, reference='synthetic municipal series', validation_status='reference_not_a_forecast')}
        budgets = {'soy': budget(), 'maize2': budget(price_per_unit=60.0, cost_per_ha=3000.0, irrigation_cost_per_m3=1.0)}
        out = hv.compare_harvests(result, sources, budgets, area_ha=10.0)
        a, b = out['alternatives']['a'], out['alternatives']['b']
        self.assertEqual(a['harvests']['maize2']['yield']['mean_kg_ha'], b['harvests']['maize2']['yield']['mean_kg_ha'])
        self.assertEqual(a['harvests']['maize2']['margin']['mean_per_ha'], 3000.0)
        self.assertEqual(b['harvests']['maize2']['margin']['mean_per_ha'], 3000.0 - 500.0)  # 40 mm net / 0.8 = 50 mm gross = 500 m3/ha
        self.assertEqual(out['differences']['b']['maize2']['margin_per_ha']['mean'], -500.0)
        self.assertEqual(out['differences']['b']['soy']['margin_per_ha']['mean'], 0.0)
        self.assertEqual(out['yield_message'], 'same_reference_yield_management_effect_on_harvest_not_estimated')
        self.assertEqual(b['rotation']['margin_per_ha_mean'], a['rotation']['margin_per_ha_mean'] - 500.0)
        self.assertEqual(a['rotation']['note'], 'money is added across harvests; kg/ha of different crops is never added')
        self.assertNotIn('yield_kg_ha', a['rotation'])
        self.assertIsNone(out['ranking'])  # no winner is named

    def test_a_crop_absent_from_one_management_has_no_harvest_there(self):
        result = cmp.run_comparison(request([alt('a'), alt('c', crops=SOY)]), members())
        sources = {'soy': reference(), 'maize2': hv.YieldSource(origin='historical_reference', crop_id='maize2', kg_ha=6000.0, reference='x', validation_status='reference_not_a_forecast')}
        out = hv.compare_harvests(result, sources, {'soy': budget(), 'maize2': budget(price_per_unit=60.0, cost_per_ha=3000.0)}, area_ha=None)
        self.assertEqual(list(out['alternatives']['c']['harvests']), ['soy'])
        self.assertNotIn('maize2', out['differences']['c'])
        self.assertEqual(out['not_comparable']['c'], ['maize2'])


if __name__ == '__main__':
    unittest.main()
