"""US v2 experiment E2: gridMET daily (THREDDS NCSS) per state box, point and CDL-weighted county series.

Variables (names verified on the server's NCSS dataset.xml, 2026-11-15):
  pr -> precipitation_amount (mm), tmmn/tmmx -> air_temperature (K), rmax/rmin -> relative_humidity (%),
  vs -> wind_speed (m/s, 10 m).
Mapping to the POWER columns so us_v2_features.weather_features is reused unchanged:
  PRECTOTCORR = pr; T2M_MIN = tmmn - 273.15; T2M_MAX = tmmx - 273.15; RH2M = (rmax + rmin) / 2;
  WS2M = vs * 4.87 / ln(67.8 * 10 - 5.42) (FAO-56 eq. 47, 10 m -> 2 m).
Missing values stay NaN (never zero). Raw responses are stored gzip-compressed with sha256 of both the
server bytes and the stored file. Main interpreter only (numpy/pandas/netCDF4/scipy); CDL weights come
from data/ingest/us_v2_cdl.py run in a separate raster venv.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from data.ingest import us_v2_features as feats
from data.ingest import us_v2_weather as wx

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/us_training_v2/gridmet'
OUT = ROOT / 'data/processed/us_training_v2'
AVAILABILITY_PATH = ROOT / 'data/frozen/us_v2_availability.json'
WEIGHTS_PATH = OUT / 'cdl_gridmet_weights.csv.gz'

NCSS = 'http://thredds.northwestknowledge.net:8080/thredds/ncss/MET/{var}/{var}_{year}.nc'
VARIABLES = {'pr': 'precipitation_amount', 'tmmn': 'air_temperature', 'tmmx': 'air_temperature',
             'rmax': 'relative_humidity', 'rmin': 'relative_humidity', 'vs': 'wind_speed'}
EXPECTED_UNITS = {'pr': 'mm', 'tmmn': 'K', 'tmmx': 'K', 'rmax': '%', 'rmin': '%', 'vs': 'm/s'}
YEARS = range(2000, 2026)
# Census state extents (degrees, NAD83) + 0.15 degree margin (> 3 gridMET cells).
MARGIN = 0.15
STATE_BOX = {'IA': {'north': 43.5012, 'south': 40.3755, 'west': -96.6397, 'east': -90.1401},
             'NE': {'north': 43.0017, 'south': 39.9999, 'west': -104.0535, 'east': -95.3080},
             'KS': {'north': 40.0032, 'south': 36.9930, 'west': -102.0517, 'east': -94.5884}}
# gridMET CONUS grid: cell centres, north-to-south latitude, 1/24 degree (from the files' geospatial attributes).
LAT0 = 49.4
LON0 = -124.7666666333333
RES = 1.0 / 24.0
WIND_10M_TO_2M = 4.87 / math.log(67.8 * 10.0 - 5.42)
MASK_YEARS = (2006, 2009, 2012, 2015, 2018, 2021, 2024)
PROVISIONAL_DAYS = 60
MAX_WORKERS = 3
BUDGET_BYTES = 60 * 1024 ** 3
POWER_COLUMNS = ['date'] + feats.FEATURE_VARS


# ---------------------------------------------------------------- pure helpers

def kelvin_to_celsius(x):
    return x - 273.15


def masked_to_nan(a) -> np.ndarray:
    """Masked (fill/missing) entries become NaN, never zero."""
    return np.ma.filled(np.ma.asarray(a).astype('float64'), np.nan)


def to_power_columns(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({'date': raw['date'].to_numpy(),
                         'PRECTOTCORR': raw['pr'].to_numpy(float),
                         'T2M_MIN': kelvin_to_celsius(raw['tmmn'].to_numpy(float)),
                         'T2M_MAX': kelvin_to_celsius(raw['tmmx'].to_numpy(float)),
                         'RH2M': (raw['rmax'].to_numpy(float) + raw['rmin'].to_numpy(float)) / 2.0,
                         'WS2M': raw['vs'].to_numpy(float) * WIND_10M_TO_2M})


def cell_index(lat: float, lon: float) -> tuple[int, int]:
    """Global gridMET (row, col) of the cell containing (lat, lon)."""
    return int(np.floor((LAT0 - lat) / RES + 0.5)), int(np.floor((lon - LON0) / RES + 0.5))


def point_cell(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> tuple[int, int]:
    """Index into the subset axes of the cell whose extent (centre +- RES/2) contains the point."""
    i, j = int(np.argmin(np.abs(np.asarray(lats) - lat))), int(np.argmin(np.abs(np.asarray(lons) - lon)))
    if abs(lats[i] - lat) > RES / 2 + 1e-9 or abs(lons[j] - lon) > RES / 2 + 1e-9:
        raise ValueError('point_outside_grid')
    return i, j


def weighted_series(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """sum_cells w * value per day; any missing contributing cell keeps the day NaN.

    The result is a convex combination, so it is bounded to [min, max] of the contributing cells; the clip
    only removes floating-point overshoot (~1e-14) that would otherwise break physical-range checks
    (e.g. RH = 100.00000000000001).
    """
    v = np.asarray(values, float)
    w = np.asarray(weights, float)
    out = v @ w
    used = v[:, w > 0]
    with np.errstate(invalid='ignore'):
        lo, hi = used.min(axis=1), used.max(axis=1)  # NaN rows stay NaN (min/max propagate NaN)
    return np.where(np.isnan(out), np.nan, np.clip(out, lo, hi))


@lru_cache(maxsize=1)
def _cdl_table() -> dict:
    return json.loads(AVAILABILITY_PATH.read_text(encoding='utf-8'))['cdl']


def cdl_available_at(mask_year: int) -> date:
    confirmed = _cdl_table().get('confirmed', {})
    if str(mask_year) in confirmed:
        return date.fromisoformat(confirmed[str(mask_year)])
    return date(mask_year + 1, 3, 1)


def issued_at(state: str, crop: str, harvest_year: int) -> date:
    return wx.window(state, crop, harvest_year)[1] + timedelta(days=1)


def validate_mask_year(mask_year: int, harvest_year: int, issued: date, available_at=cdl_available_at) -> None:
    if mask_year >= harvest_year:
        raise ValueError(f'mask year {mask_year} not before harvest year {harvest_year}')
    if available_at(mask_year) > issued:
        raise ValueError(f'mask year {mask_year} not available at {issued}')


def select_mask_year(harvest_year: int, issued: date, mask_years=MASK_YEARS, available_at=cdl_available_at) -> int | None:
    ok = [m for m in mask_years if m <= harvest_year - 1 and available_at(m) <= issued]
    if not ok:
        return None
    m = max(ok)
    validate_mask_year(m, harvest_year, issued, available_at)
    return m


def resolve_weights(weights: pd.DataFrame, fips: str, crop: str, mask_year: int | None) -> tuple[pd.DataFrame | None, bool]:
    """Cells/weights for the county x crop x mask, or (None, True) -> point fallback."""
    if mask_year is None:
        return None, True
    cells = weights[(weights.fips == fips) & (weights.crop == crop) & (weights.mask_year == mask_year)]
    if cells.empty or cells.crop_pixels.sum() <= 0:
        return None, True
    return cells, False


# ---------------------------------------------------------------- download

def _paths(state: str, var: str, year: int) -> tuple[Path, Path]:
    base = RAW / state / f'{var}_{year}'
    return base.with_suffix('.nc.gz'), base.with_suffix('.manifest.json')


def request_params(state: str, var: str, year: int) -> dict:
    b = STATE_BOX[state]
    return {'var': VARIABLES[var], 'north': round(b['north'] + MARGIN, 4), 'south': round(b['south'] - MARGIN, 4),
            'west': round(b['west'] - MARGIN, 4), 'east': round(b['east'] + MARGIN, 4),
            'time_start': f'{year}-01-01T00:00:00Z', 'time_end': f'{year}-12-31T00:00:00Z', 'accept': 'netcdf'}


def _describe(raw: bytes, var: str) -> dict:
    import netCDF4
    with netCDF4.Dataset('mem', memory=raw) as ds:
        v = ds[VARIABLES[var]]
        attrs = {k: (v.getncattr(k).tolist() if hasattr(v.getncattr(k), 'tolist') else v.getncattr(k)) for k in v.ncattrs()}
        days = ds['day'][:]
        first = date(1900, 1, 1) + timedelta(days=int(days[0]))
        last = date(1900, 1, 1) + timedelta(days=int(days[-1]))
        glob = {k: str(ds.getncattr(k)) for k in ('last_permanent_slice', 'last_provisional_slice', 'last_early_slice', 'date', 'note5') if k in ds.ncattrs()}
        return {'variable': VARIABLES[var], 'attributes': {k: attrs[k] for k in ('units', 'description', 'long_name', 'scale_factor', 'add_offset', '_FillValue', 'missing_value', '_Unsigned') if k in attrs},
                'shape': list(v.shape), 'first_day': first.isoformat(), 'last_day': last.isoformat(), 'global': glob}


class Budget:
    def __init__(self, limit: int):
        self.limit, self.used, self.lock = limit, 0, threading.Lock()

    def add(self, n: int) -> None:
        with self.lock:
            self.used += n
            if self.used > self.limit:
                raise RuntimeError('network budget exceeded')


def fetch(state: str, var: str, year: int, budget: Budget, session: requests.Session | None = None) -> dict:
    path, meta = _paths(state, var, year)
    if path.exists() and meta.exists():
        m = json.loads(meta.read_text(encoding='utf-8'))
        if wx.sha(path) != m['sha256_stored']:
            raise ValueError(f'gridMET hash mismatch {path.name}')
        return {'state': state, 'var': var, 'year': year, 'status': 'cached', 'bytes': 0}
    url = NCSS.format(var=var, year=year)
    params = request_params(state, var, year)
    http = session or requests
    error = None
    for attempt in range(6):
        try:
            r = http.get(url, params=params, timeout=(30, 600))
            budget.add(len(r.content))
            if r.status_code in (429, 500, 502, 503, 504):
                error = f'HTTP {r.status_code}'
                time.sleep(min(120, 5 * 2 ** attempt))
                continue
            r.raise_for_status()
            raw = r.content
            info = _describe(raw, var)
            if info['attributes'].get('units') != EXPECTED_UNITS[var]:
                raise ValueError(f"unexpected units {info['attributes'].get('units')}")
            if info['first_day'] != f'{year}-01-01':
                raise ValueError('unexpected first day')
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.partial')
            tmp.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
            tmp.replace(path)
            import hashlib
            retrieved = datetime.now(timezone.utc)
            last = date.fromisoformat(info['last_day'])
            wx.save_json(meta, {'url': url, 'request': params, 'full_url': r.url, 'retrieved_at': retrieved.isoformat(), 'bytes': len(raw),
                                'sha256_raw': hashlib.sha256(raw).hexdigest(), 'sha256_stored': wx.sha(path), 'stored_as': 'gzip of the server NetCDF bytes',
                                'provisional_from': (retrieved.date() - timedelta(days=PROVISIONAL_DAYS)).isoformat() if last > retrieved.date() - timedelta(days=PROVISIONAL_DAYS) else None,
                                **info})
            return {'state': state, 'var': var, 'year': year, 'status': 'downloaded', 'bytes': len(raw)}
        except RuntimeError:
            raise
        except (requests.RequestException, ValueError, OSError, KeyError) as exc:
            error = f'{type(exc).__name__}: {str(exc)[:200]}'
            time.sleep(min(120, 5 * 2 ** attempt))
    return {'state': state, 'var': var, 'year': year, 'status': 'failed', 'error': error, 'bytes': 0}


def collect(years=YEARS) -> None:
    jobs = [(s, v, y) for s in STATE_BOX for y in years for v in VARIABLES]
    budget, statuses, t0 = Budget(BUDGET_BYTES), [], time.time()
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch, s, v, y, budget, session) for s, v, y in jobs]
        for fut in as_completed(futures):
            statuses.append(fut.result())
            last = statuses[-1]
            if len(statuses) % 20 == 0 or last['status'] == 'failed':
                print(json.dumps({'done': len(statuses), 'total': len(jobs), 'bytes_this_run': budget.used, 'elapsed_s': round(time.time() - t0), **last}), flush=True)
    failed = [s for s in statuses if s['status'] == 'failed']
    wx.save_json(RAW / 'collection_status.json', {'jobs': len(jobs), 'failed': failed, 'bytes_this_run': budget.used,
                                                  'elapsed_s': round(time.time() - t0), 'finished_at': datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'jobs': len(jobs), 'failed': len(failed), 'bytes_this_run': budget.used}), flush=True)
    if failed:
        raise SystemExit('Incomplete gridMET; rerun resumes verified files')


# ---------------------------------------------------------------- build

def read_file(state: str, var: str, year: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """(values[day, lat, lon] float32 with NaN, lats, lons, dates)."""
    import netCDF4
    path, meta = _paths(state, var, year)
    m = json.loads(meta.read_text(encoding='utf-8'))
    if wx.sha(path) != m['sha256_stored']:
        raise ValueError(f'hash mismatch {path.name}')
    raw = gzip.decompress(path.read_bytes())
    with netCDF4.Dataset('mem', memory=raw) as ds:
        v = ds[VARIABLES[var]]
        v.set_auto_maskandscale(True)
        values = masked_to_nan(v[:]).astype('float32')
        lats, lons = np.asarray(ds['lat'][:], float), np.asarray(ds['lon'][:], float)
        dates = [(date(1900, 1, 1) + timedelta(days=int(d))).isoformat() for d in ds['day'][:]]
        dims = v.dimensions
    if tuple(dims) != ('day', 'lat', 'lon'):
        raise ValueError(f'unexpected dims {dims}')
    return values, lats, lons, dates


def state_cells(state: str, lats: np.ndarray, lons: np.ndarray) -> tuple[dict, np.ndarray, np.ndarray]:
    """Global (row, col) -> subset (i, j) map for the downloaded box."""
    rows = [cell_index(la, lons[0])[0] for la in lats]
    cols = [cell_index(lats[0], lo)[1] for lo in lons]
    return {'rows': {r: i for i, r in enumerate(rows)}, 'cols': {c: j for j, c in enumerate(cols)}}, np.array(rows), np.array(cols)


def load_state(state: str, years=YEARS) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, list[str]]:
    data, lats0, lons0, dates_all = {}, None, None, None
    for var in VARIABLES:
        chunks, dates = [], []
        for y in years:
            values, lats, lons, d = read_file(state, var, y)
            if lats0 is None:
                lats0, lons0 = lats, lons
            elif not (np.allclose(lats, lats0) and np.allclose(lons, lons0)):
                raise ValueError('grid changed between files')
            chunks.append(values)
            dates += d
        if len(set(dates)) != len(dates):
            raise ValueError(f'duplicate days {state} {var}')
        if dates_all is None:
            dates_all = dates
        elif dates != dates_all:
            raise ValueError(f'day axis differs for {var}')
        data[var] = np.concatenate(chunks, axis=0)
    return data, lats0, lons0, dates_all


def _feature_row(series: pd.DataFrame, c: dict, crop: str, year: int, start: date, end: date) -> dict:
    return {'fips': c['fips'], 'state': c['state'], 'crop': crop, 'year': year, 'latitude': c['latitude'], 'longitude': c['longitude'],
            'window_start': start.isoformat(), 'window_end': end.isoformat(), **feats.weather_features(series, start, end)}


def _env() -> dict:
    import netCDF4
    import scipy
    import xarray
    return {'python': sys.version.split()[0], 'executable': sys.executable, 'platform': platform.platform(), 'numpy': np.__version__,
            'pandas': pd.__version__, 'netCDF4': netCDF4.__version__, 'xarray': xarray.__version__, 'scipy': scipy.__version__, 'requests': requests.__version__}


def build(harvest_years=range(2001, 2026)) -> None:
    t0 = time.time()
    weights = pd.read_csv(WEIGHTS_PATH, dtype={'fips': str})
    columns = ['fips', 'state', 'crop', 'year', 'latitude', 'longitude', 'window_start', 'window_end'] + feats.WEATHER_COLUMNS
    point_rows, cdl_rows, excluded, mask_log = [], [], [], []
    all_counties = wx.counties()
    for state in STATE_BOX:
        data, lats, lons, dates = load_state(state)
        index, rows, cols = state_cells(state, lats, lons)
        frame_dates = np.array(dates)
        date_pos = {d: k for k, d in enumerate(dates)}
        flat = {v: a.reshape(a.shape[0], -1) for v, a in data.items()}
        nlon = len(lons)

        def series_from(weight_vec_idx: np.ndarray, w: np.ndarray, k0: int, k1: int) -> pd.DataFrame:
            raw = {'date': frame_dates[k0:k1]}
            for v, a in flat.items():
                raw[v] = weighted_series(a[k0:k1][:, weight_vec_idx], w)
            return to_power_columns(pd.DataFrame(raw))

        for c in [c for c in all_counties if c['state'] == state]:
            pi, pj = point_cell(c['latitude'], c['longitude'], lats, lons)
            pidx = np.array([pi * nlon + pj])
            for crop in wx.CROPS_BY_STATE[state]:
                for year in harvest_years:
                    start, end = wx.window(state, crop, year)
                    issued = end + timedelta(days=1)
                    k0, k1 = date_pos.get(start.isoformat()), date_pos.get(end.isoformat())
                    if k0 is None or k1 is None:
                        excluded.append({'fips': c['fips'], 'crop': crop, 'year': year, 'table': 'both', 'reason': 'window_outside_downloaded_days'})
                        continue
                    k1 += 1
                    try:
                        prow = _feature_row(series_from(pidx, np.ones(1), k0, k1), c, crop, year, start, end)
                        point_rows.append(prow | {'mask_year': None, 'weight_fallback': False, 'source': 'gridmet_point'})
                    except ValueError as exc:
                        excluded.append({'fips': c['fips'], 'crop': crop, 'year': year, 'table': 'point', 'reason': str(exc)})
                        prow = None
                    m = select_mask_year(year, issued)
                    cells, fallback = resolve_weights(weights, c['fips'], crop, m)
                    if fallback:
                        if prow is None:
                            excluded.append({'fips': c['fips'], 'crop': crop, 'year': year, 'table': 'cdl', 'reason': 'point_fallback_unavailable'})
                        else:
                            cdl_rows.append(prow | {'mask_year': m, 'weight_fallback': True, 'source': 'gridmet_point_fallback'})
                        mask_log.append({'fips': c['fips'], 'crop': crop, 'year': year, 'mask_year': m,
                                         'reason': 'no_mask_available' if m is None else 'zero_crop_pixels'})
                        continue
                    validate_mask_year(m, year, issued)
                    gi = np.array([cell_index(a, b) for a, b in zip(cells.cell_lat, cells.cell_lon)])
                    try:
                        si = np.array([index['rows'][r] * nlon + index['cols'][q] for r, q in gi])
                    except KeyError as exc:
                        raise ValueError(f"weight cell outside downloaded box for {c['fips']}") from exc
                    try:
                        crow = _feature_row(series_from(si, cells.weight.to_numpy(float), k0, k1), c, crop, year, start, end)
                        cdl_rows.append(crow | {'mask_year': m, 'weight_fallback': False, 'source': 'gridmet_cdl_weighted'})
                    except ValueError as exc:
                        excluded.append({'fips': c['fips'], 'crop': crop, 'year': year, 'table': 'cdl', 'reason': str(exc)})
        del data, flat
        print(json.dumps({'state': state, 'point_rows': len(point_rows), 'cdl_rows': len(cdl_rows), 'elapsed_s': round(time.time() - t0)}), flush=True)

    extra = ['mask_year', 'weight_fallback', 'source']
    outputs = {}
    for name, rows in (('weather_features_gridmet_point.csv.gz', point_rows), ('weather_features_gridmet_cdl.csv.gz', cdl_rows)):
        df = pd.DataFrame(rows)[columns + extra]
        df['mask_year'] = df['mask_year'].astype('Int64')
        path = OUT / name
        df.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
        outputs[name] = {'rows': len(df), 'sha256': wx.sha(path), 'weight_fallback_rows': int(df.weight_fallback.sum()),
                         'by_crop': df.groupby('crop').size().to_dict(), 'mask_years': df.mask_year.value_counts(dropna=False).rename(index=str).to_dict()}
    files = sorted(RAW.glob('*/*.manifest.json'))
    metas = [json.loads(p.read_text(encoding='utf-8')) for p in files]
    wx.save_json(OUT / 'weather_features_gridmet.manifest.json', {
        'generated_at': datetime.now(timezone.utc).isoformat(), 'runtime_s': round(time.time() - t0), 'outputs': outputs,
        'excluded': excluded, 'fallbacks': mask_log, 'harvest_years': [min(harvest_years), max(harvest_years)],
        'source': {'service': NCSS, 'variables': VARIABLES, 'units': EXPECTED_UNITS, 'files': len(metas),
                   'bytes_downloaded_raw': int(sum(m['bytes'] for m in metas)), 'bytes_stored': int(sum(p.with_suffix('').with_suffix('.nc.gz').stat().st_size for p in files)),
                   'state_box_margin_deg': MARGIN, 'state_box': STATE_BOX,
                   'provisional_files': [f"{m['url']}" for m in metas if m.get('provisional_from')],
                   'grid': {'lat0': LAT0, 'lon0': LON0, 'res_deg': RES}},
        'mapping': {'PRECTOTCORR': 'pr (mm/day)', 'T2M_MIN': 'tmmn - 273.15', 'T2M_MAX': 'tmmx - 273.15', 'RH2M': '(rmax + rmin) / 2',
                    'WS2M': f'vs * 4.87 / ln(67.8*10 - 5.42) = vs * {WIND_10M_TO_2M:.6f} (FAO-56 eq. 47)'},
        'definition_differences_vs_power': {
            'RH2M': 'POWER RH2M is the daily mean relative humidity at 2 m; gridMET has only daily max/min RH, so RH2M here is the mean of the daily extremes',
            'WS2M': 'POWER WS2M is the daily mean wind at 2 m; gridMET vs is the daily mean wind at 10 m converted with the FAO-56 log profile',
            'day_definition': 'gridMET days end approximately at midnight MST (07 UTC next day); POWER daily values are UTC days',
            'temperature': 'gridMET tmmn/tmmx are 2 m daily extremes in K converted to degC'},
        'point_rule': 'gridMET cell whose extent (centre +- 1/48 degree) contains the Census county internal point',
        'weighted_rule': 'sum over cells of weight x cell value for each raw variable, then the POWER mapping; weights = share of county crop pixels (CDL primary class) per gridMET cell',
        'mask_rule': 'mask_year = max(m in MASK_YEARS, m <= Y-1, cdl_available_at(m) <= issued_at), issued_at = window end + 1 day; else point series with weight_fallback=True',
        'mask_years': list(MASK_YEARS), 'cdl_available_at': {str(m): cdl_available_at(m).isoformat() for m in MASK_YEARS},
        'missing_policy': 'fill/missing -> NaN; any NaN in a window excludes the row (weather_features raises); never zero',
        'labels_used': False, 'note_2025': 'crop year 2025 rows are inputs only; no labels read',
        'environment_main': _env()})
    print(json.dumps({'outputs': outputs, 'excluded': len(excluded), 'runtime_s': round(time.time() - t0)}, default=str), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['collect', 'build'])
    a = ap.parse_args()
    collect() if a.action == 'collect' else build()
