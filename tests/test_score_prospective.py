"""Prospective scoring rules (protocol amendments A1-A6): counts, coverage, priority states, provenance and dates."""
import random
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOOD_INPUTS = [{'source': 'NASA POWER daily', 'retrieved_at': '2026-11-14T10:00:00+00:00', 'retrieved_at_basis': 'recorded'},
               {'source': 'GPM IMERG Late daily (GES DISC)', 'days': 30, 'retrieved_at_max': '2026-11-14T10:00:00+00:00',
                'retrieved_at_basis': 'recorded'},
               {'source': 'IBGE PAM published yields (SIDRA)', 'retrieved_at': '2026-11-14T10:00:00+00:00', 'retrieved_at_basis': 'recorded'}]


def record(code, truth, rng, issued='2026-11-14T15:00:00+00:00', decision='2026-11-14', inputs=None, models=None):
    models = models or {'good': 50.0, 'bad': 400.0, 'ref': 200.0}
    return {'key': f'soy:{code}:2027:30', 'crop': 'soy', 'municipality_code': code, 'harvest_year': 2027, 'cutoff_days': 30,
            'sowing_date': '2026-10-05', 'decision_at': decision, 'issued_at': issued, 'protocol_sha256': 'v1',
            'inputs': GOOD_INPUTS if inputs is None else inputs, '_sealed_file': 'b.jsonl.gz',
            'predictions': {m: {'estimate_kg_ha': truth + rng.choice((-1, 1)) * e} for m, e in models.items()}}


def batch(n=60, states=('31', '51'), **kwargs):
    rng = random.Random(0)
    recs, labels = [], {}
    for i in range(n):
        code = states[i % len(states)] + f'{i:05d}'
        labels[code] = 3500 + rng.uniform(-300, 300)
        recs.append(record(code, labels[code], rng, **kwargs))
    return recs, labels


VERSIONS = {'v1': '2026-11-14T09:00:00+00:00'}
SEALED = {'b.jsonl.gz': __import__('datetime').date(2026, 11, 14)}


class ScoreTests(unittest.TestCase):
    def score(self, recs, labels, states=('31', '51'), candidates=('good', 'bad'), expected=None):
        from data.serving.score_prospective import score_scope
        return score_scope(recs, labels, 'soy', states, list(candidates), ['ref'], expected, VERSIONS, SEALED)['candidates']

    def test_better_candidate_approved_worse_not(self):
        recs, labels = batch()
        result = self.score(recs, labels)
        self.assertTrue(result['good']['approved_for_operational_use'])
        self.assertFalse(result['bad']['approved_for_operational_use'])

    def test_coverage_counts_expected_keys_without_records(self):
        recs, labels = batch()
        expected = {r['key'] for r in recs} | {f'soy:5199{i:03d}:2027:30' for i in range(60)}  # half never recorded
        result = self.score(recs, labels, candidates=('good',), expected={'good': expected})
        self.assertAlmostEqual(result['good']['coverage'], 0.5)
        self.assertFalse(result['good']['approved_for_operational_use'])

    def test_priority_state_without_pairs_blocks_the_scope(self):
        recs, labels = batch(states=('51',))
        result = self.score(recs, labels, candidates=('good',))
        self.assertEqual(result['good']['status'], 'inconclusive_priority_state_without_pairs')
        self.assertEqual(result['good']['priority_states_without_pairs'], ['31'])
        self.assertFalse(result['good']['approved_for_operational_use'])

    def test_provenance_rejections(self):
        from data.serving.score_prospective import provenance_problems
        rng = random.Random(1)
        ok = record('5100001', 3500, rng)
        self.assertEqual(provenance_problems(ok, VERSIONS, SEALED), [])
        missing = record('5100001', 3500, rng, inputs=GOOD_INPUTS[1:])
        self.assertIn('missing provenance: NASA POWER', provenance_problems(missing, VERSIONS, SEALED))
        after = record('5100001', 3500, rng, inputs=[{**GOOD_INPUTS[0], 'retrieved_at': '2026-11-14T16:00:00+00:00'}, *GOOD_INPUTS[1:]])
        self.assertTrue(any(p.startswith('retrieved after issue') for p in provenance_problems(after, VERSIONS, SEALED)))
        self.assertIn('protocol version unknown', provenance_problems(ok, {}, SEALED))
        self.assertIn('protocol version registered after issue', provenance_problems(ok, {'v1': '2026-11-14T16:00:00+00:00'}, SEALED))
        mtime = record('5100001', 3500, rng, inputs=[{**GOOD_INPUTS[0], 'retrieved_at_basis': 'file_mtime'}, *GOOD_INPUTS[1:]])
        self.assertEqual(provenance_problems(mtime, VERSIONS, SEALED), [])            # proven by same-day sealing
        late_seal = {'b.jsonl.gz': __import__('datetime').date(2026, 11, 20)}
        self.assertTrue(any(p.startswith('availability not proven') for p in provenance_problems(mtime, VERSIONS, late_seal)))
        no_time = record('5100001', 3500, rng, inputs=[{**GOOD_INPUTS[0], 'retrieved_at': None}, *GOOD_INPUTS[1:]])
        self.assertTrue(any(p.startswith('no retrieval time') for p in provenance_problems(no_time, VERSIONS, SEALED)))

    def test_rejected_provenance_does_not_enter_approval(self):
        recs, labels = batch(inputs=GOOD_INPUTS[1:])
        result = self.score(recs, labels, candidates=('good',))
        self.assertEqual(result['good']['provenance_rejected'], 60)
        self.assertFalse(result['good']['approved_for_operational_use'])

    def test_dates_use_brasilia_days(self):
        from data.serving.score_prospective import dates, on_time, provenance_problems
        rng = random.Random(2)
        r = record('5100001', 3500, rng, issued='2026-11-17T01:30:00+00:00', decision='2026-11-16')  # 22:30 on the 16th in Brasilia
        d = dates(r)
        self.assertEqual((str(d['cutoff_date']), str(d['planned_issue']), str(d['deadline']), str(d['issued_date'])),
                         ('2026-11-03', '2026-11-09', '2026-11-16', '2026-11-16'))
        self.assertTrue(on_time(r))
        self.assertFalse(on_time(record('5100001', 3500, rng, issued='2026-11-17T12:00:00+00:00', decision='2026-11-17')))
        wrong = record('5100001', 3500, rng, issued='2026-11-17T01:30:00+00:00', decision='2026-11-17')
        self.assertIn('decision_at differs from the Brasilia day of issued_at', provenance_problems(wrong, VERSIONS, SEALED))

    def test_holm(self):
        from data.serving.score_prospective import holm
        adjusted = holm({'a': 0.01, 'b': 0.02, 'c': 0.04})
        self.assertAlmostEqual(adjusted['a'], 0.03)
        self.assertAlmostEqual(adjusted['c'], 0.04)


class FirstBatchTests(unittest.TestCase):
    """Reproduce the audited counts of sealed/2026-11-14.jsonl.gz (never modified)."""

    def setUp(self):
        if not (ROOT / 'data/prospective_registry/sealed/2026-11-14.jsonl.gz').exists():
            self.skipTest('registry not on this machine')

    def test_counts_chain_models_and_provenance(self):
        import collections
        from data.serving.score_prospective import load_sealed, on_time, protocol_versions, provenance_problems
        batch = [r for r in load_sealed() if r['_sealed_file'] == '2026-11-14.jsonl.gz']
        self.assertEqual(len(batch), 1917)
        timely = [r for r in batch if on_time(r)]
        self.assertEqual(len(timely), 244)
        self.assertEqual(collections.Counter(r['crop'] for r in timely), {'soy': 84, 'maize1': 160})
        models = {m for r in batch for m in r['predictions']}
        self.assertEqual(len(models), 38)
        self.assertFalse(any(m.startswith('v9:') for m in models))
        from datetime import date
        problems = [provenance_problems(r, protocol_versions(), {'2026-11-14.jsonl.gz': date(2026, 11, 14)}) for r in timely]
        self.assertTrue(all(p == [] for p in problems), {p for ps in problems for p in ps})


if __name__ == '__main__':
    unittest.main()
