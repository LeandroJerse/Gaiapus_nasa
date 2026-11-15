import unittest

LOCATIONS = [{'municipality_code': 3170206, 'longitude': -48.2772, 'latitude': -18.9186},
             {'municipality_code': 5107909, 'longitude': -55.5025, 'latitude': -11.8642}]
RECORD = {'run': 'Late', 'product': 'GPM_3IMERGDL', 'filename': '3B-DAY-L.MS.MRG.3IMERG.20210102-S000000-E235959.V07B.nc4',
          'observation_start': '2021-01-02T00:00:00.000Z', 'observation_end': '2021-01-02T23:59:59.999Z',
          'cmr_updated_at': '2024-06-11T00:00:00.000Z',
          'download_url': 'https://data.gesdisc.earthdata.nasa.gov/data/GPM_L3/GPM_3IMERGDL.07/2021/01/'
                          '3B-DAY-L.MS.MRG.3IMERG.20210102-S000000-E235959.V07B.nc4'}
RECEIPT = {'download_completed_at': '2026-11-14T12:00:00+00:00', 'sha256': 'a' * 64, 'path': 'x/y.subset.nc4'}


def payload(box, title='GPM IMERG Late (GPM_3IMERGDL)', day='2021-01-02', units='mm/day', masked=None, shift=0.0):
    import netCDF4
    import numpy as np
    i0, i1, j0, j1 = box
    ds = netCDF4.Dataset('synthetic', 'w', memory=4096)
    ds.title, ds.BeginDate, ds.EndDate, ds.ProductionTime = title, day, day, '2024-06-11T14:29:38.862Z'
    ds.createDimension('time', 1); ds.createDimension('lon', i1 - i0 + 1); ds.createDimension('lat', j1 - j0 + 1)
    time = ds.createVariable('time', 'i4', ('time',)); time.units = 'days since 1980-01-06 00:00:00Z'
    time[0] = (np.datetime64(day) - np.datetime64('1980-01-06')).astype(int)
    lon = ds.createVariable('lon', 'f4', ('lon',)); lon[:] = -179.95 + 0.1 * np.arange(i0, i1 + 1) + shift
    lat = ds.createVariable('lat', 'f8', ('lat',)); lat[:] = -89.95 + 0.1 * np.arange(j0, j1 + 1)
    rain = ds.createVariable('precipitation', 'f4', ('time', 'lon', 'lat'), fill_value=-9999.9); rain.units = units
    count = ds.createVariable('precipitation_cnt', 'i1', ('time', 'lon', 'lat'))
    grid = np.fromfunction(lambda i, j: (i0 + i) * 0.001 + (j0 + j) * 0.01, (i1 - i0 + 1, j1 - j0 + 1))
    rain[0] = grid; count[0] = 48
    if masked:
        rain[0, masked[0] - i0, masked[1] - j0] = np.ma.masked
        count[0, masked[0] - i0, masked[1] - j0] = 20
    return bytes(ds.close())


class ImergSubsetTests(unittest.TestCase):
    def test_indices_follow_the_regular_grid_and_box_covers_all_points(self):
        from data.ingest.imerg_subset import cell_box
        cells, box = cell_box(LOCATIONS)
        self.assertEqual(cells, [(1317, 710), (1244, 781)])
        self.assertEqual(box, (1244, 1317, 710, 781))

    def test_opendap_url_is_built_only_from_trusted_catalog_urls(self):
        from data.ingest.imerg_subset import opendap_url
        url = opendap_url(RECORD, (1244, 1316, 710, 780))
        self.assertTrue(url.startswith('https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDL.07/2021/01/3B-DAY-L'))
        self.assertIn('.nc4.nc4?precipitation[0:0][1244:1316][710:780],precipitation_cnt[0:0][1244:1316][710:780]', url)
        for bad in ('http://data.gesdisc.earthdata.nasa.gov/data/GPM_L3/x.nc4', 'https://evil.example/data/GPM_L3/x.nc4',
                    'https://user:pw@data.gesdisc.earthdata.nasa.gov/data/GPM_L3/x.nc4',
                    'https://data.gesdisc.earthdata.nasa.gov/other/x.nc4'):
            with self.assertRaises(ValueError):
                opendap_url({**RECORD, 'download_url': bad}, (0, 1, 0, 1))

    def test_extraction_reads_the_cell_of_each_municipality(self):
        from data.ingest.imerg_subset import cell_box, extract_subset
        cells, box = cell_box(LOCATIONS)
        rows = extract_subset(payload(box), RECORD, LOCATIONS, RECEIPT)
        self.assertEqual([r['municipality_code'] for r in rows], [3170206, 5107909])
        for row, (i, j) in zip(rows, cells):
            self.assertAlmostEqual(row['rain_mm'], i * 0.001 + j * 0.01, places=4)
            self.assertAlmostEqual(row['cell_lon'], -179.95 + 0.1 * i, places=4)
            self.assertTrue(row['complete_48_half_hours'])
            self.assertEqual((row['day_utc'], row['units'], row['run']), ('2021-01-02', 'mm/day', 'Late'))
            self.assertEqual(row['first_seen_at'], RECEIPT['download_completed_at'])
            self.assertIsNone(row['provider_first_published_at'])

    def test_masked_rain_stays_missing_and_incomplete(self):
        from data.ingest.imerg_subset import cell_box, extract_subset
        cells, box = cell_box(LOCATIONS)
        rows = extract_subset(payload(box, masked=cells[0]), RECORD, LOCATIONS, RECEIPT)
        self.assertIsNone(rows[0]['rain_mm'])
        self.assertFalse(rows[0]['complete_48_half_hours'])
        self.assertIsNotNone(rows[1]['rain_mm'])

    def test_wrong_product_day_unit_or_grid_is_rejected(self):
        from data.ingest.imerg_subset import cell_box, extract_subset
        _, box = cell_box(LOCATIONS)
        for kwargs in ({'title': 'GPM IMERG Early (GPM_3IMERGDE)'}, {'day': '2021-01-03'}, {'units': 'mm/hr'}, {'shift': 0.1}):
            with self.assertRaises(ValueError):
                extract_subset(payload(box, **kwargs), RECORD, LOCATIONS, RECEIPT)

    def test_pairs_need_both_products_complete_in_the_same_cell(self):
        from data.ingest.imerg_subset import pair_rows
        def row(run, rain, complete=True, lon=-48.25):
            return {'municipality_code': '3170206', 'day_utc': '2021-01-02', 'run': run, 'rain_mm': rain,
                    'complete_48_half_hours': complete, 'cell_lon': lon, 'cell_lat': -18.95}
        pairs = pair_rows([row('Early', 3.0), row('Late', None, False), row('Final', 2.0)])
        early = next(p for p in pairs if p['run'] == 'Early')
        late = next(p for p in pairs if p['run'] == 'Late')
        self.assertTrue(early['matched_complete'])
        self.assertEqual((early['difference_mm'], early['final_mm']), (1.0, 2.0))
        self.assertFalse(late['matched_complete'])
        self.assertIsNone(late['rain_mm'])
        with self.assertRaises(ValueError):
            pair_rows([row('Early', 3.0, lon=-48.35), row('Final', 2.0)])


if __name__ == '__main__':
    unittest.main()
