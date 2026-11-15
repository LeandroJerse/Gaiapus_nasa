"""US v2 weather: NASA POWER daily for every IA/NE/KS county, resumable, hashed, with coverage matrix.

Reuses the sealed v1 cache by verified copy (never writes under data/raw/us_training_v1).
Trailing -999 days at the end of a retrieval are 'not yet available', not gaps; missing never becomes zero.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
V1_RAW = ROOT / 'data/raw/us_training_v1'
RAW = ROOT / 'data/raw/us_training_v2'
OUT = ROOT / 'data/processed/us_training_v2'
POWER_URL = 'https://power.larc.nasa.gov/api/temporal/daily/point'
PARAMETERS = ['PRECTOTCORR', 'T2M_MIN', 'T2M_MAX', 'ALLSKY_SFC_SW_DWN', 'RH2M', 'WS2M']
STATES = {'19': 'IA', '31': 'NE', '20': 'KS'}
# Base calendar = v1 fixed regional hypotheses (E4 tests an alternative for wheat).
CALENDARS = {'soy': {'month_day': '05-20', 'days': 90, 'previous_year': False},
             'maize': {'month_day': '05-06', 'days': 90, 'previous_year': False},
             'wheat_winter': {'month_day': '10-02', 'days': 180, 'previous_year': True}}
CROPS_BY_STATE = {'IA': ['soy', 'maize'], 'NE': ['soy', 'maize'], 'KS': ['wheat_winter']}
FILL = -900.0


def sha(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.partial')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def payload_frame(payload: dict) -> pd.DataFrame:
    """POWER JSON -> one row per UTC date; fill values become NaN (never zero)."""
    frame = pd.DataFrame(payload['properties']['parameter']).reset_index(names='date')
    frame['date'] = pd.to_datetime(frame.date, format='%Y%m%d').dt.strftime('%Y-%m-%d')
    for p in PARAMETERS:
        frame[p] = pd.to_numeric(frame[p], errors='coerce').astype(float)
        frame.loc[~np.isfinite(frame[p]) | (frame[p] <= FILL), p] = np.nan
    return frame.sort_values('date').reset_index(drop=True)


def merge_chunks(frames: list[pd.DataFrame]) -> pd.DataFrame:
    merged = pd.concat(frames, ignore_index=True).sort_values('date')
    if merged.date.duplicated().any():
        raise ValueError('Overlapping weather chunks')
    return merged.reset_index(drop=True)


def _violations(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {'PRECTOTCORR': frame.PRECTOTCORR < 0, 'T2M_MIN': frame.T2M_MIN > frame.T2M_MAX, 'T2M_MAX': frame.T2M_MIN > frame.T2M_MAX,
            'ALLSKY_SFC_SW_DWN': frame.ALLSKY_SFC_SW_DWN < 0, 'RH2M': (frame.RH2M < 0) | (frame.RH2M > 100), 'WS2M': frame.WS2M < 0}


def quality(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    bad = _violations(frame)
    for p in PARAMETERS:
        valid = frame[p].notna().to_numpy()
        idx = np.flatnonzero(valid)
        last = int(idx[-1]) if len(idx) else -1
        rows.append({'variable': p, 'first_valid': frame.date.iloc[idx[0]] if len(idx) else None,
                     'last_valid': frame.date.iloc[last] if len(idx) else None,
                     'valid_days': int(valid.sum()),
                     'interior_gaps': int((~valid[:last + 1]).sum()) if len(idx) else 0,
                     'trailing_unavailable': int(len(valid) - last - 1),
                     'physical_violations': int(bad[p].fillna(False).sum())})
    return pd.DataFrame(rows)


def window(state: str, crop: str, harvest_year: int) -> tuple[date, date]:
    spec = CALENDARS[crop]
    start = date.fromisoformat(f"{harvest_year - int(spec['previous_year'])}-{spec['month_day']}")
    return start, start + timedelta(days=spec['days'] - 1)


def window_status(state: str, crop: str, harvest_year: int, observed_through: date) -> dict:
    start, end = window(state, crop, harvest_year)
    observed = max(0, (min(end, observed_through) - start).days + 1)
    status = 'complete' if observed_through >= end else ('partial' if observed > 0 else 'not_started')
    return {'state': state, 'crop': crop, 'harvest_year': harvest_year, 'window_start': start.isoformat(), 'window_end': end.isoformat(),
            'observed_through': observed_through.isoformat(), 'observed_days': observed, 'window_days': (end - start).days + 1, 'status': status}


def counties() -> list[dict]:
    with zipfile.ZipFile(V1_RAW / 'census_counties_2024.zip') as z:
        table = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), sep='\t', dtype=str)
    table.columns = table.columns.str.strip()
    table = table[table.GEOID.str[:2].isin(STATES)]
    return [{'fips': r.GEOID, 'state': STATES[r.GEOID[:2]], 'county_name': r.NAME, 'latitude': float(r.INTPTLAT), 'longitude': float(r.INTPTLONG)}
            for r in table.itertuples()]


def copy_v1_cache(fips: str, src: Path = V1_RAW / 'power', dst: Path = RAW / 'power') -> Path | None:
    data, meta = Path(src) / f'{fips}.json.gz', Path(src) / f'{fips}.manifest.json'
    if not data.exists():
        return None
    expected = json.loads(meta.read_text())['sha256']
    if sha(data) != expected:
        raise ValueError(f'v1 POWER cache hash mismatch for {fips}')
    Path(dst).mkdir(parents=True, exist_ok=True)
    target = Path(dst) / f'{fips}_2000-2024_v1.json.gz'
    if not target.exists():
        tmp = target.with_suffix('.partial')
        shutil.copyfile(data, tmp)
        if sha(tmp) != expected:
            tmp.unlink()
            raise ValueError('copy verification failed')
        tmp.replace(target)
        save_json(target.with_suffix('').with_suffix('.manifest.json'), {**json.loads(meta.read_text()), 'copied_from': data.relative_to(ROOT).as_posix(), 'copied_at': datetime.now(timezone.utc).isoformat()})
    return target


def fetch_chunk(county: dict, start: str, end: str, dst: Path = RAW / 'power') -> dict:
    tag = f"{county['fips']}_{start[:4]}-{end[:4]}_{end}"
    path, meta = Path(dst) / f'{tag}.json.gz', Path(dst) / f'{tag}.manifest.json'
    if path.exists() and meta.exists():
        if json.loads(meta.read_text())['sha256'] != sha(path):
            raise ValueError(f'POWER chunk hash mismatch {tag}')
        return {'fips': county['fips'], 'chunk': tag, 'status': 'cached'}
    params = {'parameters': ','.join(PARAMETERS), 'community': 'AG', 'latitude': county['latitude'], 'longitude': county['longitude'],
              'start': start, 'end': end, 'time-standard': 'UTC', 'format': 'JSON'}
    error = None
    for attempt in range(5):
        try:
            r = requests.get(POWER_URL, params=params, timeout=(30, 240))
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            payload = r.json()
            if set(PARAMETERS) - set(payload['properties']['parameter']):
                raise ValueError('POWER parameter missing')
            Path(dst).mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.partial')
            with gzip.open(tmp, 'wb', compresslevel=6) as f:
                f.write(r.content)
            tmp.replace(path)
            save_json(meta, {'url': POWER_URL, 'request': params, 'sha256': sha(path), 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                             'available_at': None, 'available_at_upper_bound': 'retrieved_at', 'units': payload.get('parameters'),
                             'note': 'current POWER snapshot; recent days may be revised; historical vintages unknown'})
            return {'fips': county['fips'], 'chunk': tag, 'status': 'downloaded'}
        except (requests.RequestException, ValueError, KeyError) as exc:
            error = f'{type(exc).__name__}'
            time.sleep(2 * (attempt + 1))
    return {'fips': county['fips'], 'chunk': tag, 'status': 'failed', 'error': error}


AUX_PARAMETERS = ['T2MDEW', 'PS']


def fetch_aux(county: dict, start: str = '20000101', end: str = '20261114', dst: Path = RAW / 'power_aux') -> dict:
    """Dewpoint and surface pressure (for FAO-56 ET0 in scenarios); elevation from the response geometry."""
    tag = f"{county['fips']}_aux_{start[:4]}-{end[:4]}_{end}"
    path, meta = Path(dst) / f'{tag}.json.gz', Path(dst) / f'{tag}.manifest.json'
    if path.exists() and meta.exists():
        if json.loads(meta.read_text())['sha256'] != sha(path):
            raise ValueError(f'aux chunk hash mismatch {tag}')
        return {'fips': county['fips'], 'status': 'cached'}
    params = {'parameters': ','.join(AUX_PARAMETERS), 'community': 'AG', 'latitude': county['latitude'], 'longitude': county['longitude'],
              'start': start, 'end': end, 'time-standard': 'UTC', 'format': 'JSON'}
    for attempt in range(5):
        try:
            r = requests.get(POWER_URL, params=params, timeout=(30, 240))
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            payload = r.json()
            if set(AUX_PARAMETERS) - set(payload['properties']['parameter']):
                raise ValueError('aux parameter missing')
            Path(dst).mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.partial')
            with gzip.open(tmp, 'wb', compresslevel=6) as f:
                f.write(r.content)
            tmp.replace(path)
            save_json(meta, {'url': POWER_URL, 'request': params, 'sha256': sha(path), 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                             'elevation_m': payload['geometry']['coordinates'][2], 'units': payload.get('parameters')})
            return {'fips': county['fips'], 'status': 'downloaded'}
        except (requests.RequestException, ValueError, KeyError) as exc:
            err = type(exc).__name__
            time.sleep(2 * (attempt + 1))
    return {'fips': county['fips'], 'status': 'failed', 'error': err}


def read_aux(fips: str, dst: Path = RAW / 'power_aux') -> tuple[pd.DataFrame, float]:
    paths = sorted(Path(dst).glob(f'{fips}_aux_*.json.gz'))
    if not paths:
        raise FileNotFoundError(fips)
    meta = json.loads(paths[-1].with_suffix('').with_suffix('.manifest.json').read_text())
    if sha(paths[-1]) != meta['sha256']:
        raise ValueError('aux hash mismatch')
    with gzip.open(paths[-1], 'rt', encoding='utf-8') as f:
        p = json.load(f)['properties']['parameter']
    frame = pd.DataFrame(p).reset_index(names='date')
    frame['date'] = pd.to_datetime(frame.date, format='%Y%m%d').dt.strftime('%Y-%m-%d')
    for c in AUX_PARAMETERS:
        frame[c] = pd.to_numeric(frame[c], errors='coerce')
        frame.loc[frame[c] <= FILL, c] = np.nan
    return frame, float(meta['elevation_m'])


def collect_aux() -> None:
    statuses = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for fut in as_completed([pool.submit(fetch_aux, c) for c in counties()]):
            statuses.append(fut.result())
    failed = [s for s in statuses if s['status'] == 'failed']
    print(json.dumps({'aux_counties': len(statuses), 'failed': len(failed)}), flush=True)
    if failed:
        raise SystemExit('aux incomplete; rerun resumes')


def read_county(fips: str, dst: Path = RAW / 'power') -> pd.DataFrame:
    frames = []
    for path in sorted(Path(dst).glob(f'{fips}_*.json.gz')):
        meta = json.loads(path.with_suffix('').with_suffix('.manifest.json').read_text())
        if sha(path) != meta['sha256']:
            raise ValueError(f'hash mismatch {path.name}')
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            frames.append(payload_frame(json.load(f)))
    if not frames:
        raise FileNotFoundError(fips)
    return merge_chunks(frames)


def collect(end: str) -> None:
    items = counties()
    jobs = []
    for c in items:
        if copy_v1_cache(c['fips']) is not None:
            jobs.append((c, '20250101', end))
        else:
            jobs.append((c, '20000101', end))
    statuses = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_chunk, c, s, e) for c, s, e in jobs]
        for fut in as_completed(futures):
            statuses.append(fut.result())
            if len(statuses) % 25 == 0 or statuses[-1]['status'] == 'failed':
                print(json.dumps({'done': len(statuses), 'total': len(jobs), **statuses[-1]}), flush=True)
    save_json(OUT / 'weather_collection_status.json', statuses)
    failed = [s for s in statuses if s['status'] == 'failed']
    print(json.dumps({'counties': len(items), 'failed': len(failed)}), flush=True)
    if failed:
        raise SystemExit('Incomplete weather; rerun resumes verified chunks')


def coverage() -> None:
    rows, windows = [], []
    for c in counties():
        frame = read_county(c['fips'])
        q = quality(frame)
        q.insert(0, 'fips', c['fips'])
        q.insert(1, 'state', c['state'])
        rows.append(q)
        through = date.fromisoformat(min(q.last_valid))  # all six variables valid through this day
        for crop in CROPS_BY_STATE[c['state']]:
            for year in (2025, 2026, 2027, 2028):
                windows.append({'fips': c['fips'], **window_status(c['state'], crop, year, through)})
    matrix = pd.concat(rows, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    matrix.to_csv(OUT / 'weather_coverage_matrix.csv', index=False)
    wt = pd.DataFrame(windows)
    wt.to_csv(OUT / 'weather_window_coverage.csv', index=False)
    summary = {
        'counties': int(matrix.fips.nunique()),
        'by_variable': matrix.groupby('variable').agg(min_last_valid=('last_valid', 'min'), max_last_valid=('last_valid', 'max'),
                                                     interior_gaps=('interior_gaps', 'sum'), physical_violations=('physical_violations', 'sum')).reset_index().to_dict('records'),
        'windows': wt.groupby(['state', 'crop', 'harvest_year', 'status']).size().rename('counties').reset_index().to_dict('records'),
        'files': {'weather_coverage_matrix.csv': sha(OUT / 'weather_coverage_matrix.csv'), 'weather_window_coverage.csv': sha(OUT / 'weather_window_coverage.csv')},
        'generated_at': datetime.now(timezone.utc).isoformat()}
    save_json(OUT / 'weather_coverage_summary.json', summary)
    print(json.dumps(summary, indent=1)[:4000])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['collect', 'coverage', 'collect_aux'])
    ap.add_argument('--end', default=(datetime.now(timezone.utc).date() - timedelta(days=1)).strftime('%Y%m%d'))
    a = ap.parse_args()
    {'collect': lambda: collect(a.end), 'coverage': coverage, 'collect_aux': collect_aux}[a.action]()
