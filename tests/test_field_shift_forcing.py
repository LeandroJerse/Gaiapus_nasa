"""Forcing transformations of the Field Shift sensitivity: rain scaled on the same historical day sequence."""
import copy
import math
import unittest
from datetime import date, timedelta

from data.models import field_shift_forcing as forcing


def weather(days=40):
    start = date(2019, 12, 20)
    return {(start + timedelta(days=i)).isoformat(): {'day_utc': (start + timedelta(days=i)).isoformat(), 'rain_mm': float(i % 7) * 1.5,
                                                     'et0_mm': 4.0 + (i % 3) * .1, 't_min_c': 20.0, 't_max_c': 31.0} for i in range(days)}


class TransformRain(unittest.TestCase):
    def test_factor_one_reproduces_the_input_and_never_mutates_it(self):
        source = weather(); before = copy.deepcopy(source)
        same = forcing.transform_rain(source, 1.0)
        self.assertEqual(same, before)
        self.assertIsNot(same, source)
        wetter = forcing.transform_rain(source, 1.2)
        wetter[next(iter(wetter))]['rain_mm'] = -99
        self.assertEqual(source, before)

    def test_totals_scale_with_the_factor_and_zero_rain_stays_zero(self):
        source = weather(); total = sum(r['rain_mm'] for r in source.values())
        for factor in forcing.RAIN_MULTIPLIERS:
            out = forcing.transform_rain(source, factor)
            self.assertEqual(list(out), list(source))
            self.assertAlmostEqual(sum(r['rain_mm'] for r in out.values()), total * factor, places=9)
            for day, row in out.items():
                if source[day]['rain_mm'] == 0:
                    self.assertEqual(row['rain_mm'], 0)
                self.assertEqual({k: v for k, v in row.items() if k != 'rain_mm'}, {k: v for k, v in source[day].items() if k != 'rain_mm'})

    def test_published_points_are_minus_20_base_plus_20(self):
        self.assertEqual(forcing.RAIN_MULTIPLIERS, (0.8, 1.0, 1.2))

    def test_invalid_factors_and_forcings_are_refused(self):
        for bad in (-0.1, math.nan, math.inf, True, '1.2', None, 2.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                forcing.transform_rain(weather(), bad)
        broken = weather(); broken[next(iter(broken))]['rain_mm'] = None
        with self.assertRaises(ValueError):
            forcing.transform_rain(broken, 1.2)
        negative = weather(); negative[next(iter(negative))]['rain_mm'] = -1.0
        with self.assertRaises(ValueError):
            forcing.transform_rain(negative, 1.2)


if __name__ == '__main__':
    unittest.main()
