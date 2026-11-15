import unittest
from datetime import date


def sidra(code, year, variable, value, category='40124'):
    unit = {'216': '1006', '214': '1017', '112': '33'}[variable]
    return {'D1C': code, 'D1N': 'Somewhere - MT', 'D2C': variable, 'D3C': str(year), 'D4C': category, 'MC': unit, 'V': value}


def soy_rows(code, year, area='1000', production='3000', yield_='3000'):
    return [sidra(code, year, '216', area), sidra(code, year, '214', production), sidra(code, year, '112', yield_)]


class CohortV5Tests(unittest.TestCase):
    def test_labels_are_checked_for_unit_consistency_and_missing_values(self):
        from data.ingest.cohort_v5 import parse_labels
        payload = [{}] + soy_rows('5100001', 2010) + soy_rows('5100002', 2010, yield_='4000') + soy_rows('5100003', 2010, area='-')
        labels = {r['municipality_code']: r for r in parse_labels(payload, 'soy')}
        self.assertTrue(labels['5100001']['label_valid'])
        self.assertFalse(labels['5100002']['label_valid'])
        self.assertFalse(labels['5100003']['label_valid'])
        self.assertIsNone(labels['5100003']['area_harvested_ha'])

    def test_sealed_test_years_cannot_be_parsed(self):
        from data.ingest.cohort_v5 import parse_labels
        for year in (2022, 2023, 2024):
            with self.assertRaises(ValueError):
                parse_labels([{}] + soy_rows('5100001', year), 'soy')
        with self.assertRaises(ValueError):
            parse_labels([{}] + [sidra('5100001', 2010, '216', '10', category='999')], 'soy')

    def test_selection_ignores_validation_years(self):
        from data.ingest.cohort_v5 import select_municipalities
        def series(code, years, area):
            return [{'municipality_code': code, 'harvest_year': y, 'area_harvested_ha': area, 'label_valid': True} for y in years]
        labels = series('5100001', range(2002, 2017), 8000) + series('5100002', range(2002, 2017), 500) \
            + series('5100003', range(2012, 2022), 90000) + series('5100004', range(2007, 2017), 5000)
        self.assertEqual(select_municipalities(labels, 'soy'), ['5100001', '5100004'])

    def test_split_keeps_holdout_and_embargo_out_of_training(self):
        from data.ingest.cohort_v5 import spatial_holdout, split_of
        self.assertTrue(spatial_holdout('3170107') and spatial_holdout('5107909'))
        self.assertFalse(any(spatial_holdout(c) for c in ('3170206', '3170404', '3147006', '5107925', '5105259', '5102637')))
        share = sum(spatial_holdout(f'51{n:05d}') for n in range(1000)) / 1000
        self.assertTrue(.15 < share < .25)
        self.assertEqual([split_of(y, False) for y in (2016, 2017, 2018, 2019, 2021)],
                         ['train', 'embargo', 'embargo', 'validation', 'validation'])
        self.assertEqual(split_of(2010, True), 'unused_spatial_history')

    def test_lagged_yield_never_uses_the_previous_or_current_harvest(self):
        from data.ingest.cohort_v5 import lagged_yield
        history = {2015: 1000.0, 2016: 2000.0, 2017: 3000.0, 2019: 99999.0, 2020: 99999.0}
        self.assertEqual(lagged_yield(history, 2020), (2500.0, 2))
        self.assertEqual(lagged_yield({2019: 5.0}, 2020), (None, 0))

    def test_sowing_convention(self):
        from data.ingest.cohort_v5 import sowing_date
        self.assertEqual(sowing_date('3170206', 'soy', 2010), date(2009, 11, 5))
        self.assertEqual(sowing_date('5107925', 'maize2', 2010), date(2010, 1, 5))


if __name__ == '__main__':
    unittest.main()
