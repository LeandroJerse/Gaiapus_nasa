import unittest
from datetime import date, timedelta


def sidra(code, year, variable, value, category):
    unit = {'216': '1006', '214': '1017', '112': '33'}[variable]
    return {'D1C': code, 'D1N': 'X - MG', 'D2C': variable, 'D3C': str(year), 'D4C': category, 'MC': unit, 'V': value}


def triple(code, year, area, production, yield_, category='40127'):
    return [sidra(code, year, '216', area, category), sidra(code, year, '214', production, category), sidra(code, year, '112', yield_, category)]


class CohortV7Tests(unittest.TestCase):
    def test_rejections_are_split_by_reason(self):
        from data.ingest.cohort_v7 import parse_labels
        payload = [{}] + triple('4100001', 2010, '1000', '3000', '3000') + triple('4100002', 2010, '0', '0', '0') \
            + triple('4100003', 2010, '-', '-', '-') + triple('4100004', 2010, '1000', '-', '3000') + triple('4100005', 2010, '1000', '3000', '4000')
        reasons = {r['municipality_code']: r['rejection_reason'] for r in parse_labels(payload, 'wheat', '40127')}
        self.assertEqual(reasons, {'4100001': '', '4100002': 'zero_harvested_area', '4100003': 'all_fields_absent',
                                   '4100004': 'field_absent', '4100005': 'production_area_yield_mismatch'})
        with self.assertRaises(ValueError):
            parse_labels([{}] + triple('4100001', 2022, '1000', '3000', '3000'), 'wheat', '40127')

    def test_registry_calendars_and_horizons(self):
        from data.ingest.crops import REGISTRY, horizons, sowing_date
        self.assertEqual(sowing_date('wheat', '4100001', 2015), date(2015, 5, 15))
        self.assertEqual(sowing_date('rice', '4300001', 2015), date(2014, 10, 15))
        self.assertEqual(sowing_date('bean2', '3100001', 2015), date(2015, 2, 10))
        self.assertEqual(horizons('bean1'), (30, 45, 60, 75, 90, 105))
        self.assertEqual(horizons('soy'), (30, 45, 60, 75, 90, 105))
        self.assertIsNone(REGISTRY['rice']['water'])
        self.assertTrue(all(h < sum(REGISTRY['bean2']['water']['phase_days']) for h in horizons('bean2')))

    def test_coffee_lags_respect_publication_at_each_milestone(self):
        from data.ingest.cohort_v7 import coffee_lags
        history = {2014: 1000.0, 2015: 2000.0, 2016: 1100.0, 2017: 2100.0, 2018: 9999.0, 2019: 9999.0}
        pre = coffee_lags(history, 2019, 'pre_flowering')
        post = coffee_lags(history, 2019, 'post_flowering')
        self.assertIsNone(pre['yield_lag2_kg_ha'])                      # 2017 not yet published in Aug 2018
        self.assertEqual(pre['ref_last_available_kg_ha'], 1100.0)       # 2016
        self.assertEqual(pre['ref_same_phase_kg_ha'], 2000.0)           # 2015 (lag 4)
        self.assertEqual(post['ref_last_available_kg_ha'], 2100.0)      # 2017
        self.assertEqual(post['ref_same_phase_kg_ha'], 2100.0)
        self.assertEqual(post['ref_biennial_mean_kg_ha'], (2100.0 + 2000.0) / 2)
        self.assertNotIn(9999.0, [v for v in post.values() if v is not None])

    def test_coffee_features_use_nothing_after_the_milestone(self):
        from data.ingest.cohort_v7 import coffee_features
        start = date(2018, 6, 1)
        weather = {(start + timedelta(days=i)).isoformat(): {'day_utc': (start + timedelta(days=i)).isoformat(), 'rain_mm': 0.0 if i % 9 else 15.0,
                   'et0_mm': 4.0, 't_min_c': 15.0, 't_max_c': 28.0, 'radiation_mj_m2': 18.0, 'rh2m_pct': 70.0, 'ws2m_m_s': 2.0}
                   for i in range(400)}
        cutoff = date(2018, 11, 30)
        first = coffee_features(weather, start, cutoff)
        for day, row in weather.items():
            if day > cutoff.isoformat():
                row.update(rain_mm=999.0, et0_mm=99.0)
        self.assertEqual(first, coffee_features(weather, start, cutoff))
        self.assertGreater(first['coffee_deficit_dry_season_mm'], 0)
        weather['2018-07-01']['rain_mm'] = None
        self.assertIsNone(coffee_features(weather, start, cutoff))

    def test_selection_uses_only_the_given_years(self):
        from data.ingest.cohort_v7 import select
        rows = [{'municipality_code': '3100001', 'harvest_year': y, 'area_harvested_ha': 500.0, 'label_valid': True} for y in range(2012, 2017)] \
            + [{'municipality_code': '3100002', 'harvest_year': y, 'area_harvested_ha': 900.0, 'label_valid': True} for y in range(2017, 2022)]
        self.assertEqual(select(rows, 4, 200, years=(2012, 2016)), ['3100001'])


if __name__ == '__main__':
    unittest.main()
