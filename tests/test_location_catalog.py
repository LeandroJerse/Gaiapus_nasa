"""Location catalog of the location core (Gaiapus-APP Docs/2026-11-14-nucleo-global-lugares-design.md)."""
import unittest
from datetime import date

from data.serving import location_catalog as lc
from data.ingest.crops import REGISTRY
from data.models.water_balance import CROPS


class Ids(unittest.TestCase):
    def test_ids_keep_leading_zeros_and_reject_malformed(self):
        self.assertEqual(lc.parse_id('US:fips:01001'), ('US', 'fips', '01001'))
        self.assertEqual(lc.parse_id('BR:ibge:5107925'), ('BR', 'ibge', '5107925'))
        for bad in ('US:fips:1001', 'XX:abc:1', '5107925', 'BR:ibge:510792', 'US:FIPS:19169'):
            with self.assertRaises(ValueError, msg=bad):
                lc.parse_id(bad)

    def test_file_names_replace_colons(self):
        self.assertEqual(lc.file_stem('US:fips:01001'), 'US_fips_01001')


class Catalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.places = lc.load_catalog()
        cls.by_id = {p['id']: p for p in cls.places}

    def test_seven_places_with_census_verified_fips(self):
        self.assertEqual(len(self.places), 7)
        self.assertEqual({i for i in self.by_id if i.startswith('US:')}, {'US:fips:19169', 'US:fips:31185', 'US:fips:20191'})
        self.assertEqual(self.by_id['BR:ibge:5107925']['legacy_area_id'], '5107925')

    def test_brazil_calendars_are_the_existing_registry(self):
        for place in self.places:
            if place['country'] != 'BR':
                continue
            for crop in place['crops']:
                self.assertEqual(tuple(crop['sowing_month_day']), REGISTRY[crop['crop']]['sowing'][place['code'][:2]], (place['id'], crop['crop']))

    def test_every_parameter_set_exists(self):
        for place in self.places:
            for crop in place['crops']:
                self.assertIn(crop['parameter_set'], CROPS)

    def test_us_harvest_falls_in_the_usda_usual_window(self):
        for place in self.places:
            if place['country'] != 'US':
                continue
            for crop in place['crops']:
                sowing, harvest = lc.season_dates(crop, 2024 if crop['crop'] != 'wheat_winter' else 2023)
                lo, hi = (date(harvest.year, *crop['usual_harvest'][0]), date(harvest.year, *crop['usual_harvest'][1]))
                self.assertTrue(lo <= harvest <= hi, (place['id'], crop['crop'], harvest))

    def test_harvest_is_sowing_plus_the_cycle(self):
        crop = self.by_id['US:fips:20191']['crops'][0]
        sowing, harvest = lc.season_dates(crop, 2023)
        self.assertEqual(sowing, date(2023, 10, 2))
        self.assertEqual((harvest - sowing).days, sum(CROPS['wheat_winter_us']['phase_days']))

    def test_crop_without_calendar_reports_the_reason(self):
        self.assertEqual(lc.season_dates({'crop': 'soy', 'sowing_month_day': None, 'parameter_set': 'soy'}, 2024), (None, None))
        self.assertEqual(lc.calendar_reason({'sowing_month_day': None}), 'crop_calendar_unknown')

    def test_field_boxes_are_inside_the_official_boundary_and_mostly_cropland(self):
        for place in self.places:
            self.assertTrue(lc.inside(place['field']['bbox'], lc.boundary(place)), place['id'])
            self.assertGreaterEqual(place['field']['cropland_share'], 0.9, place['id'])

    def test_box_outside_the_boundary_is_rejected(self):
        story = self.by_id['US:fips:19169']
        self.assertFalse(lc.inside([-97.6, 37.2, -97.59, 37.21], lc.boundary(story)))


if __name__ == '__main__':
    unittest.main()
