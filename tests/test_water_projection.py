import unittest


class WaterProjectionTests(unittest.TestCase):
    def test_partial_day_is_missing_not_a_partial_sum(self):
        from data.ingest.water_projection import daily_sums
        times = [f'2025-01-01T{h:02d}:00' for h in range(24)] + [f'2025-01-02T{h:02d}:00' for h in range(24)] + ['2025-01-03T00:00']
        values = [1.0] * 24 + [1.0] * 23 + [None] + [5.0]
        self.assertEqual(daily_sums(times, values), {'2025-01-01': 24.0, '2025-01-02': None, '2025-01-03': None})

    def test_advance_matches_the_physical_simulator(self):
        from data.ingest.water_projection import advance
        from data.models.water_balance import CROPS, simulate
        from datetime import date, timedelta
        start = date(2025, 1, 5)
        weather = [{'day_utc': (start + timedelta(days=i)).isoformat(), 'rain_mm': 12. if i % 6 == 0 else 0., 'et0_mm': 5.}
                   for i in range(sum(CROPS['maize2']['phase_days']))]
        full = simulate(weather, 'maize2', CROPS['maize2']['cad_mm']['AD4'], .7)
        rows = advance(.7 * CROPS['maize2']['cad_mm']['AD4'], 'maize2', 0, weather)
        for a, b in zip(rows, full):
            self.assertAlmostEqual(a['storage_end_mm'], b['storage_end_mm'])
            self.assertAlmostEqual(a['et_deficit_mm'], b['et_deficit_mm'])
        # Restarting from a stored state reproduces the same path: the projection can start on any crop day.
        resumed = advance(full[39]['storage_end_mm'], 'maize2', 40, weather[40:50])
        self.assertAlmostEqual(resumed[-1]['storage_end_mm'], full[49]['storage_end_mm'])

    def test_dry_forecast_lowers_storage_and_never_exceeds_capacity(self):
        from data.ingest.water_projection import advance
        from data.models.water_balance import CROPS
        capacity = CROPS['soy']['cad_mm']['AD4']
        dry = advance(capacity, 'soy', 50, [{'rain_mm': 0., 'et0_mm': 6.}] * 10)
        wet = advance(capacity, 'soy', 50, [{'rain_mm': 30., 'et0_mm': 6.}] * 10)
        self.assertLess(dry[-1]['storage_end_mm'], wet[-1]['storage_end_mm'])
        self.assertTrue(all(0 <= r['storage_end_mm'] <= capacity and 0 <= r['available_water_fraction'] <= 1 for r in dry + wet))
        self.assertTrue(dry[-1]['stress'])

    def test_supported_lead_stops_at_the_first_failure(self):
        from data.ingest.water_projection import supported_lead
        row = lambda lead, error=True, beats=True, coverage=True: {'lead_days': lead, 'meets_error': error, 'beats_reference': beats, 'meets_coverage': coverage}
        self.assertEqual(supported_lead([row(1), row(2), row(3, beats=False), row(4)]), 2)
        self.assertEqual(supported_lead([row(1, coverage=False), row(2)]), 0)
        self.assertEqual(supported_lead([row(2), row(1), row(3)]), 3)


if __name__ == '__main__':
    unittest.main()
