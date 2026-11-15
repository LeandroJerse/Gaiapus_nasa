"""US v2 experiment E2b: state CDL rasters (CropScape) -> county x crop x mask-year weights on the gridMET grid.

Download and weights need rasterio/pyproj/shapely/pyshp, run ONLY in the separate raster venv
(C:/Users/leand/.venvs/gaiapus-raster), e.g. from the repo root:
    <venv>/Scripts/python.exe -m data.ingest.us_v2_cdl download
    <venv>/Scripts/python.exe -m data.ingest.us_v2_cdl weights
The main interpreter only imports the pure helpers (weights_from_counts, CROP_CODES) and reads the CSV.

Method: county polygons (Census cartographic boundary, NAD83 lon/lat) are reprojected to the raster CRS
(CDL Albers, EPSG:5070) and rasterized on the CDL grid by pixel-centre rule. Each crop pixel of the county
(primary CDL class only) is assigned to a gridMET cell by transforming its pixel centre from the raster CRS
to EPSG:4326 and taking the 1/24 degree cell that contains it. weight = crop pixels in cell / crop pixels
in county. NAD83 vs WGS84 (< ~2 m) is ignored relative to the ~4 km cell size.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/us_training_v2/cdl'
OUT = ROOT / 'data/processed/us_training_v2'
CROPSCAPE = 'https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile'
CENSUS_URL = 'https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip'
LEGEND_URL = 'https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_CDL24_FGDC-STD-001-1998.htm'
# Primary CDL classes, verified in the accuracy table of LEGEND_URL (Corn 1, Soybeans 5, Winter Wheat 24).
CROP_CODES = {'maize': 1, 'soy': 5, 'wheat_winter': 24}
STATE_FIPS = {'IA': '19', 'NE': '31', 'KS': '20'}
MASK_YEARS = (2006, 2009, 2012, 2015, 2018, 2021, 2024)
BLOCK_ROWS = 1024


def weights_from_counts(counts: pd.DataFrame) -> pd.DataFrame:
    """crop_pixels per cell -> weight = share of the county x crop x mask-year crop pixels; zero groups dropped."""
    c = counts[counts.crop_pixels > 0].copy()
    total = c.groupby(['fips', 'crop', 'mask_year']).crop_pixels.transform('sum')
    c['weight'] = c.crop_pixels / total
    return c[['fips', 'crop', 'mask_year', 'cell_lat', 'cell_lon', 'weight', 'crop_pixels']].reset_index(drop=True)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.partial')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def _download(url: str, path: Path, session) -> dict:
    """Streamed download with TLS verification (requests default verify=True)."""
    tmp = path.with_suffix(path.suffix + '.partial')
    for attempt in range(5):
        try:
            with session.get(url, stream=True, timeout=(30, 900), verify=True) as r:
                r.raise_for_status()
                h, n = hashlib.sha256(), 0
                with tmp.open('wb') as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
                expected = r.headers.get('Content-Length')
                if expected is not None and int(expected) != n:
                    raise OSError('truncated download')
                tmp.replace(path)
                return {'bytes': n, 'sha256': h.hexdigest(), 'last_modified': r.headers.get('Last-Modified'), 'content_type': r.headers.get('Content-Type')}
        except Exception as exc:  # noqa: BLE001 - retried, last error re-raised
            err = exc
            time.sleep(10 * (attempt + 1))
    raise err


def download() -> None:
    import rasterio
    import requests
    session = requests.Session()
    total = 0
    path = RAW / 'cb_2024_us_county_500k.zip'
    meta = path.with_suffix('.manifest.json')
    if not (path.exists() and meta.exists() and json.loads(meta.read_text())['sha256'] == _sha(path)):
        RAW.mkdir(parents=True, exist_ok=True)
        info = _download(CENSUS_URL, path, session)
        total += info['bytes']
        _save_json(meta, {'url': CENSUS_URL, 'retrieved_at': datetime.now(timezone.utc).isoformat(), **info})
    for year in MASK_YEARS:
        for state, fips in STATE_FIPS.items():
            path = RAW / f'CDL_{year}_{fips}.tif'
            meta = path.with_suffix('.manifest.json')
            if path.exists() and meta.exists() and json.loads(meta.read_text())['sha256'] == _sha(path):
                continue
            r = session.get(CROPSCAPE, params={'year': year, 'fips': fips}, timeout=(30, 600), verify=True)
            r.raise_for_status()
            found = re.search(r'<returnURL>(.*?)</returnURL>', r.text)
            if not found:
                raise SystemExit(f'CropScape state request failed for {year} {state}: {r.text[:300]}')
            url = found.group(1)
            if not url.startswith('https://nassgeodata.gmu.edu/'):
                raise SystemExit(f'unexpected returnURL {url}')
            info = _download(url, path, session)
            total += info['bytes']
            with rasterio.open(path) as src:
                raster = {'crs': src.crs.to_string(), 'epsg': src.crs.to_epsg(), 'res_m': list(src.res), 'width': src.width, 'height': src.height,
                          'dtype': src.dtypes[0], 'nodata': src.nodata, 'bounds': list(src.bounds)}
            _save_json(meta, {'service': CROPSCAPE, 'request': {'year': year, 'fips': fips}, 'service_response': r.text.strip(), 'url': url,
                              'retrieved_at': datetime.now(timezone.utc).isoformat(), 'raster': raster, **info})
            print(json.dumps({'year': year, 'state': state, 'bytes': info['bytes'], 'res_m': raster['res_m'], 'total_bytes_this_run': total}), flush=True)
    print(json.dumps({'done': True, 'total_bytes_this_run': total}), flush=True)


def _county_shapes(state_fips: str):
    import shapefile
    from shapely.geometry import shape
    path = RAW / 'cb_2024_us_county_500k.zip'
    with zipfile.ZipFile(path) as z:
        prj = z.read('cb_2024_us_county_500k.prj').decode()
    if 'North_American_1983' not in prj or 'PROJCS' in prj:
        raise ValueError(f'unexpected county CRS {prj[:120]}')
    reader = shapefile.Reader(str(path))
    fields = [f[0] for f in reader.fields[1:]]
    out = []
    for rec, shp in zip(reader.records(), reader.shapes()):
        row = dict(zip(fields, rec))
        if row['STATEFP'] == state_fips:
            out.append((row['GEOID'], shape(shp.__geo_interface__)))
    reader.close()
    return sorted(out), prj


def raster_counts(tif: Path, state: str, mask_year: int) -> tuple[pd.DataFrame, dict]:
    import rasterio
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.windows import Window
    from shapely.ops import transform as shp_transform

    from data.ingest import us_v2_gridmet as g
    from data.ingest import us_v2_weather as wx
    crops = wx.CROPS_BY_STATE[state]
    codes = np.array([CROP_CODES[c] for c in crops])
    shapes, prj = _county_shapes(STATE_FIPS[state])
    acc: dict[int, int] = {}
    county_pixels = np.zeros(len(shapes) + 1, dtype=np.int64)
    with rasterio.open(tif) as src:
        to_raster = Transformer.from_crs('EPSG:4269', src.crs, always_xy=True)
        to_geo = Transformer.from_crs(src.crs, 'EPSG:4326', always_xy=True)
        geoms = [(shp_transform(to_raster.transform, geom), k + 1) for k, (_, geom) in enumerate(shapes)]
        for r0 in range(0, src.height, BLOCK_ROWS):
            win = Window(0, r0, src.width, min(BLOCK_ROWS, src.height - r0))
            vals = src.read(1, window=win)
            wt = src.window_transform(win)
            county = rasterize(geoms, out_shape=vals.shape, transform=wt, fill=0, dtype='int32', all_touched=False)
            county_pixels += np.bincount(county.ravel(), minlength=len(county_pixels))
            crop_idx = np.full(vals.shape, -1, dtype=np.int8)
            for k, code in enumerate(codes):
                crop_idx[vals == code] = k
            rr, cc = np.nonzero((crop_idx >= 0) & (county > 0))
            if rr.size == 0:
                continue
            x, y = wt * (cc + 0.5, rr + 0.5)
            lon, lat = to_geo.transform(np.asarray(x), np.asarray(y))
            grow = np.floor((g.LAT0 - lat) / g.RES + 0.5).astype(np.int64)
            gcol = np.floor((lon - g.LON0) / g.RES + 0.5).astype(np.int64)
            key = ((county[rr, cc].astype(np.int64) * 4 + crop_idx[rr, cc]) * 2000 + grow) * 2000 + gcol
            uk, n = np.unique(key, return_counts=True)
            for kk, nn in zip(uk.tolist(), n.tolist()):
                acc[kk] = acc.get(kk, 0) + nn
        info = {'crs': src.crs.to_string(), 'res_m': list(src.res), 'shape': [src.height, src.width]}
    keys = np.array(list(acc), dtype=np.int64)
    gcol, rest = keys % 2000, keys // 2000
    grow, rest = rest % 2000, rest // 2000
    cidx, county_id = rest % 4, rest // 4
    df = pd.DataFrame({'fips': [shapes[i - 1][0] for i in county_id], 'crop': [crops[i] for i in cidx], 'mask_year': mask_year,
                       'cell_lat': np.round(g.LAT0 - grow * g.RES, 8), 'cell_lon': np.round(g.LON0 + gcol * g.RES, 8),
                       'crop_pixels': np.array(list(acc.values()), dtype=np.int64)})
    info['county_pixels'] = {shapes[i - 1][0]: int(county_pixels[i]) for i in range(1, len(county_pixels))}
    info['county_crs_prj'] = prj
    return df, info


def _env() -> dict:
    import pyproj
    import rasterio
    import shapefile
    import shapely
    return {'python': sys.version.split()[0], 'executable': sys.executable, 'platform': platform.platform(), 'numpy': np.__version__,
            'pandas': pd.__version__, 'rasterio': rasterio.__version__, 'gdal': rasterio.__gdal_version__, 'pyproj': pyproj.__version__,
            'proj': pyproj.proj_version_str, 'shapely': shapely.__version__, 'pyshp': shapefile.__version__}


def weights() -> None:
    t0 = time.time()
    frames, rasters, zero = [], {}, []
    for year in MASK_YEARS:
        for state, fips in STATE_FIPS.items():
            tif = RAW / f'CDL_{year}_{fips}.tif'
            meta = json.loads(tif.with_suffix('.manifest.json').read_text())
            if _sha(tif) != meta['sha256']:
                raise ValueError(f'hash mismatch {tif.name}')
            df, info = raster_counts(tif, state, year)
            frames.append(df)
            county_pixels = info.pop('county_pixels')
            rasters[tif.name] = {**info, 'sha256': meta['sha256'], 'url': meta['url'], 'crop_pixels': int(df.crop_pixels.sum()),
                                 'counties_rasterized': int(sum(v > 0 for v in county_pixels.values())), 'counties_in_shapefile': len(county_pixels)}
            from data.ingest import us_v2_weather as wx
            have = set(zip(df.fips, df.crop))
            for f in county_pixels:
                for crop in wx.CROPS_BY_STATE[state]:
                    if (f, crop) not in have:
                        zero.append({'fips': f, 'crop': crop, 'mask_year': year, 'county_pixels': county_pixels[f]})
            print(json.dumps({'raster': tif.name, 'rows': len(df), 'crop_pixels': int(df.crop_pixels.sum()), 'elapsed_s': round(time.time() - t0)}), flush=True)
    w = weights_from_counts(pd.concat(frames, ignore_index=True)).sort_values(['fips', 'crop', 'mask_year', 'cell_lat', 'cell_lon'], ascending=[True, True, True, False, True])
    path = OUT / 'cdl_gridmet_weights.csv.gz'
    OUT.mkdir(parents=True, exist_ok=True)
    w.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0}, float_format='%.10g')
    sums = w.groupby(['fips', 'crop', 'mask_year']).weight.sum()
    census = json.loads((RAW / 'cb_2024_us_county_500k.manifest.json').read_text())
    _save_json(OUT / 'cdl_gridmet_weights.manifest.json', {
        'generated_at': datetime.now(timezone.utc).isoformat(), 'runtime_s': round(time.time() - t0), 'rows': len(w), 'sha256': _sha(path),
        'groups': int(len(sums)), 'weight_sum_min': float(sums.min()), 'weight_sum_max': float(sums.max()),
        'zero_crop_pixel_groups': zero, 'zero_crop_pixel_count': len(zero),
        'crop_codes': CROP_CODES, 'crop_codes_source': LEGEND_URL, 'crop_codes_rule': 'primary classes only; double-crop classes (e.g. 26, 225) excluded',
        'mask_years': list(MASK_YEARS), 'rasters': rasters,
        'county_polygons': {'url': CENSUS_URL, 'sha256': census['sha256'], 'bytes': census['bytes']},
        'method': __doc__.split('Method:')[1].strip(), 'environment_raster': _env()})
    print(json.dumps({'rows': len(w), 'groups': int(len(sums)), 'zero_groups': len(zero), 'runtime_s': round(time.time() - t0)}), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['download', 'weights'])
    a = ap.parse_args()
    download() if a.action == 'download' else weights()
