"""Authenticated GES DISC OPeNDAP subset of daily IMERG Early/Late/Final.

Downloads only the bounding box of the eight municipal cells (~50 KB per granule
instead of ~30 MB) and keeps every response immutable with its receipt. Writes to
its own folders and never touches the full-file snapshots of imerg_operational.
The token is read from EARTHDATA_TOKEN and is never stored or printed.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from data.ingest.water_balance_run import ROOT, save_json, write_csv

RAW = ROOT / 'data/raw/imerg_subset_v1'
OUT = ROOT / 'data/processed/imerg_subset_v1'
CATALOG = ROOT / 'data/processed/imerg_operational_v1/catalog.csv'
RUNS = ('Early', 'Late', 'Final')
DATA_HOST = 'data.gesdisc.earthdata.nasa.gov'
OPENDAP = 'https://gpm1.gesdisc.eosdis.nasa.gov/opendap'


def now():
    return datetime.now(timezone.utc).isoformat()


def load_locations():
    return json.loads((ROOT / 'data/raw/training_v1/locations.json').read_text(encoding='utf-8'))


def cell_box(locations):
    """Indices on the regular 0.1 degree grid (cell centres start at -179.95, -89.95)."""
    cells = [(round((loc['longitude'] + 179.95) / 0.1), round((loc['latitude'] + 89.95) / 0.1)) for loc in locations]
    lons, lats = [c[0] for c in cells], [c[1] for c in cells]
    return cells, (min(lons), max(lons), min(lats), max(lats))


def opendap_url(record, box):
    url = urlsplit(record['download_url'])
    if url.scheme != 'https' or url.hostname != DATA_HOST or url.username or url.password \
            or not url.path.startswith('/data/GPM_L3/') or url.query:
        raise ValueError('Unexpected GES DISC download URL')
    i0, i1, j0, j1 = box
    grid = f'[0:0][{i0}:{i1}][{j0}:{j1}]'
    return (f'{OPENDAP}{url.path[len("/data"):]}.nc4?precipitation{grid},precipitation_cnt{grid},'
            f'time,lon[{i0}:{i1}],lat[{j0}:{j1}]')


def extract_subset(payload, record, locations, receipt):
    import netCDF4
    import numpy as np
    cells, box = cell_box(locations)
    day = record['observation_start'][:10]
    rows = []
    with netCDF4.Dataset('subset', memory=payload) as ds:
        if record['product'] not in ds.title or ds.BeginDate != day or ds.EndDate != day:
            raise ValueError('Product identity or daily interval mismatch')
        rain = ds.variables['precipitation']
        if rain.dimensions != ('time', 'lon', 'lat') or rain.units not in ('mm/day', 'mm d-1'):
            raise ValueError('Unexpected daily rain schema')
        time_var = ds.variables['time']
        if netCDF4.num2date(time_var[0], time_var.units).strftime('%Y-%m-%d') != day:
            raise ValueError('Granule time mismatch')
        lon, lat = ds.variables['lon'][:], ds.variables['lat'][:]
        if rain.shape != (1, box[1] - box[0] + 1, box[3] - box[2] + 1):
            raise ValueError('Subset shape differs from the requested box')
        for loc, (i, j) in zip(locations, cells):
            a, b = i - box[0], j - box[2]
            # The returned cell centre must be the nominal one and the nearest to the point.
            if abs(float(lon[a]) - (-179.95 + 0.1 * i)) > 1e-4 or abs(float(lat[b]) - (-89.95 + 0.1 * j)) > 1e-4 \
                    or abs(float(lon[a]) - loc['longitude']) > 0.0501 or abs(float(lat[b]) - loc['latitude']) > 0.0501:
                raise ValueError('Subset grid does not match the expected cell')
            value = rain[0, a, b]
            amount = None if np.ma.is_masked(value) else float(value)
            if amount is not None and (not math.isfinite(amount) or amount < 0):
                raise ValueError('Invalid rainfall')
            count = ds.variables['precipitation_cnt'][0, a, b]
            count = None if np.ma.is_masked(count) else int(count)
            rows.append({'municipality_code': loc['municipality_code'], 'run': record['run'], 'product': record['product'],
                         'day_utc': day, 'cell_lon': float(lon[a]), 'cell_lat': float(lat[b]), 'rain_mm': amount,
                         'units': 'mm/day', 'valid_half_hours': count,
                         'complete_48_half_hours': count == 48 and amount is not None,
                         'production_time': ds.ProductionTime, 'observation_end': record['observation_end'],
                         'first_seen_at': receipt['download_completed_at'], 'sha256': receipt['sha256'],
                         'snapshot_path': receipt['path'], 'catalog_updated_at': record['cmr_updated_at'],
                         'provider_first_published_at': None,
                         'availability_basis': 'first_successful_local_subset_download_only'})
    return rows


def pair_rows(rows):
    """Early/Late against Final on the same cell and UTC day."""
    groups = defaultdict(dict)
    for row in rows:
        groups[(str(row['municipality_code']), row['day_utc'])][row['run']] = row
    pairs = []
    for (code, day), runs in sorted(groups.items()):
        final = runs.get('Final')
        for run in ('Early', 'Late'):
            candidate = runs.get(run)
            valid = False
            if candidate and final:
                if abs(float(candidate['cell_lon']) - float(final['cell_lon'])) > 1e-5 \
                        or abs(float(candidate['cell_lat']) - float(final['cell_lat'])) > 1e-5:
                    raise ValueError('Cell mismatch')
                valid = all(r['complete_48_half_hours'] in (True, 'True') and r['rain_mm'] not in (None, '')
                            for r in (candidate, final))
            value = float(candidate['rain_mm']) if valid else None
            reference = float(final['rain_mm']) if valid else None
            pairs.append({'municipality_code': code, 'state_code': code[:2], 'day_utc': day, 'run': run,
                          'matched_complete': valid, 'rain_mm': value, 'final_mm': reference,
                          'difference_mm': value - reference if valid else None,
                          'absolute_difference_mm': abs(value - reference) if valid else None,
                          'status': 'paired_same_cell_UTC_not_field_validation' if valid
                          else 'missing_or_incomplete_product'})
    return pairs


def subset_path(record):
    return RAW / record['run'] / record['observation_start'][:4] / f'{record["filename"]}.subset.nc4'


def read_receipts():
    path = RAW / 'receipts.jsonl'
    if not path.exists():
        return {}
    with path.open(encoding='utf-8') as stream:
        return {r['filename']: r for r in map(json.loads, stream)}


def verified(record, receipts):
    receipt = receipts.get(record['filename'])
    path = subset_path(record)
    return bool(receipt) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == receipt['sha256']


def collect(records, locations, workers):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    token = os.environ.get('EARTHDATA_TOKEN')
    if not token:
        raise SystemExit('Earthdata credential unavailable. Set EARTHDATA_TOKEN in the process environment.')
    _, box = cell_box(locations)
    receipts = read_receipts()
    pending = [r for r in records if not verified(r, receipts)]
    print(f'{len(records) - len(pending)} verified, {len(pending)} pending', flush=True)
    local = threading.local()
    stop = threading.Event()
    hdf5_lock = threading.Lock()

    def fetch(record):
        if stop.is_set():
            return record, None, 'skipped_after_authorization_failure'
        if not hasattr(local, 'session'):
            local.session = requests.Session()
            local.session.auth = lambda request: request
            local.session.headers['Authorization'] = f'Bearer {token}'
            local.session.mount('https://', HTTPAdapter(max_retries=Retry(
                total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))))
        url = opendap_url(record, box)
        started = now()
        try:
            response = local.session.get(url, timeout=(15, 120))
        except requests.RequestException as error:
            return record, None, f'request_error:{type(error).__name__}'
        if response.status_code in (401, 403):
            stop.set()
        if response.status_code != 200:
            return record, None, f'http_{response.status_code}'
        raw = response.content
        if raw[:8] != b'\x89HDF\r\n\x1a\n':
            return record, None, 'not_netcdf_hdf5'
        path = subset_path(record)
        receipt = {'filename': record['filename'], 'run': record['run'], 'granule_id': record['granule_id'],
                   'url': url, 'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(),
                   'bytes': len(raw), 'download_started_at': started, 'download_completed_at': now(),
                   'last_modified_header': response.headers.get('Last-Modified'),
                   'acquisition': 'opendap_subset_bounding_box', 'box_lon_lat_index': list(box),
                   'provider_first_published_at': None, 'last_modified_is_not_first_availability': True}
        try:
            # The HDF5 library is not thread-safe: validate one payload at a time.
            with hdf5_lock:
                extract_subset(raw, record, locations, receipt)
        except (ValueError, OSError, KeyError) as error:
            return record, None, f'validation_failed:{error}'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return record, receipt, 'downloaded_and_validated'

    failures = []
    RAW.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool, (RAW / 'receipts.jsonl').open('a', encoding='utf-8') as ledger:
        for done, (record, receipt, status) in enumerate(pool.map(fetch, pending), 1):
            if receipt:
                ledger.write(json.dumps(receipt) + '\n')
                ledger.flush()
            else:
                failures.append({'filename': record['filename'], 'run': record['run'],
                                 'day_utc': record['observation_start'][:10], 'status': status})
            if done % 500 == 0:
                print(f'{done}/{len(pending)} processed, {len(failures)} failures', flush=True)
    return failures


def build(records, locations, start, end, failures):
    """Re-extract from the verified payloads; CSVs are derived, never the source of truth."""
    receipts = read_receipts()
    points, missing = [], []
    for record in records:
        if not verified(record, receipts):
            missing.append({'run': record['run'], 'day_utc': record['observation_start'][:10]})
            continue
        points.extend(extract_subset(subset_path(record).read_bytes(), record, locations, receipts[record['filename']]))
    OUT.mkdir(parents=True, exist_ok=True)
    keys = [(str(p['municipality_code']), p['day_utc'], p['run']) for p in points]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicated municipality-day-run keys')
    if points:
        write_csv(OUT / 'points.csv', points)
    pairs = pair_rows(points)
    if pairs:
        write_csv(OUT / 'rain_comparison_daily.csv', pairs)
    summary, monthly = [], defaultdict(list)
    for run in ('Early', 'Late'):
        for state in ('31', '51', 'all'):
            delta = [p['difference_mm'] for p in pairs
                     if p['run'] == run and p['matched_complete'] and state in ('all', p['state_code'])]
            summary.append({'run': run, 'state_code': state, 'pairs': len(delta),
                            'mae_mm': sum(map(abs, delta)) / len(delta) if delta else None,
                            'rmse_mm': math.sqrt(sum(x * x for x in delta) / len(delta)) if delta else None,
                            'bias_mm': sum(delta) / len(delta) if delta else None,
                            'scope': 'same_cell_same_UTC_day_against_Final_not_ground_truth'})
    write_csv(OUT / 'rain_comparison_summary.csv', summary)
    for pair in pairs:
        if pair['matched_complete']:
            monthly[(pair['municipality_code'], pair['day_utc'][:7], pair['run'])].append(pair)
    if monthly:
        write_csv(OUT / 'rain_comparison_monthly.csv', [
            {'municipality_code': code, 'month': month, 'run': run, 'paired_days': len(group),
             'run_mm_on_paired_days': sum(p['rain_mm'] for p in group),
             'final_mm_on_paired_days': sum(p['final_mm'] for p in group),
             'bias_mm_per_paired_day': sum(p['difference_mm'] for p in group) / len(group),
             'mae_mm_per_paired_day': sum(p['absolute_difference_mm'] for p in group) / len(group)}
            for (code, month, run), group in sorted(monthly.items())])
    days = (end - start).days + 1
    by_key = defaultdict(set)
    for point in points:
        if point['complete_48_half_hours']:
            by_key[(str(point['municipality_code']), point['day_utc'])].add(point['run'])
    incomplete = [{'municipality_code': p['municipality_code'], 'day_utc': p['day_utc'], 'run': p['run'],
                   'valid_half_hours': p['valid_half_hours'], 'rain_missing': p['rain_mm'] is None}
                  for p in points if not p['complete_48_half_hours']]
    write_csv(OUT / 'incomplete_cells.csv', incomplete or [{'municipality_code': '', 'day_utc': '', 'run': '',
                                                            'valid_half_hours': '', 'rain_missing': ''}])
    coverage = {'updated_at': now(), 'start': str(start), 'end': str(end), 'days': days,
                'expected_granules': days * len(RUNS), 'verified_granules': len(records) - len(missing),
                'missing_granules': missing, 'failures_last_run': failures,
                'expected_municipality_days': days * len(locations),
                'municipality_days_with_three_complete_products': sum(len(v) == 3 for v in by_key.values()),
                'cells_extracted': len(points), 'cells_incomplete_or_missing_rain': len(incomplete),
                'raw_bytes': sum(r['bytes'] for r in receipts.values()),
                'acquisition': 'opendap_subset_bounding_box',
                'availability_basis': 'first_successful_local_subset_download_only',
                'historical_available_at_reconstructed': False}
    save_json(OUT / 'coverage.json', coverage)
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default='2021-12-31')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--build-only', action='store_true', help='Rebuild tables from verified subsets only')
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    with CATALOG.open(encoding='utf-8', newline='') as stream:
        records = [r for r in csv.DictReader(stream) if args.start <= r['observation_start'][:10] <= args.end]
    expected = {((start + timedelta(days=i)).isoformat(), run) for i in range((end - start).days + 1) for run in RUNS}
    found = [(r['observation_start'][:10], r['run']) for r in records]
    if set(found) != expected or len(found) != len(expected):
        raise ValueError('Catalog is incomplete or ambiguous for the requested period')
    locations = load_locations()
    failures = [] if args.build_only else collect(records, locations, args.workers)
    coverage = build(records, locations, start, end, failures)
    print(json.dumps({k: v for k, v in coverage.items() if k not in ('missing_granules', 'failures_last_run')}, indent=2))
    print(f'missing {len(coverage["missing_granules"])}, failures {len(failures)}')


if __name__ == '__main__':
    main()
