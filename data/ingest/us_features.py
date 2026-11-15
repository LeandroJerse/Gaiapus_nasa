"""US climate collection and strictly pre-cutoff research features."""
from __future__ import annotations

import argparse
import gzip
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from data.ingest.us_data import OUT, RAW, PROTOCOL, PROTOCOL_PATH, choose_counties, history_features, load_development, save_json, sha, validate_prediction_inputs

GAZ_URL = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip'
POWER_URL = 'https://power.larc.nasa.gov/api/temporal/daily/point'
PARAMETERS = PROTOCOL['weather']['parameters']


def season_window(state, crop, harvest_year):
    spec = PROTOCOL['calendars'][crop]
    year = harvest_year - int(spec.get('sowing_previous_year', False))
    start = date.fromisoformat(f'{year}-{spec[state]}')
    return start, start + timedelta(days=spec['cutoff_days'] - 1)


def climate_features(weather, start, end):
    w = weather[(weather.date >= start.isoformat()) & (weather.date <= end.isoformat())].sort_values('date')
    expected = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if list(w.date) != expected:
        raise ValueError('Missing or duplicate pre-cutoff day')
    x = w[PARAMETERS].to_numpy(float)
    if not np.isfinite(x).all() or (x <= -900).any():
        raise ValueError('Missing or nonfinite weather')
    if (w.PRECTOTCORR < 0).any() or (w.T2M_MIN > w.T2M_MAX).any() or ((w.RH2M < 0) | (w.RH2M > 100)).any() or (w.WS2M < 0).any() or (w.ALLSKY_SFC_SW_DWN < 0).any():
        raise ValueError('Invalid physical range')
    result = {}
    for i, indices in enumerate(np.array_split(np.arange(len(w)), 3)):
        s = w.iloc[indices]
        prefix = f'w{i}_'
        result[prefix + 'rain_sum'] = float(s.PRECTOTCORR.sum())
        for col in PARAMETERS[1:]:
            result[prefix + col.lower() + '_mean'] = float(s[col].mean())
        result[prefix + 'hot_days'] = int((s.T2M_MAX > 32).sum())
        result[prefix + 'frost_days'] = int((s.T2M_MIN < 0).sum())
        result[prefix + 'dry_days'] = int((s.PRECTOTCORR < 1).sum())
        longest, current = 0, 0
        for dry in s.PRECTOTCORR < 1:
            current = current + 1 if dry else 0
            longest = max(longest, current)
        result[prefix + 'max_dry_run'] = longest
    return result


def build_row(county, crop, year, history, weather, history_ceiling):
    start, end = season_window(county['state'], crop, year)
    return {'sample_id': f"{county['fips']}:{crop}:{year}", 'state': county['state'], 'fips': county['fips'], 'crop': crop, 'year': year, 'spatial_holdout': county['spatial_holdout'], 'latitude': county['latitude'], 'longitude': county['longitude'], 'sowing_date': start.isoformat(), 'cutoff': end.isoformat(), 'feature_end': end.isoformat(), **history_features(history, year, history_ceiling), **climate_features(weather, start, end)}


def fetch_coordinates(counties):
    path = RAW / 'census_counties_2024.zip'
    if not path.exists():
        r = requests.get(GAZ_URL, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    with zipfile.ZipFile(path) as z:
        table = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), sep='\t', dtype=str)
    table.columns = table.columns.str.strip()
    table = table.set_index('GEOID')
    for county in counties:
        row = table.loc[county['fips']]
        county.update(latitude=float(row.INTPTLAT), longitude=float(row.INTPTLONG), county_name=row.NAME)
    save_json(OUT / 'counties.json', counties)
    save_json(RAW / 'census_manifest.json', {'url': GAZ_URL, 'sha256': sha(path), 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'boundary_version': '2024; county internal point, not crop mask'})
    return counties


def collect_county(county):
    folder = RAW / 'power'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (county['fips'] + '.json.gz')
    meta = folder / (county['fips'] + '.manifest.json')
    if path.exists() and meta.exists():
        m = json.loads(meta.read_text())
        if m['sha256'] != sha(path):
            raise ValueError('POWER cache hash mismatch')
        return {'fips': county['fips'], 'status': 'cached', 'bytes': path.stat().st_size}
    params = {'parameters': ','.join(PARAMETERS), 'community': 'AG', 'latitude': county['latitude'], 'longitude': county['longitude'], 'start': '20000101', 'end': '20241231', 'time-standard': 'UTC', 'format': 'JSON'}
    last_error = None
    for attempt in range(4):
        try:
            r = requests.get(POWER_URL, params=params, timeout=(30, 180))
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            payload = r.json()
            if set(PARAMETERS) - set(payload['properties']['parameter']):
                raise ValueError('POWER parameter missing')
            temporary = path.with_suffix('.partial')
            with gzip.open(temporary, 'wb', compresslevel=6) as f:
                f.write(r.content)
            temporary.replace(path)
            save_json(meta, {'url': POWER_URL, 'request': params, 'sha256': sha(path), 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'available_at': None, 'operational_eligible': False, 'units': payload.get('parameters'), 'protocol_sha256': sha(PROTOCOL_PATH)})
            return {'fips': county['fips'], 'status': 'downloaded', 'bytes': path.stat().st_size}
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = type(exc).__name__
            time.sleep(attempt + 1)
    return {'fips': county['fips'], 'status': 'failed', 'error': last_error or 'retry_exhausted'}


def collect_weather():
    counties = choose_counties(load_development())
    if not counties:
        raise ValueError('No eligible counties')
    counties = fetch_coordinates(counties)
    statuses = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(collect_county, c): c for c in counties}
        for future in as_completed(jobs):
            result = future.result()
            statuses.append(result)
            save_json(OUT / 'weather_progress.json', statuses)
            print(json.dumps({'done': len(statuses), 'total': len(counties), **result}), flush=True)
    if any(r['status'] == 'failed' for r in statuses):
        raise ValueError('Incomplete weather; rerun resumes verified successes')


def read_weather(fips):
    path = RAW / 'power' / f'{fips}.json.gz'
    meta = json.loads(path.with_name(f'{fips}.manifest.json').read_text())
    if sha(path) != meta['sha256']:
        raise ValueError('POWER hash mismatch before features')
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        payload = json.load(f)
    w = pd.DataFrame(payload['properties']['parameter']).reset_index(names='date')
    w['date'] = pd.to_datetime(w.date, format='%Y%m%d').dt.strftime('%Y-%m-%d')
    return w


def build_features():
    development = load_development()
    counties = json.loads((OUT / 'counties.json').read_text())
    groups = {key: group for key, group in development.groupby(['fips', 'crop'])}
    partitions = {'selection': [], 'final_fit': [], 'test_inputs': []}
    exclusions = []
    for county in counties:
        weather = read_weather(county['fips'])
        for crop in (['soy', 'maize'] if county['state'] in ('IA', 'NE') else ['wheat_winter']):
            history = groups[(county['fips'], crop)]
            for name, years, ceiling in [('selection', list(range(2001, 2016)) + list(range(2017, 2021)), 2015), ('final_fit', range(2001, 2021), 2020), ('test_inputs', [2022, 2023, 2024], 2020)]:
                for year in years:
                    try:
                        row = build_row(county, crop, year, history, weather, ceiling)
                    except ValueError as exc:
                        exclusions.append({'fips': county['fips'], 'crop': crop, 'year': year, 'partition': name, 'reason': str(exc)})
                        continue
                    if name != 'test_inputs':
                        target = history[history.year == year].yield_kg_ha.dropna()
                        if len(target) != 1:
                            exclusions.append({'fips': county['fips'], 'crop': crop, 'year': year, 'partition': name, 'reason': 'label_missing'})
                            continue
                        row['yield_kg_ha'] = float(target.iloc[0])
                    partitions[name].append(row)
    manifest = {'protocol_sha256': sha(PROTOCOL_PATH), 'development_labels_sha256': sha(RAW / 'development.jsonl.gz'), 'county_sha256': sha(OUT / 'counties.json'), 'source_manifest_sha256': sha(RAW / 'manifest.json'), 'files': {}, 'rows': {}, 'exclusions': exclusions}
    for name, rows in partitions.items():
        frame = pd.DataFrame(rows)
        if frame.empty or frame.sample_id.duplicated().any():
            raise ValueError('Empty or duplicate features')
        if name == 'test_inputs':
            validate_prediction_inputs(frame)
        path = OUT / f'{name}.csv.gz'
        frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
        manifest['files'][path.name] = sha(path)
        manifest['rows'][name] = len(frame)
    save_json(OUT / 'feature_manifest.json', manifest)
    print(json.dumps({'feature_rows': manifest['rows'], 'excluded_rows': len(exclusions)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['collect', 'build'])
    args = parser.parse_args()
    collect_weather() if args.action == 'collect' else build_features()
