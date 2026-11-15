"""Meteomatics soil-moisture, rain and evapotranspiration for the cohort points.

One request covers one calendar year for a batch of points, which fits the account
limits (1000 requests per day). Credentials come from METEOMATICS_USER and
METEOMATICS_PASSWORD and are never stored. Responses are kept immutable with receipts.
The default 'mix' source does not document which product backs past dates, so the
series is a retrospective reconstruction retrieved in 2026, not a vintage. Soil moisture
index: 0 at wilting point, 1 at field capacity, may exceed 1 after rain; -15cm is the
7-28 cm layer, -50cm is 28-100 cm and -150cm is 100-289 cm.
"""
import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from data.ingest.water_balance_run import ROOT, save_json, sha

RAW = ROOT / 'data/raw/meteomatics_v1'
COHORT_RAW = ROOT / 'data/raw/training_v6'
OUT = ROOT / 'data/processed/training_v6'
PARAMETERS = {'soil_moisture_index_-15cm:idx': 'smi_15cm', 'soil_moisture_index_-50cm:idx': 'smi_50cm',
              'soil_moisture_index_-150cm:idx': 'smi_150cm', 'precip_24h:mm': 'rain_mm', 'evapotranspiration_24h:mm': 'et_mm'}
ACCUMULATED = ('rain_mm', 'et_mm')  # value at T covers the 24 h before T
BATCH = 20  # 60-point yearly requests hit the server query timeout (HTTP 408)
YEARS = range(2001, 2025)


def batches(locations, size=BATCH):
    ordered = sorted(locations, key=lambda l: l['municipality_code'])
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def request_url(year, batch):
    points = '+'.join(f"{l['latitude']},{l['longitude']}" for l in batch)
    return (f'https://api.meteomatics.com/{year}-01-01T00:00:00Z--{year + 1}-01-01T00:00:00Z:P1D/'
            f'{",".join(PARAMETERS)}/{points}/json')


def parse(payload, batch):
    """Return {municipality_code: {field: {iso_day: value}}}; accumulated fields are shifted to the day they cover."""
    result = {l['municipality_code']: {field: {} for field in PARAMETERS.values()} for l in batch}
    for item in payload['data']:
        field = PARAMETERS[item['parameter']]
        if len(item['coordinates']) != len(batch):
            raise ValueError('Response does not hold one series per requested point')
        for location, series in zip(batch, item['coordinates']):
            if abs(series['lat'] - location['latitude']) > 1e-3 or abs(series['lon'] - location['longitude']) > 1e-3:
                raise ValueError('Response coordinates do not match the request order')
            for entry in series['dates']:
                moment = date.fromisoformat(entry['date'][:10])
                day = moment - timedelta(days=1) if field in ACCUMULATED else moment
                value = entry['value']
                result[location['municipality_code']][field][day.isoformat()] = None if value is None or value <= -666 else float(value)
    return result


def window_features(series, start, horizon):
    """Soil state and fluxes from sowing to the cutoff day, inclusive; nothing after it."""
    days = [(start + timedelta(days=i)).isoformat() for i in range(horizon)]
    out = {}
    for field in ('smi_15cm', 'smi_50cm', 'smi_150cm'):
        values = [series[field].get(day) for day in days]
        if None in values:
            return None
        out[f'mm_{field}_mean'] = sum(values) / horizon
        out[f'mm_{field}_min'] = min(values)
        out[f'mm_{field}_at_cutoff'] = values[-1]
    for field in ACCUMULATED:
        values = [series[field].get(day) for day in days]
        if None in values:
            return None
        out[f'mm_{field}_sum'] = sum(values)
    return out


def cohort_locations(states):
    locations = json.loads((COHORT_RAW / 'locations.json').read_text(encoding='utf-8'))
    return [l for l in locations if not states or l['state_code'] in states]


def collect(workers, states=None, locations=None):
    import requests
    auth = (os.environ.get('METEOMATICS_USER'), os.environ.get('METEOMATICS_PASSWORD'))
    if not all(auth):
        raise SystemExit('Meteomatics credentials unavailable in the process environment.')
    groups = batches(locations or cohort_locations(states))
    jobs = [(year, index, batch) for year in YEARS for index, batch in enumerate(groups)]
    pending = [job for job in jobs if not (RAW / str(job[0]) / f'p{BATCH}_batch_{job[1]:03d}.json.manifest.json').exists()]
    print(f'{len(jobs) - len(pending)} cached, {len(pending)} requests pending', flush=True)

    def fetch(job):
        year, index, batch = job
        url = request_url(year, batch)
        path = RAW / str(year) / f'p{BATCH}_batch_{index:03d}.json'
        for attempt in range(4):
            try:
                response = requests.get(url, auth=auth, timeout=400)
                if response.status_code == 200:
                    parse(response.json(), batch)
                    break
                if response.status_code in (401, 403):
                    return f'{year}/{index}: http_{response.status_code}'
            except Exception as error:
                response = error
            time.sleep(10 * (attempt + 1))
        else:
            return f'{year}/{index}: {getattr(response, "status_code", type(response).__name__)}'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        save_json(path.with_suffix('.json.manifest.json'), {
            'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'bytes': len(response.content),
            'sha256': hashlib.sha256(response.content).hexdigest(),
            'municipalities': [l['municipality_code'] for l in batch], 'source': 'Meteomatics API default mix; past-date source not documented',
            'availability_basis': 'first_successful_local_download_only'})
        return None
    with ThreadPoolExecutor(workers) as pool:
        failures = []
        for done, failure in enumerate(pool.map(fetch, pending), 1):
            if failure:
                failures.append(failure)
            if done % 40 == 0:
                print(f'{done}/{len(pending)} requests, {len(failures)} failures', flush=True)
    print(f'failures: {failures[:20]}', flush=True)
    return failures


def build(states=None):
    import pandas as pd
    groups = batches(cohort_locations(states))
    series = {}
    for index, batch in enumerate(groups):
        for year in YEARS:
            path = RAW / str(year) / f'p{BATCH}_batch_{index:03d}.json'
            receipt = path.with_suffix('.json.manifest.json')
            if not receipt.exists():
                continue
            if json.loads(receipt.read_text(encoding='utf-8'))['sha256'] != sha(path):
                raise ValueError(f'Checksum mismatch: {path.name}')
            for code, fields in parse(json.loads(path.read_bytes()), batch).items():
                target = series.setdefault(code, {field: {} for field in PARAMETERS.values()})
                for field, values in fields.items():
                    target[field].update(values)
    samples = pd.read_parquet(OUT / 'samples.parquet', columns=['sample_id', 'horizon_days', 'municipality_code', 'sowing_date'])
    rows, missing = [], 0
    for record in samples.itertuples(index=False):
        features = window_features(series[record.municipality_code], date.fromisoformat(record.sowing_date), record.horizon_days) \
            if record.municipality_code in series else None
        if features is None:
            missing += 1
            continue
        rows.append({'sample_id': record.sample_id, 'horizon_days': record.horizon_days, **features})
    pd.DataFrame(rows).to_parquet(OUT / 'meteomatics_features.parquet', index=False)
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'municipalities_with_series': len(series),
              'examples_with_features': len(rows), 'examples_without_complete_window': missing,
              'raw_bytes': sum(p.stat().st_size for p in RAW.rglob('*.json')), 'parameters': PARAMETERS,
              'availability_status': 'retrospective_mix_source_undocumented_retrieved_2026',
              'layers': {'smi_15cm': '7-28 cm', 'smi_50cm': '28-100 cm', 'smi_150cm': '100-289 cm'},
              'index_definition': '0 at wilting point, 1 at field capacity, can exceed 1 after rain'}
    save_json(OUT / 'meteomatics_report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'parameters'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--states', default='31,51', help='State codes of the pilot; batches depend on this list')
    args = parser.parse_args()
    states = set(args.states.split(',')) if args.states else None
    if args.download:
        collect(args.workers, states)
    build(states)
