"""Crop parameter sets added for the US places of the location core (FAO-56, hypothesis, no local calibration)."""
import unittest
from datetime import date, timedelta

from data.models import water_balance as wb

FROZEN = {
    'soy': {'phase_days': (15, 35, 45, 20), 'kc_initial': .4, 'kc_mid': 1.15, 'kc_end': .5, 'p_base': .5,
            'cad_mm': {'AD2': 32., 'AD4': 55., 'AD6': 95.}},
    'maize2': {'phase_days': (20, 40, 40, 20), 'kc_initial': .3, 'kc_mid': 1.2, 'kc_end': .35, 'p_base': .55,
               'cad_mm': {'AD2': 31.8, 'AD4': 54.6, 'AD6': 95.4}},
}


def weather(days):
    start = date(2024, 5, 1)
    return [{'day_utc': (start + timedelta(days=i)).isoformat(), 'rain_mm': 2.0, 'et0_mm': 4.0} for i in range(days)]


class CropParameterSets(unittest.TestCase):
    def test_brazil_sets_are_unchanged(self):
        for crop, config in FROZEN.items():
            self.assertEqual(wb.CROPS[crop], config, crop)

    def test_new_sets_follow_fao56_tables(self):
        # Table 11 rows: maize grain "Idaho, USA" 30/40/50/50; soybeans "Central USA" 20/35/60/25;
        # winter wheat "Idaho, USA" 160/75/75/25 scaled to a 260-day Kansas cycle. Table 12 Kc values.
        self.assertEqual(wb.CROPS['maize1_us']['phase_days'], (30, 40, 50, 50))
        self.assertEqual((wb.CROPS['maize1_us']['kc_mid'], wb.CROPS['maize1_us']['kc_end']), (1.2, .35))
        self.assertEqual(wb.CROPS['soy_us']['phase_days'], (20, 35, 60, 25))
        self.assertEqual(wb.CROPS['soy_us']['kc_mid'], 1.15)
        self.assertEqual(sum(wb.CROPS['wheat_winter_us']['phase_days']), 260)
        self.assertEqual((wb.CROPS['wheat_winter_us']['kc_initial'], wb.CROPS['wheat_winter_us']['kc_mid'], wb.CROPS['wheat_winter_us']['kc_end']), (.4, 1.15, .25))

    def test_simulate_runs_exactly_one_cycle_of_each_new_crop(self):
        for crop in ('maize1_us', 'soy_us', 'wheat_winter_us'):
            length = sum(wb.CROPS[crop]['phase_days'])
            rows = wb.simulate(weather(length), crop, wb.CROPS[crop]['cad_mm']['AD4'], .5)
            self.assertEqual(len(rows), length, crop)
            self.assertEqual(rows[-1]['phase'], 'maturation')
            with self.assertRaises(ValueError):
                wb.simulate(weather(length - 1), crop, 55., .5)


if __name__ == '__main__':
    unittest.main()
