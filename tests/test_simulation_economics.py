"""Economics with explicit units; unavailable is never zero."""
import math
import unittest


def e():
    from data.models.crop_simulation import economics
    return economics


def spec(**kw):
    args = dict(currency='BRL', price_per_unit=120.0, price_unit='bag', bag_kg=60.0, price_reference='user', price_date='2026-11-01',
                fixed_cost_per_ha=2500.0, variable_cost_per_ha=1500.0, irrigation_cost_per_m3=0.5)
    args.update(kw)
    return e().EconomicsSpec(**args)


class UnitTests(unittest.TestCase):
    def test_mm_to_m3_per_ha(self):
        self.assertEqual(e().mm_to_m3_per_ha(1.0), 10.0)
        self.assertEqual(e().mm_to_m3_per_ha(35.0), 350.0)

    def test_bag_price_needs_bag_weight(self):
        with self.assertRaises(ValueError):
            spec(bag_kg=None)
        self.assertEqual(spec().price_per_kg, 2.0)
        self.assertEqual(spec(price_unit='kg', price_per_unit=2.5, bag_kg=None).price_per_kg, 2.5)

    def test_rejects_non_finite_or_negative(self):
        for bad in (dict(price_per_unit=-1.0), dict(fixed_cost_per_ha=math.nan), dict(currency=''), dict(price_unit='ton')):
            with self.assertRaises(ValueError):
                spec(**bad)


class ResultTests(unittest.TestCase):
    def test_margin_with_irrigation_cost_per_member(self):
        r = e().compute_economics(3600.0, 40.0, spec())
        self.assertEqual(r.irrigation_m3_per_ha, 400.0)
        self.assertEqual(r.irrigation_cost_per_ha, 200.0)
        self.assertEqual(r.total_cost_per_ha, 4200.0)
        self.assertEqual(r.revenue_per_ha, 7200.0)
        self.assertEqual(r.margin_per_ha, 3000.0)
        self.assertEqual(r.break_even_kg_ha, 2100.0)
        self.assertEqual(r.currency, 'BRL')

    def test_unavailable_yield_leaves_revenue_and_margin_unavailable(self):
        r = e().compute_economics(None, 40.0, spec())
        self.assertIsNone(r.revenue_per_ha)
        self.assertIsNone(r.margin_per_ha)
        self.assertEqual(r.total_cost_per_ha, 4200.0)
        self.assertEqual(r.break_even_kg_ha, 2100.0)

    def test_ensemble_economics_break_even_varies_by_member(self):
        rows = e().ensemble_economics({2001: (3000.0, 0.0), 2002: (2000.0, 100.0), 2003: (None, 50.0)}, spec())
        self.assertNotEqual(rows['members'][2001].break_even_kg_ha, rows['members'][2002].break_even_kg_ha)
        self.assertEqual(rows['negative_margin_frequency'], {'members_with_yield': 2, 'negative': 1})
        self.assertIsNone(rows['members'][2003].margin_per_ha)


if __name__ == '__main__':
    unittest.main()
