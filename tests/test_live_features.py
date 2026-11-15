import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cohort_row(parquet, crop, code, year, horizon):
    import pandas as pd
    frame = pd.read_parquet(parquet, filters=[('crop', '==', crop), ('municipality_code', '==', code)])
    row = frame[(frame.harvest_year == year) & (frame.horizon_days == horizon)]
    return row.iloc[0].to_dict() if len(row) == 1 else None


class LiveFeatureParityTests(unittest.TestCase):
    def check(self, parquet, raw_root, crop, code, year, horizon):
        import json
        import math
        import pandas as pd
        from data.ingest import cohort_v5 as v5
        from data.serving.live_features import feature_row
        if not parquet.exists():
            self.skipTest('Cohort not built on this machine')
        expected = cohort_row(parquet, crop, code, year, horizon)
        weather, elevation = v5.daily_weather(code, root=raw_root)
        if crop in ('soy', 'maize2'):
            history = {}
            for item in v5.parse_labels(json.loads((ROOT / f'data/raw/training_v6/pam_{crop}_{code[:2]}.json').read_bytes()), crop):
                if item['municipality_code'] == code and item['label_valid']:
                    history[item['harvest_year']] = item['yield_kg_ha']
        else:
            labels = pd.read_csv(ROOT / 'data/raw/training_v7/labels_all.csv', dtype={'municipality_code': str})
            part = labels[(labels.municipality_code == code) & (labels.crop == crop) & labels.label_valid]
            history = dict(zip(part.harvest_year, part.yield_kg_ha))
        location = {'latitude': expected['latitude'], 'longitude': expected['longitude']}
        row = feature_row(crop, code, year, horizon, weather, history, location, elevation)
        compared = 0
        for key, value in row.items():
            if key in expected and key not in ('sowing_date', 'feature_cutoff', 'milestone', 'municipality_code', 'crop'):
                a, b = expected[key], value
                if a is None or (isinstance(a, float) and math.isnan(a)):
                    self.assertTrue(b is None or (isinstance(b, float) and math.isnan(b)), key)
                else:
                    self.assertAlmostEqual(float(a), float(b), places=6, msg=key)
                compared += 1
        self.assertGreater(compared, 15)
        self.assertEqual(row['feature_cutoff'], expected['feature_cutoff'])

    def test_soy_row_matches_the_v6_cohort(self):
        self.check(ROOT / 'data/processed/training_v6/samples.parquet', ROOT / 'data/raw/training_v6', 'soy', '5107925', 2021, 75)

    def test_wheat_row_matches_the_v7_cohort(self):
        from data.ingest.cohort_v7 import power_root
        self.check(ROOT / 'data/processed/training_v7/samples.parquet', power_root('3119302'), 'wheat', '3119302', 2020, 60)

    def test_coffee_row_matches_the_v7_cohort(self):
        from data.ingest.cohort_v7 import power_root
        self.check(ROOT / 'data/processed/training_v7/samples.parquet', power_root('3100302'), 'coffee_arabica', '3100302', 2019, 183)

    def test_swapped_sources_recompute_et0_and_leave_gaps_incomplete(self):
        from data.serving.live_features import swap_sources
        weather = {'2021-01-01': {'day_utc': '2021-01-01', 'rain_mm': 1.0, 't_min_c': 20.0, 't_max_c': 30.0, 'radiation_mj_m2': 20.0,
                                  'ws2m_m_s': 2.0, 'pressure_kpa': 95.0, 'dewpoint_c': 18.0, 'rh2m_pct': 70.0, 'et0_mm': 4.0}}
        out = swap_sources(weather, -12.0, 400.0, rain={'2021-01-01': 7.5}, radiation={'2021-01-01': 25.0})
        self.assertEqual(out['2021-01-01']['rain_mm'], 7.5)
        self.assertGreater(out['2021-01-01']['et0_mm'], 4.0)
        gap = swap_sources(weather, -12.0, 400.0, rain={}, radiation={})
        self.assertIsNone(gap['2021-01-01']['rain_mm'])
        self.assertIsNone(gap['2021-01-01']['et0_mm'])


if __name__ == '__main__':
    unittest.main()
