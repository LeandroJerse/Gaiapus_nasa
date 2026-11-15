import unittest
from datetime import date

BATCH = [{'municipality_code': '5100001', 'latitude': -12.5, 'longitude': -55.5},
         {'municipality_code': '5100002', 'latitude': -13.5, 'longitude': -56.5}]


def series(values, lat, lon):
    return {'lat': lat, 'lon': lon, 'dates': [{'date': f'2020-01-{d:02d}T00:00:00Z', 'value': v} for d, v in values]}


def payload(parameter, first, second):
    return {'parameter': parameter, 'coordinates': [series(first, -12.5, -55.5), series(second, -13.5, -56.5)]}


class MeteomaticsSoilTests(unittest.TestCase):
    def test_accumulated_values_are_assigned_to_the_day_they_cover(self):
        from data.ingest.meteomatics_soil import parse
        data = {'data': [payload('precip_24h:mm', [(2, 5.0), (3, -999)], [(2, 1.0), (3, 2.0)]),
                         payload('soil_moisture_index_-15cm:idx', [(2, 0.4), (3, 0.5)], [(2, 0.6), (3, 0.7)])]}
        parsed = parse(data, BATCH)
        self.assertEqual(parsed['5100001']['rain_mm'], {'2020-01-01': 5.0, '2020-01-02': None})
        self.assertEqual(parsed['5100001']['smi_15cm'], {'2020-01-02': 0.4, '2020-01-03': 0.5})
        self.assertEqual(parsed['5100002']['rain_mm']['2020-01-02'], 2.0)

    def test_response_order_or_size_mismatch_is_rejected(self):
        from data.ingest.meteomatics_soil import parse
        swapped = {'data': [{'parameter': 'precip_24h:mm', 'coordinates': [series([(2, 1.0)], -13.5, -56.5), series([(2, 1.0)], -12.5, -55.5)]}]}
        with self.assertRaises(ValueError):
            parse(swapped, BATCH)
        with self.assertRaises(ValueError):
            parse({'data': [{'parameter': 'precip_24h:mm', 'coordinates': [series([(2, 1.0)], -12.5, -55.5)]}]}, BATCH)

    def test_window_features_ignore_everything_after_the_cutoff(self):
        from data.ingest.meteomatics_soil import window_features
        days = [f'2020-01-{d:02d}' for d in range(1, 31)]
        data = {field: {day: float(i) for i, day in enumerate(days)} for field in ('smi_15cm', 'smi_50cm', 'smi_150cm', 'rain_mm', 'et_mm')}
        first = window_features(data, date(2020, 1, 1), 15)
        for field in data:
            for day in days[15:]:
                data[field][day] = 9999.0
        self.assertEqual(first, window_features(data, date(2020, 1, 1), 15))
        self.assertEqual((first['mm_smi_15cm_at_cutoff'], first['mm_smi_15cm_min'], first['mm_rain_mm_sum']), (14.0, 0.0, 105.0))

    def test_incomplete_window_yields_no_features(self):
        from data.ingest.meteomatics_soil import window_features
        data = {field: {'2020-01-01': 1.0, '2020-01-02': None} for field in ('smi_15cm', 'smi_50cm', 'smi_150cm', 'rain_mm', 'et_mm')}
        self.assertIsNone(window_features(data, date(2020, 1, 1), 2))
        self.assertIsNone(window_features(data, date(2020, 1, 1), 5))

    def test_requests_carry_no_credentials(self):
        from data.ingest.meteomatics_soil import request_url
        url = request_url(2020, BATCH)
        self.assertTrue(url.startswith('https://api.meteomatics.com/2020-01-01T00:00:00Z--2021-01-01T00:00:00Z:P1D/'))
        self.assertIn('-12.5,-55.5+-13.5,-56.5/json', url)
        self.assertNotIn('@', url)


if __name__ == '__main__':
    unittest.main()
