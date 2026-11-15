"""Daily snapshot for the backend: yield estimates for every covered crop and municipality, plus water projections.

Usage:
  python -m data.serving.daily_job --as-of 2021-01-20 --mode offline        # retrospective demo, no network
  python -m data.serving.daily_job --mode live                              # today, live sources (needs EARTHDATA_TOKEN)
Output: <out>/<as_of>/yield/<crop>.json, <out>/<as_of>/water/<code>_<crop>.json, <out>/<as_of>/manifest.json
and <out>/latest.json pointing at the newest complete snapshot. The backend only reads these files.
"""
import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from data.ingest.water_balance_run import ROOT, save_json
from data.serving.yield_service import estimate_yield

DIST = ROOT / 'dist/gaiapus-models-v7'
MVP = ('3170206', '3170107', '3170404', '3147006', '5107925', '5107909', '5105259', '5102637')


def run(as_of, mode, out, crops=None, municipalities=None, water=True, workers=4, register=True):
    from data.serving import providers
    catalog = json.loads((DIST / 'catalog.json').read_text(encoding='utf-8'))
    provider = providers.Offline() if mode == 'offline' else providers.Live(ROOT / 'data/cache/live', mode='operational')
    source_mode = 'retrospective' if mode == 'offline' else 'operational'
    folder = Path(out) / as_of.isoformat()
    started = time.perf_counter()
    counts, failures, traced = {}, [], []
    for crop, item in catalog['crops'].items():
        if crops and crop not in crops:
            continue
        codes = [m['code'] for m in item['municipalities'] if not municipalities or m['code'] in municipalities]

        def one(code, crop=crop):
            trace = {}
            try:
                return estimate_yield(crop, code, as_of, catalog=catalog, models_dir=DIST / 'models', get_weather=provider.weather,
                                      get_history=provider.history, mode=source_mode, trace=trace), trace
            except Exception as error:
                failures.append({'crop': crop, 'municipality_code': code, 'error': f'{type(error).__name__}: {error}'[:300]})
                return None
        with ThreadPoolExecutor(workers) as pool:
            pairs = [r for r in pool.map(one, codes) if r is not None]
        responses = [r for r, _ in pairs]
        traced.extend(pairs)
        save_json(folder / 'yield' / f'{crop}.json', {'as_of': as_of.isoformat(), 'crop': crop, 'source_mode': source_mode,
                                                      'responses': responses})
        counts[crop] = dict(Counter(r.get('status') or r['error']['code'] for r in responses))
        print(crop, counts[crop], flush=True)
    water_files = []
    if water and mode == 'live':
        from data.serving.build_examples import water_example
        for code in MVP:
            for crop in ('soy', 'maize2'):
                try:
                    name, status, served, issued = water_example(code=code, crop=crop, horizon=15)
                    target = folder / 'water' / f'{code}_{crop}.json'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    (ROOT / 'contracts/examples' / name).replace(target)
                    water_files.append({'file': target.name, 'status': status, 'days_served': served, 'forecast_issued_at': issued})
                except Exception as error:
                    failures.append({'crop': crop, 'municipality_code': code, 'product': 'water', 'error': f'{type(error).__name__}: {error}'[:300]})
    registry = None
    if mode == 'live' and register:
        from data.serving import prospective_registry
        try:
            registry = prospective_registry.register(as_of, traced, provider, source_mode)
            registry = prospective_registry.seal(as_of) or registry
            print('prospective registry', {k: registry[k] for k in ('records_added', 'skipped', 'by_crop')}, flush=True)
        except Exception as error:
            failures.append({'product': 'prospective_registry', 'error': f'{type(error).__name__}: {error}'[:300]})
    manifest = {'as_of': as_of.isoformat(), 'generated_at': datetime.now(timezone.utc).isoformat(), 'mode': mode, 'source_mode': source_mode,
                'prospective_registry': registry,
                'catalog_version': catalog['version'], 'seconds': round(time.perf_counter() - started, 1), 'yield_status_counts': counts,
                'water': water_files, 'failures': failures, 'complete': not failures,
                'operational_release': 'not_released'}
    save_json(folder / 'manifest.json', manifest)
    if not failures:
        save_json(Path(out) / 'latest.json', {'as_of': as_of.isoformat(), 'folder': folder.name, 'generated_at': manifest['generated_at']})
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', type=date.fromisoformat, default=date.today())
    parser.add_argument('--mode', choices=('offline', 'live'), default='live')
    parser.add_argument('--out', default=str(ROOT / 'dist/snapshots'))
    parser.add_argument('--crops')
    parser.add_argument('--municipalities')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    manifest = run(args.as_of, args.mode, args.out, crops=set(args.crops.split(',')) if args.crops else None,
                   municipalities=set(args.municipalities.split(',')) if args.municipalities else None, workers=args.workers)
    print(json.dumps({k: v for k, v in manifest.items() if k != 'failures'}, indent=1), 'failures:', len(manifest['failures']))


if __name__ == '__main__':
    from data.serving.daily_job import main as entry
    entry()
