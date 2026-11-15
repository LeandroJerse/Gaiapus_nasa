"""US v2 USDA extraction: county yields/areas by practice + Crop Progress, with sealed confirmation years.

Development = crop years 1990-2024 (2022-2024 flagged seen_in_v1_test). Crop year 2025 goes to a sealed file
that only the confirmation module may open; crop years >= 2026 go to a separate sealed file.
Sealed values are never printed or logged; only total line counts per partition are reported.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/us_training_v2'
V1_MANIFEST = ROOT / 'data/raw/us_training_v1/manifest.json'
BULK_URL = 'https://www.nass.usda.gov/sites/default/files/datasets/qs.crops_20261114.txt.gz'
STATES = {'IA': '19', 'NE': '31', 'KS': '20'}
BUSHEL_LB = {'soy': 60, 'maize': 56, 'wheat_winter': 60}
KG_PER_LB, M2_PER_ACRE = 0.45359237, 4046.8564224
COUNTY_STATS = {'YIELD': 'BU / ACRE', 'AREA HARVESTED': 'ACRES', 'AREA PLANTED': 'ACRES', 'PRODUCTION': 'BU'}
SEEN_IN_V1_TEST = (2022, 2023, 2024)
PARTS = ('development', 'sealed_confirmation_2025', 'sealed_future', 'progress')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def route(year: int) -> str | None:
    if 1990 <= year <= 2024:
        return 'development'
    if year == 2025:
        return 'sealed_confirmation_2025'
    if year >= 2026:
        return 'sealed_future'
    return None


def _crop(r: dict, statistic: str) -> str | None:
    commodity, kind, util, state = r.get('commodity_desc'), r.get('class_desc'), r.get('util_practice_desc'), r.get('state_alpha')
    corn_util = 'ALL UTILIZATION PRACTICES' if statistic == 'AREA PLANTED' else 'GRAIN'
    if commodity == 'CORN' and kind == 'ALL CLASSES' and util == corn_util and state in ('IA', 'NE'):
        return 'maize'
    if commodity == 'SOYBEANS' and kind == 'ALL CLASSES' and util == 'ALL UTILIZATION PRACTICES' and state in ('IA', 'NE'):
        return 'soy'
    if commodity == 'WHEAT' and kind == 'WINTER' and util == 'ALL UTILIZATION PRACTICES' and state == 'KS':
        return 'wheat_winter'
    return None


def _number(raw: str) -> float | None:
    try:
        v = float(raw.replace(',', ''))
    except ValueError:
        return None
    return v if math.isfinite(v) and v >= 0 else None


def normalize_county(record: dict) -> dict | None:
    r = {k.lower(): str(v).strip() for k, v in record.items()}
    fixed = {'source_desc': 'SURVEY', 'agg_level_desc': 'COUNTY', 'domain_desc': 'TOTAL', 'domaincat_desc': 'NOT SPECIFIED',
             'freq_desc': 'ANNUAL', 'reference_period_desc': 'YEAR'}
    if any(r.get(k) != v for k, v in fixed.items()):
        return None
    statistic = r.get('statisticcat_desc')
    if COUNTY_STATS.get(statistic) != r.get('unit_desc'):
        return None
    state = r.get('state_alpha')
    if state not in STATES or r.get('state_ansi', '').zfill(2) != STATES[state]:
        return None
    county = r.get('county_ansi', '')
    if not county.isdigit() or not 0 < int(county) < 900:
        return None
    crop = _crop(r, statistic)
    if crop is None:
        return None
    try:
        year = int(r['year'])
    except (KeyError, ValueError):
        return None
    raw = r.get('value', '')
    value = _number(raw)
    y = value * BUSHEL_LB[crop] * KG_PER_LB * 10000 / M2_PER_ACRE if (statistic == 'YIELD' and value is not None) else None
    return {'state': state, 'fips': STATES[state] + county.zfill(3), 'county_name': r.get('county_name'), 'asd_code': r.get('asd_code'),
            'year': year, 'crop': crop, 'statistic': statistic, 'practice': r.get('prodn_practice_desc'), 'unit': r.get('unit_desc'),
            'value': value, 'yield_kg_ha': y, 'value_raw': raw, 'load_time': r.get('load_time'),
            'availability_status': 'current_revised_snapshot_not_historical_vintage'}


def normalize_progress(record: dict) -> dict | None:
    r = {k.lower(): str(v).strip() for k, v in record.items()}
    if r.get('source_desc') != 'SURVEY' or r.get('statisticcat_desc') != 'PROGRESS' or r.get('freq_desc') != 'WEEKLY':
        return None
    if r.get('agg_level_desc') not in ('STATE', 'AGRICULTURAL DISTRICT') or r.get('state_alpha') not in STATES:
        return None
    commodity, kind = r.get('commodity_desc'), r.get('class_desc')
    if not ((commodity == 'WHEAT' and kind == 'WINTER' and r['state_alpha'] == 'KS') or
            (commodity in ('CORN', 'SOYBEANS') and r['state_alpha'] in ('IA', 'NE'))):
        return None
    if not r.get('unit_desc', '').startswith('PCT '):
        return None
    try:
        year = int(r['year'])
    except (KeyError, ValueError):
        return None
    return {'state': r['state_alpha'], 'level': r['agg_level_desc'], 'asd_code': r.get('asd_code'), 'commodity': commodity, 'class': kind,
            'unit': r['unit_desc'], 'year': year, 'reference_period': r.get('reference_period_desc'), 'week_ending': r.get('week_ending'),
            'value': _number(r.get('value', '')), 'value_raw': r.get('value', ''), 'load_time': r.get('load_time'),
            'available_at_rule': 'weekly report published after week_ending; exact historical publication time not in bulk file'}


class _HashReader:
    def __init__(self, raw, limit=2_000_000_000):
        self.raw, self.digest, self.count, self.limit = raw, hashlib.sha256(), 0, limit

    def read(self, size=-1):
        data = self.raw.read(size)
        self.count += len(data)
        if self.count > self.limit:
            raise ValueError('Bulk network budget exceeded')
        self.digest.update(data)
        return data


def extract(binary_stream, out: Path, source: str) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stream = _HashReader(binary_stream)
    reader = gzip.GzipFile(fileobj=stream)
    header = [h.lower() for h in reader.readline().decode('utf-8-sig').strip().split('\t')]
    handles = {p: gzip.open(out / f'{p}.jsonl.gz.partial', 'wt', encoding='utf-8') for p in PARTS}
    counts = {p: 0 for p in PARTS}
    scanned, last = 0, time.monotonic()
    states = tuple(f'\t{s}\t'.encode() for s in STATES)
    try:
        for line in reader:
            scanned += 1
            if time.monotonic() - last > 30:
                print(json.dumps({'network_mb': round(stream.count / 1e6), 'lines_scanned': scanned, 'development_rows': counts['development']}), flush=True)
                last = time.monotonic()
            if not line.startswith(b'SURVEY\t') or not any(s in line for s in states):
                continue
            values = next(csv.reader([line.decode('utf-8').rstrip('\r\n')], delimiter='\t'))
            if len(values) != len(header):
                raise ValueError('Unexpected bulk column count')
            rec = dict(zip(header, values))
            prog = normalize_progress(rec)
            if prog:
                handles['progress'].write(json.dumps(prog) + '\n')
                counts['progress'] += 1
                continue
            norm = normalize_county(rec)
            if not norm:
                continue
            part = route(norm['year'])
            if part is None:
                continue
            if part == 'development':
                norm['seen_in_v1_test'] = norm['year'] in SEEN_IN_V1_TEST
            handles[part].write(json.dumps(norm) + '\n')
            counts[part] += 1
        for h in handles.values():
            h.close()
        for p in PARTS:
            (out / f'{p}.jsonl.gz.partial').replace(out / f'{p}.jsonl.gz')
    finally:
        for h in handles.values():
            h.close()
    manifest = {'source': source, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'compressed_source_sha256': stream.digest.hexdigest(),
                'network_bytes': stream.count, 'scanned_lines': scanned, 'counts': counts,
                'files': {f'{p}.jsonl.gz': sha(out / f'{p}.jsonl.gz') for p in PARTS},
                'sealed_handling': 'crop years >= 2025 written mechanically to sealed files; values never logged; only us_v2_confirm may read sealed_confirmation_2025'}
    (out / 'usda_manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return manifest


def collect() -> dict:
    manifest_path = RAW / 'usda_manifest.json'
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text())
        for name, expected in m['files'].items():
            if sha(RAW / name) != expected:
                raise ValueError(f'USDA v2 extraction hash mismatch: {name}')
        print('USDA v2 extraction already complete and verified', flush=True)
        return m
    with requests.get(BULK_URL, stream=True, timeout=(30, 120)) as r:
        r.raise_for_status()
        m = extract(r.raw, RAW, BULK_URL)
    v1 = json.loads(V1_MANIFEST.read_text())
    m['same_snapshot_as_v1'] = v1['compressed_source_sha256'] == m['compressed_source_sha256']
    manifest_path.write_text(json.dumps(m, indent=1), encoding='utf-8')
    print(json.dumps({'counts': m['counts'], 'network_mb': round(m['network_bytes'] / 1e6), 'same_snapshot_as_v1': m['same_snapshot_as_v1']}), flush=True)
    return m


def load_development(raw: Path = RAW) -> pd.DataFrame:
    frame = pd.read_json(Path(raw) / 'development.jsonl.gz', lines=True, dtype={'fips': str, 'asd_code': str})
    if (frame.year > 2024).any():
        raise ValueError('Confirmation-year records in development file')
    return frame


def load_yields(raw: Path = RAW) -> pd.DataFrame:
    """Development county yield labels (all practices), one row per fips x crop x year."""
    f = load_development(raw)
    y = f[(f.statistic == 'YIELD') & (f.practice == 'ALL PRODUCTION PRACTICES')].copy()
    if y.duplicated(['fips', 'crop', 'year']).any():
        raise ValueError('Duplicate county/crop/year yield keys')
    return y


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['collect'])
    ap.parse_args()
    collect()
