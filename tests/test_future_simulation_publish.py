"""Regional publication of future-planning runs: immutable artifacts, checksums and the capability catalog."""
import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
AS_OF = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)
ISSUED = datetime(2026, 11, 15, 12, tzinfo=UTC)


def pub():
    from data.serving import future_simulation_publish
    return future_simulation_publish


def power_payload(first, last, lat=-12.6, lon=-55.8):
    keys, d = [], first
    while d <= last:
        keys.append(d.strftime('%Y%m%d')); d += timedelta(days=1)
    rain = {k: (8.0 if int(k[6:]) % 4 == 0 and int(k[4:6]) in (10, 11, 12, 1, 2, 3) else 0.0) for k in keys}
    const = lambda v: {k: v for k in keys}
    return {'geometry': {'coordinates': [lon, lat, 370.0]}, 'properties': {'parameter': {
        'IMERG_PRECTOT': rain, 'T2M_MIN': const(19.0), 'T2M_MAX': const(31.0), 'ALLSKY_SFC_SW_DWN': const(20.0), 'WS2M': const(2.0),
        'T2MDEW': const(17.0), 'PS': const(96.0), 'RH2M': const(70.0)}}}


class PublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); root = Path(cls.tmp.name)
        (root / 'hist').mkdir(); (root / 'obs').mkdir()
        (root / 'hist' / 'power_5107925.json').write_text(json.dumps(power_payload(date(2001, 1, 1), date(2024, 12, 31))), encoding='utf-8')
        (root / 'hist' / 'power_9999999.json').write_text(json.dumps(power_payload(date(2018, 1, 1), date(2024, 12, 31))), encoding='utf-8')
        (root / 'obs' / 'BR_ibge_5107925.json').write_text(json.dumps(power_payload(date(2026, 6, 1), date(2026, 8, 31))), encoding='utf-8')
        (root / 'labels.csv').write_text('municipality_code,municipality,crop,harvest_year,area_harvested_ha,production_t,yield_kg_ha,label_valid\n' +
                                         ''.join(f'5107925,Sorriso - MT,soy,{y},1,1,{3000 + y},True\n' for y in range(2001, 2025)) +
                                         '5107925,Sorriso - MT,soy,2016,1,1,1,False\n', encoding='utf-8')
        places = [{'location_id': 'BR:ibge:5107925', 'name': 'Sorriso', 'state_code': '51', 'plans': ['soy', 'soy>maize2'], 'crops': ['soy', 'maize2', 'rice', 'coffee_arabica']},
                  {'location_id': 'BR:ibge:9999999', 'name': 'Curto', 'state_code': '51', 'plans': ['soy'], 'crops': ['soy']}]
        cls.out = root / 'out'
        cls.result = pub().publish_regional(places, history_dir=root / 'hist', observed_dir=root / 'obs', label_files=[root / 'labels.csv'],
                                            out_dir=cls.out, as_of=AS_OF, issued_at=ISSUED, seed=42, harvest_years=[2027, 2028], max_members=8)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_index_lists_runs_with_verified_checksums(self):
        index = json.loads((self.out / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual(index['contract'], 'future-simulation-publications.v1')
        self.assertEqual({p['plan_id'] for p in index['publications']}, {'soy', 'soy>maize2'})
        for p in index['publications']:
            self.assertEqual(p['visibility'], 'public_regional')
            for a in p['artifacts']:
                data = (self.out / a['path']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), a['sha256'])
                self.assertEqual(len(data), a['bytes'])
            series = json.loads(gzip.decompress((self.out / p['artifacts'][1]['path']).read_bytes()))
            self.assertEqual(len(series['members']), 8)

    def test_public_runs_carry_no_owner_or_private_fields(self):
        index = json.loads((self.out / 'index.json').read_text(encoding='utf-8'))
        for p in index['publications']:
            run = json.loads((self.out / p['artifacts'][0]['path']).read_text(encoding='utf-8'))
            text = json.dumps(run)
            for k in ('owner', 'user_id', 'email', 'field_polygon', 'price'):
                self.assertNotIn(f'"{k}', text)

    def test_invalid_labels_are_ignored(self):
        index = json.loads((self.out / 'index.json').read_text(encoding='utf-8'))
        p = next(p for p in index['publications'] if p['plan_id'] == 'soy')
        run = json.loads((self.out / p['artifacts'][0]['path']).read_text(encoding='utf-8'))
        self.assertEqual(run['seasons'][0]['yield']['value_kg_ha'], sum(3000 + y for y in range(2017, 2022)) / 5)

    def test_catalog_states_each_capability_with_reason(self):
        cat = json.loads((self.out / 'catalog.json').read_text(encoding='utf-8'))
        sorriso = next(p for p in cat['places'] if p['location_id'] == 'BR:ibge:5107925')
        caps = {(c['crop_id'], c['harvest_year']): c for c in sorriso['capabilities']}
        self.assertEqual(caps[('soy', 2027)]['status'], 'ready')
        self.assertTrue(caps[('soy', 2027)]['run_ids'])
        self.assertEqual(caps[('maize2', 2028)]['status'], 'ready')
        self.assertEqual((caps[('rice', 2027)]['status'], caps[('rice', 2027)]['reason_code']), ('unsupported', 'flooded_system_not_represented'))
        self.assertEqual(caps[('coffee_arabica', 2027)]['reason_code'], 'perennial_engine_missing')
        short = next(p for p in cat['places'] if p['location_id'] == 'BR:ibge:9999999')
        self.assertEqual((short['capabilities'][0]['status'], short['capabilities'][0]['reason_code']), ('missing_data', 'insufficient_history'))
        self.assertEqual(sorriso['history_years'], 24)

    def test_republishing_is_idempotent(self):
        before = (self.out / 'index.json').read_bytes()
        runs_before = sorted(p.name for p in (self.out / 'runs').iterdir())
        pub().publish_regional(json.loads((self.out / 'catalog.json').read_text(encoding='utf-8'))['inputs']['places'],
                               history_dir=Path(self.tmp.name) / 'hist', observed_dir=Path(self.tmp.name) / 'obs', label_files=[Path(self.tmp.name) / 'labels.csv'],
                               out_dir=self.out, as_of=AS_OF, issued_at=ISSUED, seed=42, harvest_years=[2027, 2028], max_members=8)
        self.assertEqual(sorted(p.name for p in (self.out / 'runs').iterdir()), runs_before)
        self.assertEqual((self.out / 'index.json').read_bytes(), before)

    def test_measurements_are_recorded(self):
        index = json.loads((self.out / 'index.json').read_text(encoding='utf-8'))
        m = index['publications'][0]['measured']
        for k in ('seconds', 'peak_python_mib', 'series_bytes_raw', 'series_bytes_gzip'):
            self.assertIn(k, m)
        self.assertLess(m['series_bytes_gzip'], m['series_bytes_raw'])


if __name__ == '__main__':
    unittest.main()
