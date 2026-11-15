import gzip
import csv
from datetime import date
from pathlib import Path
import unittest

from data.ingest import training_foundation as tf


class TrainingFoundationTests(unittest.TestCase):
    def test_zero_yield_is_not_missing_when_area_is_positive(self):
        self.assertTrue(hasattr(tf, 'valid_label_values'))
        record = dict(area_planted_ha=10, area_harvested_ha=10, production_t=0, yield_kg_ha=0)
        self.assertTrue(tf.valid_label_values(record))
        self.assertFalse(tf.valid_label_values({**record, 'yield_kg_ha': None}))
        self.assertFalse(tf.valid_label_values({**record, 'area_harvested_ha': 0}))

    def test_json_transport_accepts_plain_and_gzip(self):
        self.assertTrue(hasattr(tf, 'decode_json'))
        payload = b'{"id": 3170206}'
        self.assertEqual(tf.decode_json(payload), tf.decode_json(gzip.compress(payload)))

    def test_missing_rain_breaks_spell_and_blocks_features(self):
        self.assertEqual(tf.longest_dry([0, 0, None, 0, 0, 0, 1]), 3)
        result = tf.summarize([dict(rain_mm=None, t_min_c=20, t_max_c=30,
                                    radiation_mj_m2=20, rh2m_pct=60, ws2m_m_s=2)])
        self.assertFalse(result['complete'])
        self.assertNotIn('rain_sum_mm', result)

    def test_dry_threshold_and_rain_units(self):
        rows = [dict(rain_mm=p, t_min_c=20, t_max_c=36, radiation_mj_m2=20,
                     rh2m_pct=60, ws2m_m_s=2) for p in [0, .9, 1, 10]]
        result = tf.summarize(rows)
        self.assertEqual(result['dry_days_lt1mm'], 2)
        self.assertEqual(result['max_dry_spell_days'], 2)
        self.assertEqual(result['rain_sum_mm'], 11.9)

    def test_collected_dataset_has_unique_dates_and_valid_windows(self):
        path = Path('data/processed/training_v1')
        if not path.exists():
            self.skipTest('Acquired dataset is not present')
        with (path / 'weather_daily.csv').open(encoding='utf-8-sig', newline='') as stream:
            daily = list(csv.DictReader(stream))
        self.assertEqual(len(daily), len({(r['municipality_code'], r['day_utc']) for r in daily}))
        with (path / 'research_samples.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), len({r['sample_id'] for r in rows}))
        for row in rows:
            start, end = date.fromisoformat(row['window_start']), date.fromisoformat(row['feature_cutoff'])
            self.assertEqual(int(row['days']), (end-start).days+1)
            self.assertEqual(end.year, int(row['harvest_year']))
            self.assertEqual(row['complete'], 'True')
            self.assertGreaterEqual(float(row['yield_kg_ha']), 0)


if __name__ == '__main__':
    unittest.main()
