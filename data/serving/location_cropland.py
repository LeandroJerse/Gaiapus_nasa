"""Cropland share around a place, used only to choose the location-core field boxes.

US: USDA Cropland Data Layer (`usda-cdl`, asset `cropland`: 1 = cropland). Elsewhere: ESA WorldCover
(class 40 = cropland). Both read from Microsoft Planetary Computer. The layer itself is never published;
only the share and its source go into the catalog rationale.
"""
import time
from urllib.parse import urlparse

import numpy as np
import requests

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
TOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token'
SOURCES = {'US': ('usda-cdl', 'cropland', 2021, lambda a: a == 1), 'default': ('esa-worldcover', 'map', 2021, lambda a: a == 40)}
_tokens = {}


def _get(url, **kwargs):
    for attempt in range(6):
        r = requests.get(url, timeout=120, **kwargs)
        if r.status_code != 429:
            r.raise_for_status(); return r
        time.sleep(int(r.headers.get('Retry-After', 10)) + attempt)
    r.raise_for_status()


def signed(href):
    """Append a container SAS token (cached per storage account/container)."""
    parts = urlparse(href); account = parts.netloc.split('.')[0]; container = parts.path.split('/')[1]
    if (account, container) not in _tokens:
        _tokens[account, container] = _get(f'{TOKEN}/{account}/{container}').json()['token']
    return f'{href}?{_tokens[account, container]}'


def _item(collection, year, bbox):
    body = {'collections': [collection], 'bbox': list(bbox), 'datetime': f'{year}-01-01/{year}-12-31', 'limit': 5}
    if collection == 'usda-cdl':
        body['query'] = {'usda_cdl:type': {'eq': 'cropland'}}
    items = requests.post(f'{STAC}/search', json=body, timeout=120).json()['features']
    if not items:
        raise LookupError(f'No {collection} item for {bbox} in {year}')
    return items[0]


def _read(bbox, country):
    """Cropland and valid masks over `bbox`, plus the source CRS transform of the window."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds, transform as window_transform
    collection, asset, year, is_crop = SOURCES.get(country, SOURCES['default'])
    item = _item(collection, year, bbox)
    with rasterio.open(signed(item['assets'][asset]['href'])) as src:
        w, s, e, n = transform_bounds('EPSG:4326', src.crs, *bbox)
        window = from_bounds(w, s, e, n, src.transform)
        data = src.read(1, window=window)
        valid = data != (src.nodata if src.nodata is not None else 0)
        geo = (window_transform(window, src.transform), src.crs, abs(src.res[0]))
    return is_crop(data) & valid, valid, f'{collection} {year}', geo


def cropland_share(bbox, country):
    """Fraction of valid pixels classified as cropland inside `bbox` (lon/lat WGS84)."""
    crop, valid, source, _ = _read(bbox, country)
    if not valid.any():
        raise ValueError('Empty window')
    return float(crop.sum() / valid.sum()), source


def best_box(search_bbox, country, size_m=850, step_m=200, boundary=None):
    """Square box of about `size_m` with the largest cropland share inside `search_bbox` (lon/lat WGS84).

    The scan runs on the source grid (CDL is EPSG:5070, WorldCover is EPSG:4326); the chosen window is
    converted back to lon/lat with the source CRS, never by linear pixel arithmetic. With `boundary`
    (GeoJSON FeatureCollection in lon/lat), only windows entirely inside it are candidates.
    """
    from rasterio.features import rasterize
    from rasterio.warp import transform_bounds, transform_geom
    crop, valid, source, (affine, crs, res) = _read(search_bbox, country)
    if boundary is not None:
        shapes = [(transform_geom('EPSG:4326', crs, f['geometry']), 1) for f in boundary['features']]
        valid = valid & rasterize(shapes, out_shape=valid.shape, transform=affine, fill=0).astype(bool)
    if crs.is_geographic:
        res = res * 111_320  # degrees to metres at the equator; only sets the window size in pixels
    k, st = max(1, round(size_m / res)), max(1, round(step_m / res))
    rows, cols = crop.shape
    best = (-1.0, None)
    for r0 in range(0, rows - k + 1, st):
        for c0 in range(0, cols - k + 1, st):
            v = valid[r0:r0 + k, c0:c0 + k].sum()
            if v < k * k if boundary is not None else v < 0.9 * k * k:
                continue
            share = crop[r0:r0 + k, c0:c0 + k].sum() / v
            if share > best[0]:
                best = (float(share), (r0, c0))
    if best[1] is None:
        raise ValueError('No valid window in the search area')
    r0, c0 = best[1]
    x0, y0 = affine * (c0, r0 + k); x1, y1 = affine * (c0 + k, r0)
    w, s, e, n = transform_bounds(crs, 'EPSG:4326', min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return tuple(round(v, 5) for v in (w, s, e, n)), best[0], source
