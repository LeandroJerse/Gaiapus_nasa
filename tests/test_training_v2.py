import importlib.util
import unittest


class TrainingV2Tests(unittest.TestCase):
    def test_midnight_belongs_to_previous_accumulation_day(self):
        self.assertIsNotNone(importlib.util.find_spec('data.ingest.rain_validation_v2'))
        from data.ingest.rain_validation_v2 import rain_day
        self.assertEqual(rain_day('2024/01/02', '0000 UTC'), '2024-01-01')
        self.assertEqual(rain_day('2024/01/02', '0100 UTC'), '2024-01-02')

    def test_weights_are_not_silently_renormalized_over_missing_weather(self):
        self.assertIsNotNone(importlib.util.find_spec('data.ingest.crop_weighted_v2'))
        from data.ingest.crop_weighted_v2 import weighted_value
        self.assertAlmostEqual(weighted_value([10, 30], [1, 3]), 25)
        self.assertIsNone(weighted_value([10, None], [1, 3]))
        with self.assertRaises(ValueError):
            weighted_value([10, 30], [-1, 3])

    def test_day_requires_24_valid_preceding_hours(self):
        from data.ingest.rain_validation_v2 import aggregate_hours,rain_day
        rows=[]
        for hour in range(1,25):
            day='2024-01-01' if hour<24 else '2024-01-02'
            label=f'{hour%24:02d}00'
            rows.append(dict(station_id='A000',message_date=day,message_hour=label,
                             day_utc=rain_day(day,label),rain_mm=1.0))
        self.assertEqual(aggregate_hours(rows)[0]['rain_mm'],24)
        rows[-1]['rain_mm']=None
        self.assertIsNone(aggregate_hours(rows)[0]['rain_mm'])
        with self.assertRaises(ValueError):aggregate_hours(rows+[rows[0]])

    def test_metrics_use_only_paired_days(self):
        from data.ingest.rain_validation_v2 import metrics
        result=metrics([{'station_mm':10,'imerg_mm':12},{'station_mm':None,'imerg_mm':100}])
        self.assertEqual(result['pairs'],1)
        self.assertEqual(result['bias_mm'],2)
        self.assertEqual(result['imerg_sum_mm'],12)

    def test_missing_api_day_remains_in_expected_calendar(self):
        from data.ingest import rain_validation_v2 as rain
        self.assertTrue(hasattr(rain,'pair_days'))
        rows=rain.pair_days([],{'20240228':1},-999,'2024-02-28','2024-02-29','A000')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['day_utc'],'2024-02-29')
        self.assertIsNone(rows[1]['imerg_mm'])

    def test_invalid_physical_weather_is_rejected(self):
        from data.ingest import crop_weighted_v2 as crop
        self.assertTrue(hasattr(crop,'validate_daily_fields'))
        with self.assertRaises(ValueError):crop.validate_daily_fields({'rain_mm':[-1]})
        with self.assertRaises(ValueError):crop.validate_daily_fields({'rh2m_pct':[101]})
        with self.assertRaises(ValueError):crop.validate_daily_fields({'t_min_c':[30],'t_max_c':[20]})

    def test_real_matrix_aggregation_propagates_missing_cell(self):
        import math
        from data.ingest.crop_weighted_v2 import weighted_matrix
        result=weighted_matrix([[10,10],[30,float('nan')]],[.25,.75])
        self.assertEqual(result[0],25)
        self.assertTrue(math.isnan(result[1]))


if __name__ == '__main__':
    unittest.main()
