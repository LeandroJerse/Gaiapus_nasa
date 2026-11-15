"""Assembly of one LocationCore package and its manifest; validated against the Gaiapus-APP contract schema."""
import hashlib
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from data.serving import location_core as core

APP_SCHEMA = Path(os.environ.get('GAIAPUS_APP_SCHEMA', Path.home() / 'Documents/Gaiapus-APP/packages/contracts/schemas/application.json'))
PLACE = {
    'id': 'US:fips:19169', 'country': 'US', 'scheme': 'fips', 'code': '19169', 'name': 'Story County', 'admin1_code': 'US-IA', 'admin1_name': 'Iowa', 'legacy_area_id': None,
    'field': {'bbox': [-93.36448, 42.12328, -93.35397, 42.13099], 'cropland_share': 0.997, 'cropland_source': 'usda-cdl 2021'},
    'crops': [{'crop': 'soy', 'parameter_set': 'soy_us', 'sowing_month_day': [5, 20], 'calendar_source': 'usda_handbook_628_2010'}],
}
BOUNDARY = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon', 'coordinates': [[[-93.7, 41.86], [-93.2, 41.86], [-93.2, 42.21], [-93.7, 42.21], [-93.7, 41.86]]]}}]}
SOURCES = {'usda_handbook_628_2010': {'title': 'USDA NASS Agricultural Handbook 628 (2010)', 'url': 'https://example.test/628'}}


def power(lon, lat, first, last):
    days = (last - first).days + 1
    keys = [(first + timedelta(days=i)).strftime('%Y%m%d') for i in range(days)]
    base = {'IMERG_PRECTOT': 2.0, 'T2M_MIN': 15.0, 'T2M_MAX': 28.0, 'ALLSKY_SFC_SW_DWN': 20.0, 'RH2M': 70.0, 'WS2M': 3.0, 'T2MDEW': 12.0, 'PS': 97.0}
    return {'geometry': {'coordinates': [lon, lat, 300.0]}, 'properties': {'parameter': {p: {k: v for k in keys} for p, v in base.items()}}}


def fetchers():
    return {
        'power': power,
        'boundary': lambda place: BOUNDARY,
        'ndvi': lambda bbox, boundary, start, end: {'status': 'unavailable', 'reason_code': 'too_few_clear_scenes', 'source': 'NASA HLS v2'},
        'forecast': lambda lon, lat, past_days: None,
        'generator': 'gaiapus_nasa@test',
    }


def lagging_power(lag_days):
    def fetch(lon, lat, first, last):
        payload = power(lon, lat, first, last)
        cut = (last - timedelta(days=lag_days)).strftime('%Y%m%d')
        for name in ('IMERG_PRECTOT', 'ALLSKY_SFC_SW_DWN'):
            for key in payload['properties']['parameter'][name]:
                if key > cut:
                    payload['properties']['parameter'][name][key] = -999.0
        return payload
    return fetch


def open_meteo(lon, lat, past_days):
    start = date(2026, 11, 15) - timedelta(days=past_days)
    return {(start + timedelta(days=i)).isoformat(): {'rain_mm': 1.0, 'et0_mm': 5.0} for i in range(past_days + 11)}


class ForecastBridge(unittest.TestCase):
    def test_power_lag_is_bridged_with_open_meteo_and_the_forecast_starts_at_issue(self):
        place = {**PLACE, 'id': 'BR:ibge:5107925', 'country': 'BR', 'scheme': 'ibge', 'code': '5107925', 'admin1_code': 'BR-MT', 'admin1_name': 'Mato Grosso', 'legacy_area_id': '5107925',
                 'crops': [{'crop': 'soy', 'parameter_set': 'soy', 'sowing_month_day': [10, 5], 'calendar_source': 'usda_handbook_628_2010'}]}
        f = {**fetchers(), 'power': lagging_power(60), 'forecast': open_meteo}
        pkg = core.build_core(place, SOURCES, datetime(2026, 11, 15, 12, tzinfo=timezone.utc), f, period=(date(2023, 1, 1), date(2024, 12, 31)))
        fc = pkg['layers']['forecast']
        self.assertEqual(fc['status'], 'available')
        self.assertEqual((fc['rows'][0]['date'], len(fc['rows'])), ('2026-11-15', 10))
        self.assertIn('Open-Meteo', fc['method'])
        self.assertIn('from 2026-10-05 to 2026-11-14', fc['method'])  # sowing to the day before issue, all model days

    def test_provenance_names_only_the_sources_used_after_sowing(self):
        place = {**PLACE, 'crops': [{'crop': 'soy', 'parameter_set': 'soy', 'sowing_month_day': [10, 5], 'calendar_source': 'usda_handbook_628_2010'}]}
        f = {**fetchers(), 'power': lagging_power(60), 'forecast': open_meteo}
        pkg = core.build_core(place, SOURCES, datetime(2026, 11, 15, 12, tzinfo=timezone.utc), f, period=(date(2023, 1, 1), date(2024, 12, 31)))
        method = pkg['layers']['forecast']['method']
        self.assertNotIn('NASA POWER', method)  # sowing (Oct 5) is after the last complete POWER day (Sep 15)
        self.assertIn('model output, not observations', method)
        self.assertFalse(any('spin-up' in i['dataset'] and i['source'] == 'NASA POWER' for i in pkg['provenance']['inputs']))
        f = {**fetchers(), 'power': lagging_power(20), 'forecast': open_meteo}
        pkg = core.build_core(place, SOURCES, datetime(2026, 11, 15, 12, tzinfo=timezone.utc), f, period=(date(2023, 1, 1), date(2024, 12, 31)))
        self.assertIn('NASA POWER from 2026-10-05 to 2026-10-25', pkg['layers']['forecast']['method'])

    def test_open_meteo_payload_without_hourly_is_source_unavailable(self):
        from data.serving import location_forecast as lf
        from unittest import mock
        class R:
            def raise_for_status(self): pass
            def json(self): return {'error': True, 'reason': 'x'}
        with mock.patch.object(lf.requests, 'get', return_value=R()):
            self.assertIsNone(lf.fetch_forecast(-55.9, -12.5))

    def test_a_gap_longer_than_open_meteo_history_is_unavailable(self):
        place = {**PLACE, 'crops': [{'crop': 'soy', 'parameter_set': 'soy', 'sowing_month_day': [10, 5], 'calendar_source': 'usda_handbook_628_2010'}]}
        f = {**fetchers(), 'power': lagging_power(120), 'forecast': open_meteo}
        pkg = core.build_core(place, SOURCES, datetime(2026, 11, 15, 12, tzinfo=timezone.utc), f, period=(date(2023, 1, 1), date(2024, 12, 31)))
        self.assertEqual(pkg['layers']['forecast']['reason_code'], 'source_unavailable')


class Assemble(unittest.TestCase):
    def setUp(self):
        self.issued = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        self.pkg = core.build_core(PLACE, SOURCES, self.issued, fetchers(), period=(date(2023, 1, 1), date(2024, 12, 31)))

    def test_place_and_field(self):
        self.assertEqual(self.pkg['place']['id'], 'US:fips:19169')
        self.assertEqual(self.pkg['place']['boundary_url'], '/publications/location-core-v1/US_fips_19169.geojson')
        self.assertTrue(self.pkg['field']['inside_boundary'])
        self.assertAlmostEqual(self.pkg['field']['area_ha'], 74, delta=8)
        self.assertIn('usda-cdl 2021', self.pkg['field']['rationale'])

    def test_rationale_says_the_crop_in_the_box_is_not_verified(self):
        self.assertIn('99.7%', self.pkg['field']['rationale'])
        self.assertIn('crop grown in the box each season is not verified', self.pkg['field']['rationale'])

    def test_ndvi_window_comes_from_the_calendar_even_without_weather(self):
        seen = {}
        f = {**fetchers(), 'power': lambda *a: {'geometry': {'coordinates': [0, 0, 0]}, 'properties': {'parameter': {p: {} for p in ('IMERG_PRECTOT', 'T2M_MIN', 'T2M_MAX', 'ALLSKY_SFC_SW_DWN', 'WS2M', 'T2MDEW', 'PS')}}},
             'ndvi': lambda bbox, boundary, start, end: seen.update(start=start, end=end) or {'status': 'unavailable', 'reason_code': 'too_few_clear_scenes', 'source': 'x'}}
        wheat = {**PLACE, 'crops': [{'crop': 'wheat_winter', 'parameter_set': 'wheat_winter_us', 'sowing_month_day': [10, 2], 'calendar_source': 'usda_handbook_628_2010'}]}
        core.build_core(wheat, SOURCES, self.issued, f, period=(date(2023, 1, 1), date(2024, 12, 31)))
        self.assertEqual((seen['start'], seen['end']), ('2023-10-02', '2024-06-18'))

    def test_layers(self):
        layers = self.pkg['layers']
        self.assertEqual(layers['weather']['status'], 'available')
        self.assertEqual([s['season'] for s in layers['water_balance']['seasons']], ['2023', '2024'])
        self.assertEqual(layers['forecast']['reason_code'], 'out_of_season')
        self.assertEqual(self.pkg['crops'][0]['calendar_source'], 'USDA NASS Agricultural Handbook 628 (2010) — https://example.test/628')
        self.assertEqual(self.pkg['crops'][0]['season'], '2024')

    @unittest.skipUnless(APP_SCHEMA.exists(), 'requires the Gaiapus-APP contract schema')
    def test_validates_against_the_app_contract(self):
        import jsonschema
        schema = json.loads(APP_SCHEMA.read_text(encoding='utf-8'))
        validator = jsonschema.Draft202012Validator({'$ref': '#/$defs/LocationCore', '$defs': schema['$defs']}, format_checker=jsonschema.FormatChecker())
        errors = sorted(validator.iter_errors(self.pkg), key=lambda e: list(e.path))
        self.assertEqual([f'{list(e.path)}: {e.message[:120]}' for e in errors], [])

    def test_manifest_hashes_match_the_written_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = core.write_packages([self.pkg], Path(tmp), 'gaiapus_nasa@test', self.issued)
            [entry] = manifest['files']
            body = (Path(tmp) / entry['path']).read_bytes()
            self.assertEqual(entry['sha256'], hashlib.sha256(body).hexdigest())
            self.assertEqual(entry['id'], 'US:fips:19169')
            self.assertEqual(json.loads((Path(tmp) / 'manifest.json').read_text(encoding='utf-8')), manifest)


if __name__ == '__main__':
    unittest.main()
