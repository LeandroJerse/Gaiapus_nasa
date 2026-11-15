"""10-day water forecast of the location core: spin-up with observed weather, then Open-Meteo ECMWF IFS 0.25."""
import unittest
from datetime import date, datetime, timedelta, timezone

from data.serving import location_forecast as lf

SOY_MT = [{'crop': 'soy', 'parameter_set': 'soy', 'sowing_month_day': [10, 5]}]
MAIZE_US = [{'crop': 'maize1', 'parameter_set': 'maize1_us', 'sowing_month_day': [5, 6]}]
ISSUED = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def observed(start, end):
    days = (end - start).days + 1
    return [{'date': (start + timedelta(days=i)).isoformat(), 'rain_mm': 3.0, 'et0_mm': 5.0, 'tmin_c': 20.0, 'tmax_c': 32.0} for i in range(days)]


def forecast(start, days=10):
    return {(start + timedelta(days=i)).isoformat(): {'rain_mm': 0.0, 'et0_mm': 6.0} for i in range(days)}


class Forecast(unittest.TestCase):
    def test_no_crop_in_the_field_is_out_of_season(self):
        layer = lf.forecast_layer(MAIZE_US, ISSUED, observed(date(2026, 5, 1), date(2026, 11, 14)), forecast(date(2026, 11, 15)), 55.0)
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'out_of_season'))

    def test_soy_in_season_runs_ten_days_from_the_spun_up_storage(self):
        layer = lf.forecast_layer(SOY_MT, ISSUED, observed(date(2026, 10, 5), date(2026, 11, 14)), forecast(date(2026, 11, 15)), 55.0)
        self.assertEqual(layer['status'], 'available')
        self.assertEqual(layer['issued_at'], '2026-11-15T12:00:00+00:00')
        self.assertEqual((layer['crop'], layer['season']), ('soy', '2026/27'))
        self.assertEqual(len(layer['rows']), 10)
        self.assertEqual(layer['rows'][0]['date'], '2026-11-15')
        self.assertEqual(set(layer['rows'][0]), {'date', 'rain_mm', 'et0_mm', 'storage_mm', 'stress'})
        # Dry forecast after a wet spin-up: storage only goes down.
        storages = [r['storage_mm'] for r in layer['rows']]
        self.assertEqual(storages, sorted(storages, reverse=True))

    def test_forecast_service_failure_is_source_unavailable(self):
        layer = lf.forecast_layer(SOY_MT, ISSUED, observed(date(2026, 10, 5), date(2026, 11, 14)), None, 55.0)
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'source_unavailable'))

    def test_missing_spin_up_days_are_not_invented(self):
        layer = lf.forecast_layer(SOY_MT, ISSUED, observed(date(2026, 10, 5), date(2026, 11, 10)), forecast(date(2026, 11, 15)), 55.0)
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'source_unavailable'))

    def test_forecast_starts_after_the_last_observed_day_when_power_lags(self):
        layer = lf.forecast_layer(SOY_MT, ISSUED, observed(date(2026, 10, 5), date(2026, 11, 11)), forecast(date(2026, 11, 12)), 55.0, start_on=date(2026, 11, 12))
        self.assertEqual((layer['rows'][0]['date'], layer['issued_at']), ('2026-11-12', '2026-11-15T12:00:00+00:00'))

    def test_forecast_days_with_missing_hours_are_dropped(self):
        f = forecast(date(2026, 11, 15)); f['2026-11-20'] = None
        layer = lf.forecast_layer(SOY_MT, ISSUED, observed(date(2026, 10, 5), date(2026, 11, 14)), f, 55.0)
        self.assertEqual(len(layer['rows']), 5)


if __name__ == '__main__':
    unittest.main()
