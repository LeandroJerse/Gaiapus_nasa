"""Build public regional future-planning runs from data already collected, as immutable artifacts plus a capability catalog.

Layout of `out_dir` (consumed by the APP pin script and the Supabase uploader):
  runs/<run_id>/run.json          run envelope (no owner, no private field)
  runs/<run_id>/series.json.gz    all members' daily series and quantile bands (gzip, mtime 0 → reproducible bytes)
  index.json                      publications with artifact checksums and measured cost
  catalog.json                    capability per place × crop × harvest year, with stable reason codes
A run directory that already exists is never rewritten; republishing the same inputs yields identical bytes.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import time
import tracemalloc
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from data.forecasting.weather_ensemble import PowerPointFile
from data.models.crop_simulation.types import SimulationInputError, SoilProfile
from data.serving.future_simulation import PLANS, UNSUPPORTED, PlaceSpec, run_future_simulation

INDEX_CONTRACT = 'future-simulation-publications.v1'
CATALOG_CONTRACT = 'future-simulation-capabilities.v1'
SOIL = SoilProfile(55.0, 'AD4-hypothesis')  # same class as the location core; not a soil survey
MISSING_DATA = {'insufficient_history', 'source_unavailable', 'season_already_started_without_observations'}


def _dump(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise RuntimeError(f'immutable artifact differs: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def load_labels(files) -> dict:
    out = {}
    for f in files:
        with open(f, encoding='utf-8') as h:
            for r in csv.DictReader(h):
                if r.get('label_valid') != 'True' or not r.get('yield_kg_ha'):
                    continue
                out[(r['municipality_code'], r['crop'], int(r['harvest_year']))] = float(r['yield_kg_ha'])
    return out


def _complete_years(history) -> int:
    by_year = {}
    for d in history.days:
        by_year.setdefault(d.date.year, []).append(d.complete())
    return sum(1 for y, flags in by_year.items() if all(flags) and len(flags) >= 365)


def _observed(observed_dir: Path | None, code: str, location_id: str, as_of: datetime):
    if observed_dir is None:
        return None
    p = Path(observed_dir) / f'BR_ibge_{code}.json'
    if not p.is_file():
        return None
    h = PowerPointFile(p).load(location_id)
    days = tuple(d for d in h.days if d.date <= as_of.date())
    # The observed prefix is usable only when complete up to as_of (latency tails are missing in POWER/IMERG).
    if not days or days[-1].date != as_of.date() or not all(d.complete() for d in days):
        return None
    return replace(h, days=days)


def publish_regional(places: list[dict], *, history_dir: Path, observed_dir: Path | None, label_files, out_dir: Path, as_of: datetime,
                     issued_at: datetime, seed: int, harvest_years: list[int], max_members: int | None = None) -> dict:
    out_dir = Path(out_dir)
    labels = load_labels(label_files)
    previous = {}
    if (out_dir / 'index.json').is_file():
        previous = {p['run_id']: p for p in json.loads((out_dir / 'index.json').read_text(encoding='utf-8'))['publications']}
    publications, catalog = [], []
    for spec in places:
        code = spec['location_id'].split(':')[-1]
        try:
            history = PowerPointFile(Path(history_dir) / f'power_{code}.json').load(spec['location_id'])
        except SimulationInputError:
            history = None
        observed = _observed(observed_dir, code, spec['location_id'], as_of)
        place = PlaceSpec(spec['location_id'], spec['name'], spec['state_code'], history, observed, SOIL,
                          {(c, y): v for (m, c, y), v in labels.items() if m == code}) if history else None
        runs_by_crop, failures = {}, {}
        for plan_id in spec['plans']:
            if place is None:
                failures[plan_id] = 'source_unavailable'
                continue
            tracemalloc.start(); t0 = time.perf_counter()
            try:
                result = run_future_simulation(place, plan_id=plan_id, harvest_years=harvest_years, as_of=as_of, issued_at=issued_at,
                                               seed=seed, max_members=max_members)
            except SimulationInputError as error:
                tracemalloc.stop()
                failures[plan_id] = error.code
                continue
            seconds = time.perf_counter() - t0; peak = tracemalloc.get_traced_memory()[1]; tracemalloc.stop()
            run, series = result['run'], result['series']
            run_id = run['run_id']
            raw = _dump(series)
            gz = gzip.compress(raw, compresslevel=9, mtime=0)
            run_bytes = _dump(run)
            base = f'runs/{run_id}'
            _write_once(out_dir / base / 'run.json', run_bytes)
            _write_once(out_dir / base / 'series.json.gz', gz)
            entry = previous.get(run_id) or {
                'run_id': run_id, 'location_id': spec['location_id'], 'place_name': spec['name'], 'plan_id': plan_id,
                'crops': list(PLANS[plan_id]), 'harvest_years': sorted(harvest_years), 'as_of': run['as_of'], 'issued_at': run['issued_at'],
                'visibility': 'public_regional', 'publication_version': run_id, 'current': True,
                'artifacts': [{'kind': 'run', 'path': f'{base}/run.json', 'sha256': hashlib.sha256(run_bytes).hexdigest(), 'bytes': len(run_bytes), 'compression': None},
                              {'kind': 'series', 'path': f'{base}/series.json.gz', 'sha256': hashlib.sha256(gz).hexdigest(), 'bytes': len(gz), 'compression': 'gzip'}],
                'measured': {'seconds': round(seconds, 3), 'peak_python_mib': round(peak / 2 ** 20, 2), 'series_bytes_raw': len(raw),
                             'series_bytes_gzip': len(gz), 'members': run['ensemble']['members'], 'days': len(series['dates']),
                             'note': 'measured on the publishing machine; tracemalloc peak covers Python allocations only'}}
            publications.append(entry)
            for crop in PLANS[plan_id]:
                runs_by_crop.setdefault(crop, []).append(run_id)
        caps = []
        for crop in spec['crops']:
            for year in sorted(harvest_years):
                if crop in runs_by_crop:
                    caps.append({'crop_id': crop, 'harvest_year': year, 'status': 'ready', 'reason_code': None, 'run_ids': runs_by_crop[crop],
                                 'water_balance': 'physical_experimental', 'yield': 'reference_or_unavailable'})
                    continue
                reason = next((failures[p] for p in spec['plans'] if crop in PLANS.get(p, ()) and p in failures), None) or UNSUPPORTED.get(crop, 'unsupported')
                status = 'missing_data' if reason in MISSING_DATA else 'unsupported'
                caps.append({'crop_id': crop, 'harvest_year': year, 'status': status, 'reason_code': reason, 'run_ids': []})
        catalog.append({'location_id': spec['location_id'], 'name': spec['name'], 'state_code': spec['state_code'],
                        'history_years': _complete_years(history) if history else 0,
                        'observed_until': observed.days[-1].date.isoformat() if observed else None, 'capabilities': caps})
    publications.sort(key=lambda p: (p['location_id'], p['plan_id']))
    index = {'contract': INDEX_CONTRACT, 'as_of': as_of.isoformat(), 'publications': publications}
    _replace(out_dir / 'index.json', json.dumps(index, sort_keys=True, indent=1, ensure_ascii=False).encode('utf-8'))
    cat = {'contract': CATALOG_CONTRACT, 'as_of': as_of.isoformat(), 'harvest_years': sorted(harvest_years), 'places': catalog,
           'inputs': {'places': places, 'seed': seed, 'max_members': max_members}}
    _replace(out_dir / 'catalog.json', json.dumps(cat, sort_keys=True, indent=1, ensure_ascii=False).encode('utf-8'))
    return {'index': index, 'catalog': cat}


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description='Publish regional 2027/2028 planning runs (Brazil places with collected history).')
    ap.add_argument('--places', required=True, type=Path, help='JSON list of {location_id, name, state_code, plans, crops}')
    ap.add_argument('--history-dir', required=True, type=Path)
    ap.add_argument('--observed-dir', type=Path)
    ap.add_argument('--labels', nargs='+', type=Path, default=[])
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--as-of', required=True, help='ISO instant of the information limit, e.g. 2026-08-31T23:59:00+00:00')
    ap.add_argument('--seed', type=int, default=42)
    a = ap.parse_args(argv)
    publish_regional(json.loads(a.places.read_text(encoding='utf-8')), history_dir=a.history_dir, observed_dir=a.observed_dir, label_files=a.labels,
                     out_dir=a.out, as_of=datetime.fromisoformat(a.as_of), issued_at=datetime.now(timezone.utc), seed=a.seed, harvest_years=[2027, 2028])


if __name__ == '__main__':
    main()
