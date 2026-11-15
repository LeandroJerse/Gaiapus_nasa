"""Season NDVI over the location-core field box (NASA HLS v2 L30/S30 on Planetary Computer).

Reuses data/ingest/field_imagery (same Fmask cloud rule and 65% minimum clear coverage as the published
recortes). Cloudy scenes keep ndvi = null; nothing is interpolated.
"""
from concurrent.futures import ThreadPoolExecutor

from data.ingest import field_imagery as fi
from data.serving.location_catalog import inside

SOURCE = 'NASA HLS v2 (L30/S30), Microsoft Planetary Computer; median NDVI of clear pixels (Fmask), >=65% clear coverage'
MIN_CLEAR_DATES = 3


def ndvi_layer(bbox, boundary, start, end):
    if not inside(bbox, boundary):
        raise ValueError('Field box outside the place boundary')
    shape = {'type': 'Polygon', 'coordinates': [fi.ring(bbox)]}
    scenes = {fi.scene_id(s): s for s in fi.catalog(bbox, start, end)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda s: fi.summarize(s, shape, bbox), scenes.values()))
    errors = sum(1 for r in records if 'error' in r)
    # Overlapping MGRS tiles deliver the same pass twice: keep one row per date and sensor (largest clear share).
    best = {}
    for r in (r for r in records if 'error' not in r):
        key = (r['date'], r['collection'])
        if key not in best or (r['valid_fraction'] or 0) > (best[key]['valid_fraction'] or 0):
            best[key] = r
    duplicates = len(records) - errors - len(best)
    records = sorted(best.values(), key=lambda r: (r['date'], r['scene_id']))
    if len({r['date'] for r in records if r['ndvi'] is not None}) < MIN_CLEAR_DATES:
        return {'status': 'unavailable', 'reason_code': 'too_few_clear_scenes', 'source': SOURCE}
    rows = [{'date': r['date'], 'ndvi': r['ndvi'], 'valid_fraction': r['valid_fraction'] or 0, 'scene_id': r['scene_id'], 'collection': r['collection']} for r in records]
    method = f'NDVI = (NIR - Red) / (NIR + Red); {duplicates} overlapping-tile duplicate(s) removed; {errors} scene(s) dropped for asset read errors'
    return {'status': 'available', 'source': SOURCE, 'method': method, 'period': {'start': start, 'end': end}, 'rows': rows}
