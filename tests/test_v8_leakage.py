import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frame(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def soy_row(year, cutoff, lag_count=3, sample=None):
    return {'sample_id': sample or f'5107925:soy:{year}', 'harvest_year': year, 'horizon_days': 60, 'feature_cutoff': cutoff,
            'yield_lag_count': lag_count, 'milestone': 'day_60'}


class V8LeakageTests(unittest.TestCase):
    def test_a_2022_row_is_rejected(self):
        from data.ingest.cohort_v8 import ContaminationError, validate
        clean = frame([soy_row(2021, '2020-12-03')])
        validate(clean, 'soy')
        with self.assertRaises(ContaminationError) as raised:
            validate(frame([soy_row(2021, '2020-12-03'), soy_row(2022, '2021-12-03')]), 'soy')
        self.assertIn('after 2021', str(raised.exception))

    def test_duplicated_keys_are_rejected(self):
        from data.ingest.cohort_v8 import ContaminationError, validate
        with self.assertRaises(ContaminationError):
            validate(frame([soy_row(2021, '2020-12-03'), soy_row(2021, '2020-12-03')]), 'soy')

    def test_lag_unpublished_at_the_decision_is_rejected(self):
        from data.ingest.cohort_v8 import ContaminationError, validate
        # Harvest 2021 decided on 2020-09-30: its Y-2 lag (PAM 2019) was published only on 2020-10-01.
        with self.assertRaises(ContaminationError) as raised:
            validate(frame([soy_row(2021, '2020-09-29')]), 'soy')
        self.assertIn('PAM 2019', str(raised.exception))
        validate(frame([soy_row(2021, '2020-10-01')]), 'soy')  # decided on 2020-10-02: published

    def test_coffee_lag_rules_follow_the_milestone(self):
        from data.ingest.cohort_v8 import ContaminationError, validate
        row = {'sample_id': '3170206:coffee_arabica:2021', 'harvest_year': 2021, 'horizon_days': 92, 'feature_cutoff': '2020-08-31',
               'milestone': 'pre_flowering', 'yield_lag2_kg_ha': None, 'yield_lag3_kg_ha': 1500.0, 'yield_lag4_kg_ha': 1400.0,
               'yield_lag5_kg_ha': None, 'yield_lag6_kg_ha': None}
        validate(frame([row]), 'coffee_arabica')
        leaked = dict(row, yield_lag2_kg_ha=1600.0, milestone='post_flowering', feature_cutoff='2020-08-31')
        with self.assertRaises(ContaminationError):
            validate(frame([leaked]), 'coffee_arabica')  # PAM 2019 used on 2020-09-01, published 2020-10-01

    def test_forecast_issued_or_available_after_the_decision_is_rejected(self):
        from data.ingest.cohort_v8 import ContaminationError, check_forecast, check_observation
        check_forecast('2026-11-14T00:00:00+00:00', '2026-11-14T08:05:00+00:00', '2026-11-14T12:00:00+00:00')
        with self.assertRaises(ContaminationError):
            check_forecast('2026-11-14T12:00:00+00:00', '2026-11-14T18:00:00+00:00', '2026-11-14T06:00:00+00:00')
        with self.assertRaises(ContaminationError):
            check_forecast('2026-11-14T00:00:00+00:00', '2026-11-14T08:05:00+00:00', '2026-11-14T07:00:00+00:00')
        check_observation('2026-11-13', date(2026, 11, 14))
        with self.assertRaises(ContaminationError):
            check_observation('2026-11-14', date(2026, 11, 14))

    def test_loader_accepts_only_allowlisted_files_with_matching_checksums(self):
        from data.ingest.cohort_v8 import ContaminationError, load_development
        manifest = ROOT / 'data/processed/training_v8/audit.json'
        if not manifest.exists():
            self.skipTest('v8 audit not run on this machine')
        with self.assertRaises(ContaminationError):
            load_development('soy', files={'soy': 'data/processed/training_v6/test_samples.parquet'})
        with tempfile.TemporaryDirectory() as folder:
            audit = json.loads(manifest.read_text(encoding='utf-8'))
            audit['development_manifest']['data/processed/training_v6/samples.parquet']['sha256'] = '0' * 64
            fake = Path(folder) / 'audit.json'
            fake.write_text(json.dumps(audit), encoding='utf-8')
            with self.assertRaises(ContaminationError):
                load_development('soy', manifest_path=fake)
        loaded = load_development('coffee_conilon')
        self.assertLessEqual(int(loaded.harvest_year.max()), 2021)

    def test_label_derived_high_year_is_never_a_feature(self):
        from data.ingest import train_v7
        columns = set(train_v7.COFFEE_BASE) | {c for cols in train_v7.feature_sets('coffee')[1].values() for c in cols}
        self.assertNotIn('high_year', columns)


if __name__ == '__main__':
    unittest.main()
