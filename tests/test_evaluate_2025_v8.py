"""Leakage and traceability guards of the staged 2025 evaluation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def soy_target(cutoff, year=2025):
    import pandas as pd
    return pd.DataFrame([{'sample_id': f'5107925:soy:{year}', 'harvest_year': year, 'horizon_days': 60, 'feature_cutoff': cutoff,
                          'yield_lag_count': 3, 'milestone': 'day_60'}])


class Evaluate2025Tests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        from data.ingest import evaluate_2025_v8 as module
        self.module = module
        self.frozen = self.folder / 'frozen.json'
        self.frozen.write_text(json.dumps({'choices': {}, 'rules': {'decision_rule': {}}}), encoding='utf-8')
        self.patches = [mock.patch.object(module, 'RESULT', self.folder / 'result'), mock.patch.object(module, 'FROZEN', self.frozen),
                        mock.patch.object(module, 'SEALED', self.folder / 'sealed')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        import shutil
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.folder)

    def test_history_with_the_target_harvest_is_rejected(self):
        from data.ingest.cohort_v8 import ContaminationError
        self.module.guard_history({'5107925': {2023: 3600.0, 2024: 3700.0}})
        with self.assertRaises(ContaminationError):
            self.module.guard_history({'5107925': {2024: 3700.0, 2025: 3900.0}})

    def test_target_rows_must_be_2025_unlabelled_with_published_lags(self):
        from data.ingest.cohort_v8 import ContaminationError
        self.module.check_target(soy_target('2024-12-03'), 'soy')
        with self.assertRaises(ContaminationError):
            self.module.check_target(soy_target('2023-12-03', year=2024), 'soy')
        with self.assertRaises(ContaminationError):  # PAM 2023 was published on 2024-09-12
            self.module.check_target(soy_target('2024-09-01'), 'soy')
        with self.assertRaises(ContaminationError):
            self.module.check_target(soy_target('2024-12-03').assign(yield_kg_ha=3500.0), 'soy')

    def test_frozen_choices_cannot_change_after_the_start(self):
        from data.ingest.cohort_v8 import ContaminationError
        self.module.frozen_checked()
        self.module.frozen_checked()  # resume with the same file
        self.frozen.write_text(json.dumps({'choices': {'x': {}}, 'rules': {}}), encoding='utf-8')
        with self.assertRaises(ContaminationError):
            self.module.frozen_checked()

    def test_labels_wait_for_every_prediction_checkpoint(self):
        self.frozen.write_text(json.dumps({'choices': {'soy_mgmt': {'crop': 'soy', 'municipalities': ['5107925']}}, 'rules': {}}), encoding='utf-8')
        with mock.patch.object(self.module, 'fetch_labels') as fetch, self.assertRaises(SystemExit):
            self.module.labels()
        fetch.assert_not_called()

    def test_an_existing_score_is_never_recomputed(self):
        self.frozen.write_text(json.dumps({'choices': {'soy_mgmt': {'crop': 'soy'}}, 'rules': {'decision_rule': {}}}), encoding='utf-8')
        out = self.folder / 'result' / 'score_soy_mgmt.json'
        out.parent.mkdir(parents=True)
        out.write_text('{"original": true}', encoding='utf-8')
        self.module.score()
        self.assertEqual(json.loads(out.read_text(encoding='utf-8')), {'original': True})

    def test_cached_2025_yields_are_rejected_in_any_predict_input(self):
        import pandas as pd
        from data.ingest.cohort_v8 import ContaminationError
        payload = [{'D1C': 'Município', 'D3C': 'Ano'}, {'D1C': '5107925', 'D3C': '2024', 'V': '3600'}]
        clean = self.folder / 'pam_ok.json'
        clean.write_text(json.dumps(payload), encoding='utf-8')
        self.module.reject_2025_yields(clean)
        leaked = self.folder / 'pam_leak.json'
        leaked.write_text(json.dumps(payload + [{'D1C': '5107925', 'D3C': '2025', 'V': '3900'}]), encoding='utf-8')
        with self.assertRaises(ContaminationError):
            self.module.reject_2025_yields(leaked)
        table = self.folder / 'labels.csv'
        pd.DataFrame({'harvest_year': [2024, 2025], 'yield_kg_ha': [1.0, 2.0]}).to_csv(table, index=False)
        with self.assertRaises(ContaminationError):
            self.module.reject_2025_yields(table)

    def test_inputs_must_match_the_frozen_hashes(self):
        from data.ingest.cohort_v8 import ContaminationError
        from data.ingest.water_balance_run import sha
        path = ROOT / 'data/processed/training_v8' / '_test_input.json'
        path.write_text('[]', encoding='utf-8')
        try:
            frozen = {path.relative_to(ROOT).as_posix(): sha(path)}
            self.assertTrue(self.module.verify_inputs('soy', frozen))
            path.write_text('[{"D3C": "2025"}]', encoding='utf-8')
            with self.assertRaises(ContaminationError):
                self.module.verify_inputs('soy', frozen)
        finally:
            path.unlink()

    def test_label_history_reads_only_allowlisted_files(self):
        from data.ingest.cohort_v8 import ContaminationError
        if not (ROOT / 'data/raw/training_v7/labels_all.csv').exists():
            self.skipTest('labels not on this machine')
        with self.assertRaises(ContaminationError):
            self.module.label_history('coffee_conilon', allowed={})

    def test_every_pam_lag_used_in_2025_is_available_at_its_decision(self):
        """available_at <= decision_at for each lag the builders actually read, cutoff by cutoff (history loaded to 2024)."""
        from datetime import timedelta
        from data.ingest.audit_v8 import release_date
        from data.ingest.availability_v8 import STATES, lags_used, windows
        for crop in STATES:
            for label, _, _, _, cutoff in windows(crop, 2025):
                decision = cutoff + timedelta(days=1)
                for pam in lags_used(crop, 2025, label, range(2001, 2025)):
                    self.assertLessEqual(release_date(pam), decision, (crop, label, pam))

    def test_training_and_history_stop_before_2025(self):
        if not (ROOT / 'data/processed/training_v6/test_samples.parquet').exists():
            self.skipTest('cohorts not built on this machine')
        frame = self.module.training_frame('soy')
        self.assertEqual(int(frame.harvest_year.max()), 2023)
        years = {y for h in self.module.label_history('soy').values() for y in h}
        self.assertEqual(max(years), 2024)


class AvailabilityTests(unittest.TestCase):
    def test_unpublished_share_of_a_window(self):
        from datetime import date
        from data.ingest.availability_v8 import unavailable_fraction
        self.assertEqual(unavailable_fraction(date(2024, 11, 1), date(2024, 12, 30), 105), 1.0)
        self.assertEqual(unavailable_fraction(date(2024, 1, 1), date(2024, 1, 10), 0), 0.0)
        self.assertAlmostEqual(unavailable_fraction(date(2024, 1, 1), date(2024, 12, 31), 105), 104 / 366)


if __name__ == '__main__':
    unittest.main()
