"""Forcing adapter for the crop simulation engine: quality, temporal cutoff, provenance and cache identity.

Payloads are synthetic NASA POWER-shaped JSON (diagnostic only, not observations).
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc


def fx():
    from data.serving import simulation_forcing
    return simulation_forcing


def payload(first=date(2021, 1, 1), days=40, rain_var='IMERG_PRECTOT', units=None, overrides=None):
    keys = [(first + timedelta(days=i)).strftime('%Y%m%d') for i in range(days)]
    base = {rain_var: 3.0, 'T2M_MIN': 19.0, 'T2M_MAX': 31.0, 'ALLSKY_SFC_SW_DWN': 21.0, 'WS2M': 2.0, 'T2MDEW': 18.0, 'PS': 96.0}
    param = {name: {k: v for k in keys} for name, v in base.items()}
    for (name, i), v in (overrides or {}).items():
        param[name][keys[i]] = v
    u = {name: {'units': unit} for name, unit in {rain_var: 'mm/day', 'T2M_MIN': 'C', 'T2M_MAX': 'C', 'ALLSKY_SFC_SW_DWN': 'MJ/m^2/day', 'WS2M': 'm/s', 'T2MDEW': 'C', 'PS': 'kPa'}.items()}
    u.update(units or {})
    return {'geometry': {'coordinates': [-55.78, -12.70, 365.0]}, 'properties': {'parameter': param}, 'parameters': u}


def request(**kw):
    f = fx()
    args = dict(location_id='BR:ibge:5107925', latitude=-12.70, longitude=-55.78, start=date(2021, 1, 1), end=date(2021, 2, 9), mode='historical_replay',
                decision_at=None, source=f.POWER_IMERG)
    args.update(kw)
    return f.ForcingRequest(**args)


RETRIEVED = datetime(2026, 11, 15, 12, tzinfo=UTC)


class NormalisationTests(unittest.TestCase):
    def test_complete_payload_builds_contiguous_days_with_et0(self):
        b = fx().build_forcing_from_power(payload(), request(), retrieved_at=RETRIEVED)
        self.assertEqual(len(b.records), 40)
        self.assertTrue(all(r.precipitation_mm == 3.0 and r.et0_mm is not None and r.et0_mm > 0 for r in b.records))
        q = fx().validate_forcing(b, 'historical_replay')
        self.assertTrue(q.usable)

    def test_sentinel_becomes_missing_with_flag_never_zero(self):
        b = fx().build_forcing_from_power(payload(overrides={('IMERG_PRECTOT', 5): -999.0}), request(), retrieved_at=RETRIEVED)
        r = b.records[5]
        self.assertIsNone(r.precipitation_mm)
        self.assertIn('precipitation_missing', r.flags)
        q = fx().validate_forcing(b, 'historical_replay')
        self.assertFalse(q.usable)
        self.assertEqual(q.reason_code, 'FORCING_INCOMPLETE')
        self.assertEqual(q.missing_days, ['2021-01-06'])
        days = fx().to_engine_days(b)
        self.assertIsNone(days[5].precipitation_mm)  # the engine then refuses; nothing turns it into zero

    def test_h04_thirty_days_without_temperature_are_not_interpolated(self):
        over = {('T2M_MAX', i): -999.0 for i in range(5, 35)}
        b = fx().build_forcing_from_power(payload(overrides=over), request(), retrieved_at=RETRIEVED)
        self.assertTrue(all(b.records[i].et0_mm is None and 'et0_inputs_missing' in b.records[i].flags for i in range(5, 35)))
        q = fx().validate_forcing(b, 'historical_replay')
        self.assertFalse(q.usable)
        self.assertEqual(len(q.missing_days), 30)

    def test_physical_ranges_and_tmin_tmax(self):
        b = fx().build_forcing_from_power(payload(overrides={('T2M_MIN', 3): 35.0}), request(), retrieved_at=RETRIEVED)
        self.assertIsNone(b.records[3].et0_mm)
        self.assertIn('tmin_above_tmax', b.records[3].flags)
        b = fx().build_forcing_from_power(payload(overrides={('IMERG_PRECTOT', 2): 2000.0}), request(), retrieved_at=RETRIEVED)
        self.assertIsNone(b.records[2].precipitation_mm)
        self.assertIn('precipitation_out_of_range', b.records[2].flags)

    def test_duplicate_or_missing_day_rejected(self):
        p = payload(days=40)
        del p['properties']['parameter']['T2M_MIN']['20210110']
        with self.assertRaises(fx().SimulationInputError) as ctx:
            fx().build_forcing_from_power(p, request(), retrieved_at=RETRIEVED)
        self.assertEqual(ctx.exception.code, 'FORCING_INCOMPLETE')
        with self.assertRaises(fx().SimulationInputError):
            fx().build_forcing_from_power(payload(days=30), request(), retrieved_at=RETRIEVED)  # shorter than requested

    def test_product_and_unit_mismatch_rejected(self):
        with self.assertRaises(fx().SimulationInputError) as ctx:
            fx().build_forcing_from_power(payload(rain_var='PRECTOTCORR'), request(), retrieved_at=RETRIEVED)
        self.assertEqual(ctx.exception.code, 'SOURCE_MISMATCH')
        with self.assertRaises(fx().SimulationInputError) as ctx:
            fx().build_forcing_from_power(payload(units={'IMERG_PRECTOT': {'units': 'kg/m^2/s'}}), request(), retrieved_at=RETRIEVED)
        self.assertEqual(ctx.exception.code, 'SOURCE_MISMATCH')

    def test_rain_products_are_not_equivalent(self):
        f = fx()
        self.assertFalse(f.equivalent_sources(f.POWER_IMERG, f.POWER_PRECTOTCORR))
        self.assertTrue(f.equivalent_sources(f.POWER_IMERG, f.POWER_IMERG))


class CutoffTests(unittest.TestCase):
    def test_operational_mode_rejects_values_available_after_decision(self):
        f = fx(); dec = datetime(2021, 1, 20, 12, tzinfo=UTC)
        avail = lambda d: datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=2)
        b = f.build_forcing_from_power(payload(), request(mode='short_forecast', decision_at=dec), retrieved_at=RETRIEVED, available_at=avail)
        q = f.validate_forcing(b, 'short_forecast', decision_at=dec)
        self.assertFalse(q.usable)
        self.assertEqual(q.reason_code, 'SOURCE_NOT_AVAILABLE_AT_CUTOFF')
        cut = f.cut_at_decision(b, dec)
        self.assertTrue(all(r.available_at <= dec for r in cut.records))
        self.assertEqual(cut.records[-1].date, date(2021, 1, 18))

    def test_unknown_availability_is_not_operational(self):
        f = fx(); dec = datetime(2021, 3, 1, tzinfo=UTC)
        b = f.build_forcing_from_power(payload(), request(mode='short_forecast', decision_at=dec), retrieved_at=RETRIEVED)
        self.assertTrue(all(r.available_at is None for r in b.records))
        q = f.validate_forcing(b, 'short_forecast', decision_at=dec)
        self.assertFalse(q.usable)
        self.assertEqual(q.reason_code, 'SOURCE_NOT_AVAILABLE_AT_CUTOFF')
        self.assertTrue(f.validate_forcing(b, 'historical_replay').usable)  # retrospective use is still allowed
        self.assertEqual(b.manifest['availability'], 'unknown')

    def test_operational_mode_requires_decision_time(self):
        with self.assertRaises(fx().SimulationInputError):
            fx().validate_forcing(fx().build_forcing_from_power(payload(), request(), retrieved_at=RETRIEVED), 'short_forecast')


class ProvenanceTests(unittest.TestCase):
    def test_manifest_is_reproducible_and_complete(self):
        f = fx()
        a = f.build_forcing_from_power(payload(), request(), retrieved_at=RETRIEVED)
        b = f.build_forcing_from_power(payload(), request(), retrieved_at=RETRIEVED + timedelta(hours=3))
        self.assertEqual(a.manifest['content_sha256'], b.manifest['content_sha256'])  # retrieval time is not content
        for key in ('provider', 'product', 'variable', 'version', 'resolution', 'point', 'temporal_standard', 'transformation', 'retrieved_at', 'availability', 'content_sha256', 'period'):
            self.assertIn(key, a.manifest)
        c = f.build_forcing_from_power(payload(overrides={('IMERG_PRECTOT', 0): 3.5}), request(), retrieved_at=RETRIEVED)
        self.assertNotEqual(a.manifest['content_sha256'], c.manifest['content_sha256'])

    def test_cache_key_has_no_rounding_collision_and_names_the_product(self):
        f = fx()
        k = lambda **kw: f.cache_key(request(**kw))
        self.assertEqual(k(latitude=-12.70, longitude=-55.78), k(latitude=-12.70001, longitude=-55.78001))  # same declared point
        self.assertNotEqual(k(latitude=-12.70, longitude=-55.78), k(latitude=-12.7049, longitude=-55.78))
        self.assertNotEqual(k(), k(source=f.POWER_PRECTOTCORR))
        self.assertNotEqual(k(), k(end=date(2021, 2, 10)))
        self.assertEqual(f.declared_point(request(latitude=-12.70001, longitude=-55.78001)), (-12.7, -55.78))

    def test_archived_bundle_roundtrip_and_legible_errors(self):
        f = fx(); b = f.build_forcing_from_power(payload(), request(), retrieved_at=RETRIEVED)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'bundle.json'
            f.save_bundle(b, path)
            again = f.load_bundle(path)
            self.assertEqual(again.manifest['content_sha256'], b.manifest['content_sha256'])
            self.assertEqual([r.precipitation_mm for r in again.records], [r.precipitation_mm for r in b.records])
            raw = json.loads(path.read_text(encoding='utf-8')); raw['records'][0]['precipitation_mm'] = 99.0
            path.write_text(json.dumps(raw), encoding='utf-8')
            with self.assertRaises(f.SimulationInputError) as ctx:
                f.load_bundle(path)
            self.assertEqual(ctx.exception.code, 'FORCING_CORRUPT')
            with self.assertRaises(f.SimulationInputError) as ctx:
                f.load_bundle(Path(d) / 'missing.json')
            self.assertEqual(ctx.exception.code, 'FORCING_INCOMPLETE')
            self.assertIn('missing.json', ctx.exception.message)


class EngineHandoffTests(unittest.TestCase):
    def test_clean_bundle_runs_through_the_engine(self):
        from data.models.crop_simulation import engine, types
        f = fx(); start = date(2020, 10, 5)
        b = f.build_forcing_from_power(payload(first=start, days=115), request(start=start, end=start + timedelta(days=114)), retrieved_at=RETRIEVED)
        t = engine.simulate_daily([types.CropPlanItem('soy', 'gaiapus-fao56-br-v1', start)], f.to_engine_days(b),
                                  types.SoilProfile(55.0, 'AD4-hypothesis'), types.SoilState(38.5), types.ManagementSpec())
        self.assertEqual(len(t.rows), 115)
        self.assertLessEqual(t.totals['max_abs_daily_residual_mm'], 1e-8)


if __name__ == '__main__':
    unittest.main()
