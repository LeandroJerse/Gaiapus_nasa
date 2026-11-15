"""Regressions for the review of harvest.py: inputs per alternative, coverage, currencies, costs without yield, Decimal money."""
import unittest
from decimal import Decimal

from data.models.crop_simulation import comparison as cmp
from data.models.crop_simulation import harvest as hv
from test_simulation_comparison import IRRIGATED, alt, members, request
from test_simulation_harvest import budget, reference

MAIZE = hv.YieldSource(origin='historical_reference', crop_id='maize2', kg_ha=6000.0, reference='synthetic municipal series', validation_status='reference_not_a_forecast')
MAIZE_BUDGET = budget(price_per_unit=60.0, cost_per_ha=3000.0, irrigation_cost_per_m3=1.0)


def compared(**kw):
    result = cmp.run_comparison(request([alt('a'), alt('b', **IRRIGATED)]), members())
    args = dict(sources={'soy': reference(), 'maize2': MAIZE}, budgets={'soy': budget(), 'maize2': MAIZE_BUDGET}, area_ha=10.0)
    args.update(kw)
    return result, hv.compare_harvests(result, **args)


class PerAlternative(unittest.TestCase):
    def test_budget_per_alternative_and_harvest_keeps_the_common_one_for_the_others(self):
        costly = budget(price_per_unit=60.0, cost_per_ha=3400.0, irrigation_cost_per_m3=1.0, cost_basis='user cost with pivot maintenance')
        _, out = compared(budgets_by_alternative={'b': {'maize2': costly}})
        a, b = out['alternatives']['a']['harvests'], out['alternatives']['b']['harvests']
        self.assertEqual(a['maize2']['cost_basis'], 'user total cost per hectare')
        self.assertEqual(b['maize2']['cost_basis'], 'user cost with pivot maintenance')
        self.assertEqual(b['maize2']['inputs_scope'], {'budget': 'alternative', 'yield': 'common'})
        self.assertEqual(a['soy']['inputs_scope'], {'budget': 'common', 'yield': 'common'})
        self.assertEqual(out['differences']['b']['maize2']['margin_per_ha']['mean'], -900.0)  # 400 of cost + 500 of water
        self.assertEqual(out['yield_message'], 'same_reference_yield_management_effect_on_harvest_not_estimated')

    def test_a_flag_alone_never_declares_an_effect_of_the_management(self):
        flagged = hv.YieldSource(origin='experimental_model', crop_id='maize2', kg_ha=6500.0, reference='synthetic chain', validation_status='experimental', responds_to_management=True)
        with self.assertRaises(ValueError):  # a common source cannot respond to management
            compared(sources={'soy': reference(), 'maize2': flagged})
        with self.assertRaises(ValueError):  # per alternative, but not bound to the configuration that was simulated
            compared(sources_by_alternative={'b': {'maize2': flagged}})
        result = cmp.run_comparison(request([alt('a'), alt('b', **IRRIGATED)]), members())
        bound = hv.YieldSource(origin='experimental_model', crop_id='maize2', kg_ha=6500.0, reference='synthetic chain', validation_status='experimental', responds_to_management=True,
                               estimated_for_config_sha256=result.runs['b'].config_sha256)
        out = hv.compare_harvests(result, {'soy': reference(), 'maize2': MAIZE}, {'soy': budget(), 'maize2': MAIZE_BUDGET}, area_ha=10.0, sources_by_alternative={'b': {'maize2': bound}})
        self.assertEqual(out['alternatives']['b']['harvests']['maize2']['yield']['management_effect'], 'estimated_by_source_for_this_configuration')
        self.assertEqual(out['alternatives']['a']['harvests']['maize2']['yield']['management_effect'], 'not_estimated')
        self.assertEqual(out['yield_message'], 'yield_estimated_for_some_managements_by_source')
        standalone = hv.outcome_members(flagged, MAIZE_BUDGET, area_ha=None, irrigation_gross_mm={2015: 0.0})
        self.assertEqual(standalone['yield']['management_effect'], 'not_estimated')

    def test_a_grower_hypothesis_per_management_is_labelled_as_hypothesis_and_a_reference_cannot_differ(self):
        hypo = hv.YieldSource(origin='user_hypothesis', crop_id='maize2', kg_ha=6600.0, reference='informed by the grower', validation_status='user_hypothesis')
        _, out = compared(sources_by_alternative={'b': {'maize2': hypo}})
        self.assertEqual(out['alternatives']['b']['harvests']['maize2']['yield']['management_effect'], 'grower_hypothesis_for_this_management')
        self.assertEqual(out['yield_message'], 'yield_differs_by_grower_hypothesis_not_estimated')
        other = hv.YieldSource(origin='historical_reference', crop_id='maize2', kg_ha=6400.0, reference='another series', validation_status='reference_not_a_forecast')
        with self.assertRaises(ValueError):
            compared(sources_by_alternative={'b': {'maize2': other}})
        with self.assertRaises(ValueError):
            compared(budgets_by_alternative={'zz': {'maize2': MAIZE_BUDGET}})


class Coverage(unittest.TestCase):
    def test_a_harvest_without_budget_or_yield_is_listed_with_reasons_and_the_rotation_total_is_unavailable(self):
        _, out = compared(sources={'soy': reference()}, budgets={'soy': budget()})
        a = out['alternatives']['a']
        self.assertEqual(list(a['harvests']), ['soy', 'maize2'])
        self.assertEqual(a['harvests']['maize2']['unavailable'], ['yield_source_missing', 'budget_missing'])
        self.assertIsNone(a['harvests']['maize2']['margin']['mean_per_ha'])
        self.assertEqual(a['coverage'], {'harvests': 2, 'with_margin': 1, 'missing': {'maize2': ['yield_source_missing', 'budget_missing']}})
        self.assertIsNone(a['rotation']['margin_per_ha_mean'])
        self.assertFalse(a['rotation']['complete'])
        self.assertEqual(a['rotation']['subtotal'], {'harvests': ['soy'], 'margin_per_ha_mean': 2000.0, 'kind': 'subtotal_of_the_listed_harvests_not_the_rotation'})
        self.assertEqual(out['differences']['b']['maize2'], {'margin_per_ha': None, 'reason': 'margin_unavailable'})

    def test_costs_are_kept_without_a_yield_and_the_two_coverages_are_separate(self):
        none = hv.resolve_yield('maize2', [])
        out = hv.outcome_members(none, MAIZE_BUDGET, area_ha=10.0, irrigation_gross_mm={2015: 0.0, 2016: 30.0, 2017: 60.0})
        self.assertEqual(out['cost_per_ha'], 3300.0)
        self.assertEqual(out['irrigation_cost_per_ha'], 300.0)
        self.assertEqual(out['break_even_kg_ha'], 3300.0)
        self.assertIsNone(out['revenue_per_ha']); self.assertIsNone(out['margin_per_ha'])
        self.assertEqual(out['coverage'], {'cost': {'members': 3, 'total': 3}, 'yield': {'members': 0, 'total': 3}, 'revenue': {'members': 0, 'total': 3}, 'margin': {'members': 0, 'total': 3}})
        self.assertEqual(out['money']['cost_per_ha'], '3300.00')
        partial = hv.YieldSource(origin='experimental_model', crop_id='maize2', kg_ha=None, per_member={2015: 6000.0, 2016: None, 2017: 5000.0}, reference='synthetic chain', validation_status='experimental')
        out = hv.outcome_members(partial, MAIZE_BUDGET, area_ha=None, irrigation_gross_mm={2015: 0.0, 2016: 30.0, 2017: 60.0})
        self.assertEqual(out['coverage']['cost'], {'members': 3, 'total': 3})
        self.assertEqual(out['coverage']['margin'], {'members': 2, 'total': 3})
        self.assertEqual(out['cost_per_ha'], 3300.0)                      # every member with a known cost
        self.assertEqual(out['margin_per_ha'], (3000.0 + 1400.0) / 2)     # only members with a yield


class Currencies(unittest.TestCase):
    def test_money_of_different_currencies_is_never_added_or_compared_without_a_stated_conversion(self):
        usd = budget(price_per_unit=10.0, price_unit='bushel', unit_kg=25.4, currency='USD', cost_per_ha=600.0, irrigation_cost_per_m3=0.0)
        _, out = compared(budgets={'soy': budget(), 'maize2': usd})
        a = out['alternatives']['a']
        self.assertIsNone(a['rotation']['margin_per_ha_mean'])
        self.assertEqual(a['rotation']['unavailable'], ['mixed_currencies'])
        self.assertEqual(a['rotation']['currencies'], ['BRL', 'USD'])
        _, out = compared(budgets_by_alternative={'b': {'maize2': usd}})
        self.assertEqual(out['differences']['b']['maize2'], {'margin_per_ha': None, 'reason': 'currency_mismatch'})
        for bad in (dict(rate=0.0), dict(rate=-1.0), dict(date=''), dict(source=''), dict(to_currency='USD')):
            with self.assertRaises(ValueError, msg=bad):
                hv.CurrencyConversion(**{**dict(from_currency='USD', to_currency='BRL', rate=5.0, date='2026-11-01', source='user'), **bad})

    def test_a_stated_rate_date_and_source_convert_and_are_reported(self):
        usd = budget(price_per_unit=1.0, price_unit='kg', unit_kg=None, currency='USD', cost_per_ha=600.0, irrigation_cost_per_m3=0.0)
        rate = hv.CurrencyConversion(from_currency='USD', to_currency='BRL', rate=5.0, date='2026-11-01', source='rate informed by the user')
        _, out = compared(budgets={'soy': budget(), 'maize2': usd}, report_currency='BRL', conversions=(rate,))
        a = out['alternatives']['a']
        self.assertEqual(a['harvests']['maize2']['money']['currency'], 'USD')     # each harvest stays in its own currency
        self.assertEqual(a['rotation']['currency'], 'BRL')
        self.assertEqual(a['rotation']['margin_per_ha_mean'], 2000.0 + (6000.0 - 600.0) * 5.0)
        self.assertEqual(a['rotation']['conversions'], [{'from': 'USD', 'to': 'BRL', 'rate': '5.0', 'date': '2026-11-01', 'source': 'rate informed by the user'}])
        _, out = compared(budgets={'soy': budget(), 'maize2': usd}, report_currency='EUR', conversions=(rate,))
        self.assertEqual(out['alternatives']['a']['rotation']['unavailable'], ['conversion_missing'])


class DecimalMoney(unittest.TestCase):
    def test_money_is_computed_in_decimal_not_only_formatted(self):
        cheap = budget(price_unit='kg', price_per_unit=0.1, unit_kg=None, cost_per_ha=0.0)
        out = hv.outcome_single(reference(kg_ha=3.0), cheap, area_ha=3.0, irrigation_gross_mm=0.0)
        self.assertEqual(Decimal(str(out['revenue_per_ha'])), Decimal('0.3'))     # 3 * 0.1 in binary floating point is 0.30000000000000004
        self.assertEqual(Decimal(str(out['total_revenue'])), Decimal('0.9'))
        self.assertEqual(out['exact']['revenue_per_ha'], Decimal('0.3'))
        src = hv.YieldSource(origin='experimental_model', crop_id='soy', kg_ha=None, per_member={1: 1.0, 2: 2.0, 3: 3.0}, reference='synthetic chain', validation_status='experimental')
        out = hv.outcome_members(src, cheap, area_ha=None, irrigation_gross_mm={1: 0.0, 2: 0.0, 3: 0.0})
        self.assertEqual(out['exact']['margin_per_ha'], Decimal('0.2'))            # mean of 0.1, 0.2, 0.3
        self.assertEqual(Decimal(str(out['margin']['mean_per_ha'])), Decimal('0.2'))
        odd = hv.outcome_single(reference(kg_ha=1.0), budget(price_unit='kg', price_per_unit=2.675, unit_kg=None, cost_per_ha=0.0), area_ha=None, irrigation_gross_mm=0.0)
        self.assertEqual(odd['money']['revenue_per_ha'], '2.68')                   # 2.675 exactly, half-even on the decimal value


if __name__ == '__main__':
    unittest.main()
