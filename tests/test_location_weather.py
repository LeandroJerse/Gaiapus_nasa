"""Daily weather and per-season FAO-56 water balance of the location core."""
import csv
import unittest
from datetime import date, timedelta
from pathlib import Path

from data.models.water_balance import CROPS, et0_daily
from data.serving import location_weather as lw

V1 = Path('data/processed/water_balance_v1')
CONTRACT_ROW = {'date', 'storage_mm', 'available_fraction', 'etc_mm', 'eta_mm', 'deficit_mm', 'drainage_mm', 'ks', 'stage'}


def power_payload(start, days, *, rain=2.0, fill_on=None):
    keys = [(start + timedelta(days=i)).strftime('%Y%m%d') for i in range(days)]
    base = {'IMERG_PRECTOT': rain, 'T2M_MIN': 20.0, 'T2M_MAX': 31.0, 'ALLSKY_SFC_SW_DWN': 22.0, 'RH2M': 70.0, 'WS2M': 2.0, 'T2MDEW': 17.0, 'PS': 96.0}
    parameters = {p: {k: (-999.0 if fill_on == (p, k) else v) for k in keys} for p, v in base.items()}
    return {'geometry': {'coordinates': [-55.9, -12.55, 380.0]}, 'properties': {'parameter': parameters}}


def synthetic_weather(start, days):
    return [{'date': (start + timedelta(days=i)).isoformat(), 'rain_mm': 2.0, 'et0_mm': 4.0, 'tmin_c': 15.0, 'tmax_c': 28.0} for i in range(days)]


class Weather(unittest.TestCase):
    def test_power_rows_carry_rain_temperatures_and_fao56_et0(self):
        rows = lw.parse_power(power_payload(date(2024, 1, 1), 2))
        self.assertEqual([r['date'] for r in rows], ['2024-01-01', '2024-01-02'])
        expected = et0_daily(date(2024, 1, 1), -12.55, 380.0, 20.0, 31.0, 22.0, 2.0, 96.0, dewpoint=17.0)['et0_mm']
        self.assertAlmostEqual(rows[0]['et0_mm'], expected, places=9)
        self.assertEqual((rows[0]['rain_mm'], rows[0]['tmin_c'], rows[0]['tmax_c']), (2.0, 20.0, 31.0))

    def test_a_fill_value_makes_the_layer_unavailable_never_zero(self):
        payload = power_payload(date(2024, 1, 1), 3, fill_on=('IMERG_PRECTOT', '20240102'))
        layer = lw.weather_layer(payload)
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'source_unavailable'))

    def test_available_layer_states_source_and_period(self):
        layer = lw.weather_layer(power_payload(date(2024, 1, 1), 3))
        self.assertEqual(layer['status'], 'available')
        self.assertIn('NASA POWER', layer['source'])
        self.assertEqual(layer['period'], {'start': '2024-01-01', 'end': '2024-01-03'})


class WaterBalance(unittest.TestCase):
    crops_us = [{'crop': 'wheat_winter', 'parameter_set': 'wheat_winter_us', 'sowing_month_day': [10, 2]}]

    def test_winter_wheat_is_one_cycle_across_the_year(self):
        layer = lw.water_layer(self.crops_us, synthetic_weather(date(2023, 9, 1), 330), 55.0)
        self.assertEqual(layer['status'], 'available')
        [season] = layer['seasons']
        self.assertEqual((season['season'], season['sowing_date'], len(season['rows'])), ('2023/24', '2023-10-02', 260))
        self.assertEqual(set(season['rows'][0]), CONTRACT_ROW)
        self.assertEqual(season['rows'][-1]['stage'], 'maturation')

    def test_scaled_parameter_sets_are_declared_in_the_method(self):
        layer = lw.water_layer(self.crops_us, synthetic_weather(date(2023, 9, 1), 330), 55.0)
        self.assertIn('wheat_winter_us', layer['method'])
        self.assertIn('scaled', layer['method'])

    def test_incomplete_seasons_are_dropped_not_truncated(self):
        layer = lw.water_layer(self.crops_us, synthetic_weather(date(2023, 9, 1), 200), 55.0)
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'source_unavailable'))

    def test_crop_without_calendar_is_reported(self):
        layer = lw.water_layer([{'crop': 'soy', 'parameter_set': 'soy', 'sowing_month_day': None}], synthetic_weather(date(2023, 9, 1), 400), 55.0)
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'crop_calendar_unknown'))

    @unittest.skipUnless((V1 / 'daily_2024.csv').exists(), 'requires local data/processed/water_balance_v1')
    def test_reproduces_water_balance_v1_for_sorriso_soy_2024(self):
        weather = []
        with (V1 / 'et0_daily.csv').open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row['municipality_code'] == '5107925' and '2023-09-01' <= row['day_utc'] <= '2024-03-31':
                    weather.append({'date': row['day_utc'], 'rain_mm': float(row['rain_mm']), 'et0_mm': float(row['et0_mm']), 'tmin_c': 0.0, 'tmax_c': 0.0})
        expected = {}
        with (V1 / 'daily_2024.csv').open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row['scenario_id'] == '5107925_soy_2024_AD4_i0.7_historical_r1':
                    expected[row['day_utc']] = float(row['storage_end_mm'])
        layer = lw.water_layer([{'crop': 'soy', 'parameter_set': 'soy', 'sowing_month_day': [10, 5]}], weather, CROPS['soy']['cad_mm']['AD4'], initial_fraction=.7)
        rows = {r['date']: r['storage_mm'] for r in layer['seasons'][0]['rows']}
        self.assertEqual(len(expected), 115)
        self.assertEqual(set(rows), set(expected))
        self.assertLess(max(abs(rows[d] - expected[d]) for d in expected), 0.1)


if __name__ == '__main__':
    unittest.main()
