"""Calendar and plan validation of the crop simulation engine (v1).

H01, H03 and H06 reproduce findings of the 2026-11-15 audit of the Lara simulator
(docs/superpowers/plans/2026-11-15-integracao-lara/AUDITORIA.md §4) against the new Gaiapus engine.
"""
import math
import unittest
from datetime import date, timedelta


def cal():
    from data.models.crop_simulation import calendar
    return calendar


def item(crop, sowing, **extra):
    from data.models.crop_simulation.types import CropPlanItem
    return CropPlanItem(crop_id=crop, parameter_set_id='gaiapus-fao56-br-v1', sowing_date=sowing, **extra)


class DateTests(unittest.TestCase):
    def assertCode(self, code, fn, *args, **kw):
        from data.models.crop_simulation.types import SimulationInputError
        with self.assertRaises(SimulationInputError) as ctx:
            fn(*args, **kw)
        self.assertEqual(ctx.exception.code, code)

    def test_h01_impossible_date_is_rejected_not_clamped(self):
        c = cal()
        self.assertCode('INVALID_DATE', c.parse_iso_date, '2020-02-31')
        self.assertCode('INVALID_DATE', c.resolve_month_day, '02-31', 2020)

    def test_h06_malformed_month_day_rejected(self):
        c = cal()
        for bad in ('99-99', '13-01', '1-5', '', '02-30'):
            self.assertCode('INVALID_DATE', c.resolve_month_day, bad, 2021)

    def test_leap_day_policy_is_explicit(self):
        c = cal()
        self.assertEqual(c.resolve_month_day('02-29', 2024), date(2024, 2, 29))
        # Default policy rejects; it never shifts silently to 02-28 or 03-01.
        self.assertCode('INVALID_DATE', c.resolve_month_day, '02-29', 2023)
        self.assertIsNone(c.resolve_month_day('02-29', 2023, leap_policy='exclude'))

    def test_iso_dates_strict(self):
        c = cal()
        self.assertEqual(c.parse_iso_date('2024-02-29'), date(2024, 2, 29))
        for bad in ('2024-2-29', '29/02/2024', '2024-02-29T00:00', None, 20240229):
            self.assertCode('INVALID_DATE', c.parse_iso_date, bad)


class PlanTests(unittest.TestCase):
    def assertCode(self, code, fn, *args, **kw):
        from data.models.crop_simulation.types import SimulationInputError
        with self.assertRaises(SimulationInputError) as ctx:
            fn(*args, **kw)
        self.assertEqual(ctx.exception.code, code)

    def test_harvest_comes_from_the_parameter_set(self):
        plan = cal().validate_crop_plan([item('soy', date(2020, 10, 5)), item('maize2', date(2021, 2, 1))])
        soy, maize = plan.items
        self.assertEqual(soy.cycle_days, 115)
        self.assertEqual(soy.harvest_date, date(2021, 1, 27))
        self.assertEqual(maize.cycle_days, 120)
        self.assertEqual(plan.start, date(2020, 10, 5))
        self.assertEqual(plan.end, maize.harvest_date)

    def test_h03_overlapping_sequence_rejected(self):
        # Lara: harvest A 2021-02-11 and sowing B 2021-02-01 were accepted.
        self.assertCode('OVERLAPPING_CROPS', cal().validate_crop_plan, [item('soy', date(2020, 10, 19)), item('maize2', date(2021, 2, 1))])
        # Sowing the day after harvest is a valid sequence.
        a = cal().validate_crop_plan([item('soy', date(2020, 10, 5))]).items[0]
        cal().validate_crop_plan([item('soy', date(2020, 10, 5)), item('maize2', a.harvest_date + timedelta(days=1))])

    def test_h06_span_and_yield_limits(self):
        c = cal()
        self.assertCode('RESOURCE_LIMIT', c.validate_crop_plan, [item('soy', date(2000, 10, 5)), item('soy', date(2079, 10, 5))])
        self.assertCode('RESOURCE_LIMIT', c.validate_crop_plan, [item('soy', date(2020 + i, 10, 5)) for i in range(7)])
        for bad in (-10.0, math.nan, math.inf):
            self.assertCode('INVALID_PARAMETER', c.validate_crop_plan, [item('soy', date(2020, 10, 5), reference_yield_kg_ha=bad)])
        self.assertCode('INVALID_PARAMETER', c.validate_crop_plan, [])

    def test_crop_identity(self):
        c = cal()
        self.assertCode('CROP_UNSUPPORTED', c.validate_crop_plan, [item('coffee', date(2020, 10, 5))])
        self.assertCode('CROP_UNSUPPORTED', c.validate_crop_plan, [item('coffee_total_mg', date(2020, 10, 5))])
        self.assertCode('CROP_UNSUPPORTED', c.validate_crop_plan, [item('wheat', date(2020, 10, 5))])
        # Lara's key maps to the Gaiapus identifier, it does not create a second crop.
        self.assertEqual(c.validate_crop_plan([item('maize_safrinha', date(2021, 2, 1))]).items[0].crop_id, 'maize2')

    def test_unknown_parameter_set_rejected(self):
        from data.models.crop_simulation.types import CropPlanItem
        self.assertCode('INVALID_PARAMETER', cal().validate_crop_plan, [CropPlanItem(crop_id='soy', parameter_set_id='nope', sowing_date=date(2020, 10, 5))])


if __name__ == '__main__':
    unittest.main()
