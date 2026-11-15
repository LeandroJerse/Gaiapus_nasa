"""Season NDVI of the location-core field box (HLS v2, same cloud rules as the published recortes)."""
import unittest
from unittest import mock

import numpy as np
from rasterio.transform import from_bounds

from data.ingest import field_imagery as fi
from data.serving import location_ndvi as ln

BOX = (-93.36448, 42.12328, -93.35397, 42.13099)
BOUNDARY = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon', 'coordinates': [[[-93.7, 41.86], [-93.2, 41.86], [-93.2, 42.21], [-93.7, 42.21], [-93.7, 41.86]]]}}]}


def scene(i, cloudy=False, collection='hls2-s30'):
    return {'id': f'HLS.S30.T15TVG.2024{100 + i:03d}', 'collection': collection, 'cloudy': cloudy,
            'properties': {'datetime': f'2024-06-{i + 1:02d}T17:00:00Z'},
            'assets': {k: {'href': 'https://hls2.blob.core.windows.net/x/y.tif'} for k in ('B04', 'B8A', 'B05', 'Fmask')}}


def fake_band(scene_record, key, bounds):
    """Red 500, NIR 3500 (NDVI 0.75); Fmask cloud bit set on cloudy scenes."""
    transform = from_bounds(*bounds, 10, 10)
    value = {'B04': 500, 'B8A': 3500, 'B05': 3500, 'Fmask': 2 if scene_record['cloudy'] else 0}[key]
    return np.ma.masked_array(np.full((10, 10), value, dtype=np.int16)), transform, 'EPSG:4326'


class Ndvi(unittest.TestCase):
    def run_with(self, scenes):
        with mock.patch.object(fi, 'catalog', lambda bounds=None, start=None, end=None: iter(scenes)), mock.patch.object(fi, 'band', fake_band):
            return ln.ndvi_layer(BOX, BOUNDARY, '2024-05-01', '2024-10-31')

    def test_clear_scenes_give_median_ndvi(self):
        layer = self.run_with([scene(i) for i in range(4)])
        self.assertEqual(layer['status'], 'available')
        self.assertEqual([r['ndvi'] for r in layer['rows']], [0.75] * 4)
        self.assertEqual(set(layer['rows'][0]), {'date', 'ndvi', 'valid_fraction', 'scene_id', 'collection'})
        self.assertEqual(layer['period'], {'start': '2024-05-01', 'end': '2024-10-31'})

    def test_cloudy_scenes_stay_null_never_interpolated(self):
        layer = self.run_with([scene(0), scene(1, cloudy=True), scene(2), scene(3)])
        self.assertIsNone(layer['rows'][1]['ndvi'])
        self.assertEqual(layer['rows'][1]['valid_fraction'], 0)

    def test_overlapping_tiles_keep_one_row_per_date_and_sensor(self):
        twin = scene(0); twin['id'] = 'HLS.S30.T15TVH.2024100'
        layer = self.run_with([scene(0), twin, scene(1), scene(2)])
        self.assertEqual([r['date'] for r in layer['rows']], ['2024-06-01', '2024-06-02', '2024-06-03'])
        self.assertIn('1 overlapping-tile duplicate', layer['method'])

    def test_fewer_than_three_clear_dates_is_unavailable(self):
        layer = self.run_with([scene(0), scene(1), scene(2, cloudy=True)])
        self.assertEqual((layer['status'], layer['reason_code']), ('unavailable', 'too_few_clear_scenes'))

    def test_box_outside_the_boundary_raises(self):
        with self.assertRaises(ValueError):
            ln.ndvi_layer((-97.6, 37.2, -97.59, 37.21), BOUNDARY, '2024-05-01', '2024-10-31')

    def test_published_recorte_defaults_are_unchanged(self):
        self.assertEqual(fi.FIELD_BOUNDS, (-48.413, -18.907, -48.405, -18.900))
        self.assertEqual((fi.START, fi.END, fi.MIN_COVERAGE), ('2024-04-01', '2024-07-31', 0.65))


if __name__ == '__main__':
    unittest.main()
