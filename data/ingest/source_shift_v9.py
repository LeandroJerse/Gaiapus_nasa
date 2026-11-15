"""v9 plan section 2: controlled comparison of final vs operational-type inputs, same models and keys, no training.

Models : the served v7 artifacts (dist/gaiapus-models-v7, fitted on harvests <= 2019).
Keys   : harvest 2021 (outside their training; PAM 2019 lags published before the 2021 decisions), every catalog municipality
         and cutoff of each crop; the same lags (cohort labels <= 2019) in every variant.
Inputs : final  = NASA POWER as in training (IMERG Final rain, POWER radiation);
         late   = rain replaced by IMERG Late V07; early = rain replaced by IMERG Early V07;
         era5   = radiation replaced by ERA5 (ET0 recomputed); late_era5 = both.
Today's archived Late/Early/ERA5 values are used: a retrospective evaluation with operational-type sources (see
data/frozen/source_availability_v9.json), not a real-time replay.

Steps: `fetch` (resumable caches under data/cache/v9_shift), then `compare`.
"""
import argparse
import json
import os
import time
from datetime import date, timedelta

from data.ingest.water_balance_run import ROOT, save_json, write_csv

CACHE = ROOT / 'data/cache/v9_shift'
OUT = ROOT / 'data/processed/training_v9/source_shift'
FIRST, LAST = date(2019, 6, 1), date(2021, 9, 30)
PRODUCTS = {'late': 'GPM_3IMERGDL', 'early': 'GPM_3IMERGDE'}
HARVEST = 2021


def points():
    from data.serving.providers import locations
    return sorted({(v['latitude'], v['longitude']) for v in locations().values()})


def granule_name(short_name, day):
    import requests
    entries = requests.get('https://cmr.earthdata.nasa.gov/search/granules.json', params={
        'short_name': short_name, 'version': '07', 'temporal': f'{day}T00:00:00Z,{day}T23:59:59Z', 'page_size': 5},
        timeout=60).json()['feed']['entry']
    names = sorted({e['producer_granule_id'] for e in entries if e['time_start'][:10] == day.isoformat()})
    if not names:
        raise LookupError(f'{short_name} {day} not found')
    return names[-1]


def fetch_day(product, pts, day, token):
    """Daily IMERG (mm/day) at the 0.1 degree cell of each point; cells with fewer than 48 valid half-hours are None."""
    import netCDF4
    import numpy as np
    import requests
    short = PRODUCTS[product]
    cells = [(round((lon + 179.95) / 0.1), round((lat + 89.95) / 0.1)) for lat, lon in pts]
    i0, i1 = min(c[0] for c in cells), max(c[0] for c in cells)
    j0, j1 = min(c[1] for c in cells), max(c[1] for c in cells)
    grid = f'[0:0][{i0}:{i1}][{j0}:{j1}]'
    name = granule_name(short, day)
    url = (f'https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/{short}.07/{day:%Y}/{day:%m}/{name}.nc4'
           f'?precipitation{grid},precipitation_cnt{grid}')
    response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=240)
    response.raise_for_status()
    with netCDF4.Dataset('imerg', memory=response.content) as ds:
        if ds.BeginDate != day.isoformat():
            raise ValueError('unexpected day')
        rain = np.ma.filled(ds.variables['precipitation'][0].astype(float), np.nan)
        count = np.ma.filled(ds.variables['precipitation_cnt'][0].astype(float), np.nan)
    values = {}
    for (lat, lon), (i, j) in zip(pts, cells):
        value, valid = rain[i - i0, j - j0], count[i - i0, j - j0]
        values[f'{lat},{lon}'] = None if np.isnan(value) or valid != 48 else float(value)
    return name, values


def fetch():
    from datetime import datetime, timezone
    from data.serving.live_features import fetch_era5_radiation
    token = os.environ['EARTHDATA_TOKEN']
    pts = points()
    print('points', len(pts), flush=True)
    for product in PRODUCTS:
        folder = CACHE / product
        folder.mkdir(parents=True, exist_ok=True)
        day = FIRST
        while day <= LAST:
            path = folder / f'{day}.json'
            if not path.exists():
                for attempt in range(5):
                    try:
                        name, values = fetch_day(product, pts, day, token)
                        tmp = path.with_suffix('.part')
                        tmp.write_text(json.dumps({'granule': name, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'values': values}),
                                       encoding='utf-8')
                        tmp.replace(path)
                        break
                    except Exception as error:
                        print(product, day, 'retry', attempt, type(error).__name__, str(error)[:120], flush=True)
                        time.sleep(15 * (attempt + 1))
            if day.day == 1:
                print(product, day, flush=True)
            day += timedelta(days=1)
    era5 = CACHE / 'era5'
    era5.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(pts), 100):
        path = era5 / f'chunk_{i:05d}.json'
        if path.exists():
            continue
        chunk = pts[i:i + 100]
        for attempt in range(5):
            try:
                values = fetch_era5_radiation(chunk, FIRST, LAST)
                path.write_text(json.dumps({f'{k[0]},{k[1]}': v for k, v in values.items()}), encoding='utf-8')
                break
            except Exception as error:
                print('era5', i, 'retry', attempt, type(error).__name__, str(error)[:120], flush=True)
                time.sleep(60 * (attempt + 1))
        print('era5 chunk', i, flush=True)
    print('fetch complete', flush=True)


VARIANTS = ('final', 'late', 'early', 'era5', 'late_era5')


def load_rain(product):
    out = {}
    for path in sorted((CACHE / product).glob('*.json')):
        out[path.stem] = json.loads(path.read_text(encoding='utf-8'))['values']
    return out


def load_era5():
    out = {}
    for path in sorted((CACHE / 'era5').glob('chunk_*.json')):
        out.update(json.loads(path.read_text(encoding='utf-8')))
    return out


def variant_weather(weather, variant, key, latitude, elevation, rain, era5):
    from data.serving.live_features import swap_sources
    if variant == 'final':
        return weather
    new_rain = {d: rain[variant.split('_')[0]].get(d, {}).get(key) for d in weather} if variant in ('late', 'early', 'late_era5') else None
    new_rad = {d: era5.get(key, {}).get(d) for d in weather} if variant in ('era5', 'late_era5') else None
    return swap_sources(weather, latitude, elevation, rain=new_rain, radiation=new_rad)


def compare_crop(crop, catalog, provider, rain, era5):
    from data.ingest.cohort_v7 import COFFEE
    from data.ingest.crops import sowing_date
    from data.serving.live_features import feature_row
    from data.serving.yield_model import YieldModel
    from data.serving.yield_service import model_for
    item = catalog['crops'][crop]
    models, rows = {}, []
    for municipality in item['municipalities']:
        code = municipality['code']
        truth = provider.history(crop, code, [HARVEST]).get(HARVEST)
        if truth is None:
            continue
        scope = model_for(catalog, crop, code)
        if scope['artifact'] not in models:
            models[scope['artifact']] = YieldModel(ROOT / 'dist/gaiapus-models-v7/models' / scope['artifact'])
        model = models[scope['artifact']]
        start = date(HARVEST - 1, 6, 1) if crop in COFFEE else sowing_date(crop, code, HARVEST)
        first = start - timedelta(days=400) if crop in COFFEE else start
        last = start + timedelta(days=max(item['cutoffs_days']))
        weather, elevation, location = provider.weather(code, first, last)
        history = provider.history(crop, code, list(range(HARVEST - 6, HARVEST - 1)))
        key = f'{location["latitude"]},{location["longitude"]}'
        for variant in VARIANTS:
            w = variant_weather(weather, variant, key, location['latitude'], elevation, rain, era5)
            for cutoff in item['cutoffs_days']:
                row = feature_row(crop, code, HARVEST, cutoff, w, history, location, elevation)
                window = [w.get((start + timedelta(days=i)).isoformat()) for i in range(cutoff)]
                entry = {'crop': crop, 'scope': scope['scope'], 'municipality_code': code, 'state': code[:2], 'cutoff': cutoff,
                         'variant': variant, 'yield_kg_ha': truth, 'predicted_kg_ha': None,
                         'rain_mm': sum(r['rain_mm'] for r in window if r and r.get('rain_mm') is not None),
                         'et0_mm': sum(r['et0_mm'] for r in window if r and r.get('et0_mm') is not None)}
                if row is not None:
                    row['horizon_days'] = cutoff
                    entry['predicted_kg_ha'] = max(model.predict(row), 0.0)
                rows.append(entry)
    return rows


def compare():
    import numpy as np
    import pandas as pd
    from data.serving.providers import Offline
    from data.serving.score_prospective import bootstrap
    catalog = json.loads((ROOT / 'dist/gaiapus-models-v7/catalog.json').read_text(encoding='utf-8'))
    provider = Offline()
    rain = {'late': load_rain('late'), 'early': load_rain('early')}
    era5 = load_era5()
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for crop in catalog['crops']:
        started = time.perf_counter()
        frame = pd.DataFrame(compare_crop(crop, catalog, provider, rain, era5))
        frames.append(frame)
        print(crop, len(frame), f'{time.perf_counter() - started:.0f}s', flush=True)
    table = pd.concat(frames, ignore_index=True)
    table.to_parquet(OUT / 'predictions_2021.parquet')
    # same keys: municipality x cutoff predicted in every variant
    complete = table.pivot_table(index=['crop', 'scope', 'municipality_code', 'state', 'cutoff'], columns='variant', values='predicted_kg_ha')
    complete = complete.dropna()
    summary, drift = [], []
    for (crop, scope), part in complete.groupby(level=['crop', 'scope']):
        truth = table.drop_duplicates(['crop', 'municipality_code']).set_index(['crop', 'municipality_code']).yield_kg_ha
        rows = []
        for idx, values in part.iterrows():
            for variant in VARIANTS:
                rows.append({'model': variant, 'sample': idx[2], 'state': idx[3], 'abs_error': abs(values[variant] - truth[(crop, idx[2])])})
        h = pd.DataFrame(rows).groupby(['model', 'sample', 'state'], as_index=False).abs_error.mean()
        weighting = 'state50' if scope == 'MG+MT' else 'harvest'
        by_state = lambda m: h[h.model == m].groupby('state').abs_error.mean()
        mae = {v: float(by_state(v).mean()) if weighting == 'state50' else float(h[h.model == v].abs_error.mean()) for v in VARIANTS}
        for variant in VARIANTS[1:]:
            point, low, high, _, n = bootstrap(h, variant, 'final', weighting)
            summary.append({'crop': crop, 'scope': scope, 'variant': variant, 'harvests': n, 'mae_final_kg_ha': round(mae['final'], 1),
                            'mae_variant_kg_ha': round(mae[variant], 1), 'delta_kg_ha': round(point, 1), 'ci95_low': round(low, 1),
                            'ci95_high': round(high, 1), 'relative_change': round((mae[variant] - mae['final']) / mae['final'], 4)})
        keys = table[(table.crop == crop) & (table.scope == scope)]
        for variant in VARIANTS[1:]:
            a = keys[keys.variant == 'final'].set_index(['municipality_code', 'cutoff'])
            b = keys[keys.variant == variant].set_index(['municipality_code', 'cutoff'])
            joined = a[['rain_mm', 'et0_mm']].join(b[['rain_mm', 'et0_mm']], rsuffix='_v')
            drift.append({'crop': crop, 'scope': scope, 'variant': variant,
                          'rain_window_bias_mm': round(float((joined.rain_mm_v - joined.rain_mm).mean()), 1),
                          'rain_window_mae_mm': round(float((joined.rain_mm_v - joined.rain_mm).abs().mean()), 1),
                          'et0_window_bias_mm': round(float((joined.et0_mm_v - joined.et0_mm).mean()), 1),
                          'et0_window_mae_mm': round(float((joined.et0_mm_v - joined.et0_mm).abs().mean()), 1)})
    coverage = table.groupby(['crop', 'variant']).predicted_kg_ha.apply(lambda s: round(float(s.notna().mean()), 4)).unstack().reset_index()
    write_csv(OUT / 'summary_2021.csv', summary)
    write_csv(OUT / 'feature_drift_2021.csv', drift)
    coverage.to_csv(OUT / 'coverage_2021.csv', index=False)
    save_json(OUT / 'meta.json', {'harvest': HARVEST, 'models': 'served v7 artifacts (fit <= 2019)', 'variants': VARIANTS,
                                  'classification': 'retrospective with operational-type sources (archived Late/Early/ERA5)',
                                  'paired_keys': int(len(complete)), 'no_training': True})
    return pd.DataFrame(summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('fetch', 'compare'))
    args = parser.parse_args()
    from data.ingest import source_shift_v9 as module
    print(module.fetch() if args.step == 'fetch' else module.compare().to_string())
