import unittest
from datetime import date

from data.ingest.weather import compare_rain, normalize_power
from data.ingest.imerg_ncar import parse_subset


class WeatherPipelineTests(unittest.TestCase):
    def test_months_include_lacunas_and_no_pairs(self):
        rows = [{"day_utc": "2023-12-31", "rain_mm": 2},
                {"day_utc": "2024-01-01", "rain_mm": 0},
                {"day_utc": "2024-01-02", "rain_mm": None},
                {"day_utc": "2024-01-03", "rain_mm": 100}]
        months = compare_rain(rows, {"2024-01-01": 0.02, "2024-01-02": 1})["monthly"]
        self.assertEqual(months["2023-12"]["expected_days"], 1)
        self.assertIsNone(months["2023-12"]["power_mm"])
        january = months["2024-01"]
        self.assertEqual(january["status"], "incomplete")
        self.assertEqual(january["paired_days"], 1)
        self.assertEqual(january["missing_power_dates"], ["2024-01-02"])
        self.assertEqual(january["missing_reference_dates"], ["2024-01-03"])
        self.assertEqual(january["mismatched_days"], 0)
        self.assertEqual(january["power_mm"], 0)

    def payload(self):
        return {
            "header": {"time_standard": "UTC", "fill_value": -999},
            "parameters": {
                "T2M_MIN": {"units": "C"}, "T2M_MAX": {"units": "C"},
                "ALLSKY_SFC_SW_DWN": {"units": "kWh/m^2/day"},
                "IMERG_PRECTOT": {"units": "mm/day"},
            },
            "properties": {"parameter": {
                "T2M_MIN": {"20240229": 20}, "T2M_MAX": {"20240229": 30},
                "ALLSKY_SFC_SW_DWN": {"20240229": 2},
                "IMERG_PRECTOT": {"20240229": 0, "20240301": -999},
            }},
        }

    def test_leap_day_zero_fill_and_unit_conversion(self):
        rows = normalize_power(self.payload(), date(2024, 2, 29), date(2024, 3, 1))
        self.assertEqual([r["day_utc"] for r in rows], ["2024-02-29", "2024-03-01"])
        self.assertEqual(rows[0]["rain_mm"], 0)
        self.assertEqual(rows[0]["radiation_mj_m2"], 7.2)
        self.assertIsNone(rows[1]["rain_mm"])
        self.assertIsNone(rows[1]["t_min_c"])

    def test_comparison_reports_paired_days_and_does_not_promote_empty(self):
        rows = [{"day_utc": "2024-01-01", "rain_mm": 0}, {"day_utc": "2024-01-02", "rain_mm": 1}]
        report = compare_rain(rows, {"2024-01-01": 0.03})
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["matched_days"], 1)
        self.assertEqual(report["mismatched_days"], 1)
        self.assertAlmostEqual(report["bias_mm"], -0.03)
        self.assertEqual(compare_rain([], {})["status"], "incomplete")

    def test_complete_match_and_discrepancy(self):
        rows = [{"day_utc": "2024-01-01", "rain_mm": 3.6}]
        self.assertEqual(compare_rain(rows, {"2024-01-01": 3.61})["status"], "complete_match")
        self.assertEqual(compare_rain(rows, {"2024-01-01": 4})["status"], "discrepancy")

    def test_rejects_invalid_precipitation(self):
        payload = self.payload()
        payload["properties"]["parameter"]["IMERG_PRECTOT"]["20240229"] = -1
        with self.assertRaises(ValueError):
            normalize_power(payload, date(2024, 2, 29), date(2024, 3, 1))

    def test_imerg_spatial_and_temporal_identity(self):
        subset = (
            b"precipitation.precipitation[1][1][1]\n[0][0], 7.244999\n\n"
            b"precipitation.time[1]\n16067.0\n\n"
            b"precipitation.lon[1]\n-48.25\n\n"
            b"precipitation.lat[1]\n-18.95\n"
        )
        day = date(2024, 1, 2)
        self.assertAlmostEqual(parse_subset(subset, day, -18.95, -48.25), 7.244999)
        self.assertIsNone(parse_subset(subset.replace(b"7.244999", b"-9999.9"), day, -18.95, -48.25))
        with self.assertRaises(ValueError):
            parse_subset(subset, day, -18.85, -48.25)
        with self.assertRaises(ValueError):
            parse_subset(subset, date(2024, 1, 3), -18.95, -48.25)
