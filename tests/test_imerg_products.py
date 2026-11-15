import unittest
from datetime import date, timedelta


def triple(early, late, final):
    return {'Early': early, 'Late': late, 'Final': final}


class ImergProductTrainingTests(unittest.TestCase):
    def test_paired_rain_keeps_only_days_with_three_complete_products(self):
        from data.ingest.train_imerg_products import paired_rain
        rows = [
            {'municipality_code': '3170206', 'day_utc': '2020-01-01', 'run': 'Early', 'matched_complete': 'True', 'rain_mm': '1.0', 'final_mm': '2.0'},
            {'municipality_code': '3170206', 'day_utc': '2020-01-01', 'run': 'Late', 'matched_complete': 'True', 'rain_mm': '1.5', 'final_mm': '2.0'},
            {'municipality_code': '3170206', 'day_utc': '2020-01-02', 'run': 'Early', 'matched_complete': 'True', 'rain_mm': '0.0', 'final_mm': '0.0'},
            {'municipality_code': '3170206', 'day_utc': '2020-01-02', 'run': 'Late', 'matched_complete': 'False', 'rain_mm': '', 'final_mm': ''},
        ]
        self.assertEqual(paired_rain(rows), {('3170206', '2020-01-01'): triple(1.0, 1.5, 2.0)})

    def test_window_with_a_gap_is_excluded_for_every_product(self):
        from data.ingest.train_imerg_products import window_rain
        start = date(2020, 1, 5)
        paired = {('3170206', (start + timedelta(days=i)).isoformat()): triple(1.0, 2.0, 3.0) for i in range(60)}
        rain, gaps = window_rain(paired, '3170206', start, 60)
        self.assertEqual((len(rain['Early']), rain['Late'][0], rain['Final'][-1], gaps), (60, 2.0, 3.0, []))
        del paired[('3170206', '2020-02-01')]
        rain, gaps = window_rain(paired, '3170206', start, 60)
        self.assertIsNone(rain)
        self.assertEqual(gaps, ['2020-02-01'])

    def test_sowing_convention_matches_v3(self):
        from data.ingest.train_imerg_products import sowing_date
        self.assertEqual(sowing_date('3170206', 'soy', 2020), date(2019, 11, 5))
        self.assertEqual(sowing_date('5107909', 'soy', 2020), date(2019, 10, 5))
        self.assertEqual(sowing_date('5107909', 'maize2', 2020), date(2020, 1, 5))

    def test_only_declared_years_enter_and_test_stays_sealed(self):
        from data.ingest.train_imerg_products import role_of_year
        self.assertEqual([role_of_year(y) for y in (2017, 2018, 2020, 2021)], ['train', 'train', 'validation', 'validation'])
        for year in (2016, 2019, 2022, 2023, 2024):
            self.assertIsNone(role_of_year(year))


if __name__ == '__main__':
    unittest.main()
