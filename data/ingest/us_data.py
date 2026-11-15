"""US research data: official-source extraction and leakage-aware identifiers/history."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / 'data/frozen/us_training_v1_protocol.json'
PROTOCOL = json.loads(PROTOCOL_PATH.read_text(encoding='utf-8'))
RAW = ROOT / 'data/raw/us_training_v1'
OUT = ROOT / 'data/processed/us_training_v1'
BULK_URL = 'https://www.nass.usda.gov/sites/default/files/datasets/qs.crops_20261114.txt.gz'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.partial')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def normalize_record(record):
    r = {k.lower(): str(v).strip() for k, v in record.items()}
    required = {'source_desc': 'SURVEY', 'agg_level_desc': 'COUNTY', 'prodn_practice_desc': 'ALL PRODUCTION PRACTICES', 'statisticcat_desc': 'YIELD', 'unit_desc': 'BU / ACRE', 'domain_desc': 'TOTAL', 'domaincat_desc': 'NOT SPECIFIED', 'freq_desc': 'ANNUAL', 'reference_period_desc': 'YEAR'}
    if any(r.get(k) != v for k, v in required.items()):
        return None
    state = r.get('state_alpha')
    if state not in PROTOCOL['states'] or r.get('state_ansi', '').zfill(2) != PROTOCOL['states'][state]:
        return None
    county = r.get('county_ansi', '')
    if not county.isdigit() or not 0 < int(county) < 900:
        return None
    commodity, kind, utilization = r.get('commodity_desc'), r.get('class_desc'), r.get('util_practice_desc')
    crop = None
    if commodity == 'CORN' and kind == 'ALL CLASSES' and utilization == 'GRAIN' and state in ('IA', 'NE'):
        crop = 'maize'
    elif commodity == 'SOYBEANS' and kind == 'ALL CLASSES' and utilization == 'ALL UTILIZATION PRACTICES' and state in ('IA', 'NE'):
        crop = 'soy'
    elif commodity == 'WHEAT' and kind == 'WINTER' and utilization == 'ALL UTILIZATION PRACTICES' and state == 'KS':
        crop = 'wheat_winter'
    if crop is None:
        return None
    try:
        year = int(r['year'])
    except (ValueError, KeyError):
        return None
    raw = r.get('value', '')
    try:
        value = float(raw.replace(',', ''))
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid yield')
        y = value * PROTOCOL['labels']['bushel_lb'][crop] * 0.45359237 * 10000 / 4046.8564224
    except ValueError:
        y = None
    return {'state': state, 'fips': PROTOCOL['states'][state] + county.zfill(3), 'county_name': r.get('county_name'), 'year': year, 'crop': crop, 'yield_kg_ha': y, 'value_raw': raw, 'unit_raw': r['unit_desc'], 'load_time': r.get('load_time'), 'availability_status': 'current_revised_snapshot_not_historical_vintage', 'practice': r['prodn_practice_desc']}


def split_name(year):
    if 1990 <= year <= 2020:
        return 'development'
    if year in (2022, 2023, 2024):
        return 'sealed_test'
    return None


def choose_counties(frame, per_state=50, holdout_count=10):
    fit = frame[(frame.year >= 2001) & (frame.year <= 2015) & frame.yield_kg_ha.notna()]
    result = []
    for state, crops in [('IA', ['soy', 'maize']), ('NE', ['soy', 'maize']), ('KS', ['wheat_winter'])]:
        part = fit[fit.state == state]
        sets = []
        for crop in crops:
            count = part[part.crop == crop].groupby('fips').year.nunique()
            sets.append(set(count[count >= 12].index))
        eligible = set.intersection(*sets) if sets else set()
        anchors = PROTOCOL['county_selection']['anchors'][state]
        ranked = sorted(eligible, key=lambda f: (f not in anchors, hashlib.sha256(f'us_training_v1:county:{f}'.encode()).hexdigest()))[:per_state]
        spatial = set(sorted([f for f in ranked if f not in anchors], key=lambda f: hashlib.sha256(f'us_training_v1:spatial:{f}'.encode()).hexdigest())[:holdout_count])
        result.extend({'state': state, 'fips': f, 'spatial_holdout': f in spatial} for f in ranked)
    return result


def history_features(history, target_year, history_ceiling):
    h = history[(history.year <= min(target_year - 2, history_ceiling)) & (history.year >= target_year - 10)].dropna(subset=['yield_kg_ha']).sort_values('year')
    if h.empty:
        return {k: None for k in ['hist_last', 'hist_mean3', 'hist_mean10', 'hist_trend', 'hist_age']} | {'hist_count': 0}
    import numpy as np
    y = h.yield_kg_ha.to_numpy(float)
    years = h.year.to_numpy(float)
    trend = np.polyval(np.polyfit(years - target_year, y, 1), 0) if len(h) >= 3 else y.mean()
    return {'hist_last': float(y[-1]), 'hist_mean3': float(y[-3:].mean()), 'hist_mean10': float(y.mean()), 'hist_trend': max(0., float(trend)), 'hist_count': len(h), 'hist_age': int(target_year - years[-1])}


def validate_prediction_inputs(frame):
    forbidden = {'yield_kg_ha', 'production', 'harvested_area', 'target', 'value_raw'} & set(frame.columns)
    if forbidden:
        raise ValueError(f'Target-bearing prediction input: {sorted(forbidden)}')
    if {'feature_end', 'cutoff'} <= set(frame.columns) and (frame.feature_end > frame.cutoff).any():
        raise ValueError('Post-cutoff weather')


class HashReader:
    def __init__(self, raw):
        self.raw, self.digest, self.count = raw, hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.raw.read(size)
        self.count += len(data)
        if self.count > 2_000_000_000:
            raise ValueError('Bulk network budget exceeded')
        self.digest.update(data)
        return data


def collect_bulk():
    """Stream official national gzip; persist only selected state/crop/statistic rows."""
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for name, expected in manifest['files'].items():
            if sha(RAW / name) != expected:
                raise ValueError('Existing USDA extraction hash mismatch')
        print('USDA extraction already complete and verified', flush=True)
        return
    response = requests.get(BULK_URL, stream=True, timeout=(30, 120))
    response.raise_for_status()
    stream = HashReader(response.raw)
    reader = gzip.GzipFile(fileobj=stream)
    header = reader.readline().decode('utf-8-sig').strip().split('\t')
    files = {k: gzip.open(RAW / f'{k}.jsonl.gz.partial', 'wt', encoding='utf-8') for k in ['development', 'sealed_test', 'ancillary']}
    counts = {k: 0 for k in files}
    total, last = 0, time.monotonic()
    try:
        for line in reader:
            total += 1
            if time.monotonic() - last > 25:
                print(json.dumps({'phase': 'USDA stream', 'network_mb': round(stream.count / 1e6, 1), 'lines_scanned': total, 'retained_rows': counts}), flush=True)
                last = time.monotonic()
            if not line.startswith(b'SURVEY\t') or b'\tCOUNTY\t' not in line or not any(s in line for s in (b'\tIA\t', b'\tNE\t', b'\tKS\t')):
                continue
            values = next(csv.reader([line.decode('utf-8').rstrip('\r\n')], delimiter='\t'))
            if len(values) != len(header):
                raise ValueError('Unexpected bulk column count')
            row = dict(zip([h.lower() for h in header], values))
            try:
                year = int(row['year'])
            except ValueError:
                continue
            partition = split_name(year)
            if partition is None:
                continue
            normalized = normalize_record(row)
            if normalized:
                files[partition].write(json.dumps(normalized, ensure_ascii=False) + '\n')
                counts[partition] += 1
            elif partition == 'development' and row['statisticcat_desc'] in ('AREA HARVESTED', 'AREA PLANTED', 'PRODUCTION'):
                candidate = {**row, 'statisticcat_desc': 'YIELD', 'unit_desc': 'BU / ACRE'}
                if normalize_record(candidate):
                    files['ancillary'].write(json.dumps(row, ensure_ascii=False) + '\n')
                    counts['ancillary'] += 1
        for handle in files.values():
            handle.close()
        for part in files:
            (RAW / f'{part}.jsonl.gz.partial').replace(RAW / f'{part}.jsonl.gz')
        save_json(manifest_path, {'source_url': BULK_URL, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'compressed_source_sha256': stream.digest.hexdigest(), 'network_bytes': stream.count, 'scanned_lines': total, 'counts': counts, 'protocol_sha256': sha(PROTOCOL_PATH), 'files': {f'{p}.jsonl.gz': sha(RAW / f'{p}.jsonl.gz') for p in files}, 'test_handling': 'Extracted mechanically into separate sealed_test file; values not logged or used in development'})
        print(json.dumps({'complete': True, 'counts': counts, 'saved_bytes': sum((RAW / f'{p}.jsonl.gz').stat().st_size for p in files)}), flush=True)
    finally:
        for handle in files.values():
            handle.close()
        response.close()


def load_development():
    frame = pd.read_json(RAW / 'development.jsonl.gz', lines=True, dtype={'fips': str})
    keys = ['fips', 'crop', 'year']
    if frame.duplicated(keys).any():
        raise ValueError('Duplicate USDA county/crop/year keys; must audit dimensions instead of averaging')
    if (frame.year > 2020).any():
        raise ValueError('Test labels in development file')
    return frame


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['collect', 'select'])
    args = parser.parse_args()
    if args.action == 'collect':
        collect_bulk()
    else:
        frame = load_development()
        selected = choose_counties(frame)
        save_json(OUT / 'counties.json', selected)
        print(json.dumps({'selected': len(selected), 'by_state': {s: sum(c['state'] == s for c in selected) for s in PROTOCOL['states']}}))
