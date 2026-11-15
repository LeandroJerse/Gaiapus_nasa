"""v8 source study: training sources (IMERG Final, CERES via POWER) vs operational ones (IMERG Late, ERA5).

Parts
  rain_mvp    : daily IMERG Late vs Final at the 8 MVP cells, 2016-2021 (existing validated subset)
  rain_inmet  : IMERG Late (GES DISC subset at station cells) and Final (POWER at station coordinates)
                vs INMET gauges in 2025, same days; INMET hours shifted to UTC as in rain_validation_v2
  et0_mvp     : ET0 with CERES (POWER) vs ERA5 radiation, 8 MVP points, 2016-2021
  propagation : effect on features and estimates (source_swap_v1 and v8 models when available)
Retrospective study only: none of these products is shown to have been available in real time historically.
"""
import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

from data.ingest.water_balance_run import ROOT, save_json, write_csv

OUT = ROOT / 'data/processed/training_v8/sources'
STATIONS = {'A904': 'Sorriso', 'A917': 'Sinop', 'A903': 'S.J. do Rio Claro', 'A902': 'Tangara da Serra', 'A911': 'Sapezal',
            'A905': 'Campo Novo dos Parecis', 'A507': 'Uberlandia', 'A568': 'Uberaba', 'A542': 'Unai', 'A571': 'Paracatu',
            'A928': 'Nova Maringa', 'A927': 'Brasnorte'}


def stats(pairs, a, b):
    import numpy as np
    x = np.array([p[a] for p in pairs], float)
    y = np.array([p[b] for p in pairs], float)
    d = y - x
    return {'pairs': len(d), 'mae_mm': float(np.abs(d).mean()), 'bias_mm': float(d.mean()), 'rmse_mm': float(np.sqrt((d ** 2).mean())),
            'correlation': float(np.corrcoef(x, y)[0, 1]) if len(d) > 2 else None, 'sum_ref_mm': float(x.sum()), 'sum_other_mm': float(y.sum()),
            'wet_hits': int(((x >= 1) & (y >= 1)).sum()), 'wet_misses': int(((x >= 1) & (y < 1)).sum()),
            'wet_false_alarms': int(((x < 1) & (y >= 1)).sum()), 'extreme_days_ref_ge50': int((x >= 50).sum()),
            'extreme_days_other_ge50': int((y >= 50).sum())}


def rain_mvp():
    import pandas as pd
    points = pd.read_csv(ROOT / 'data/processed/imerg_subset_v1/points.csv', dtype={'municipality_code': str})
    wide = points[points.complete_48_half_hours].pivot_table(index=['municipality_code', 'day_utc'], columns='run', values='rain_mm').dropna().reset_index()
    rows = []
    for scope, part in [('all', wide), ('MG', wide[wide.municipality_code.str.startswith('31')]), ('MT', wide[wide.municipality_code.str.startswith('51')])]:
        for run in ('Early', 'Late'):
            rows.append({'scope': scope, 'compared': f'{run} vs Final', **stats(part.to_dict('records'), 'Final', run)})
    wide['month'] = wide.day_utc.str[:7]
    monthly = wide.groupby(['municipality_code', 'month'])[['Final', 'Late']].sum().reset_index()
    rows.append({'scope': 'monthly sums', 'compared': 'Late vs Final', **stats(monthly.to_dict('records'), 'Final', 'Late')})
    write_csv(OUT / 'rain_mvp_late_vs_final.csv', rows)
    return rows


def station_daily(year, station_id):
    from data.ingest.rain_validation_v2 import aggregate_hours, station_rows
    archive = ROOT / f'data/raw/training_v2/inmet/{year}.zip'
    return {r['day_utc']: r['rain_mm'] for r in aggregate_hours(station_rows(archive, station_id)) if r['day_utc'].startswith(str(year))}


def station_coordinates(year):
    import csv
    import io
    import zipfile
    coords = {}
    with zipfile.ZipFile(ROOT / f'data/raw/training_v2/inmet/{year}.zip') as zipped:
        for name in zipped.namelist():
            for station in STATIONS:
                if f'_{station}_' in name and name.upper().endswith('.CSV'):
                    head = zipped.read(name).decode('latin1').splitlines()[:8]
                    meta = {line.split(';')[0].strip(':').upper(): line.split(';')[1] for line in head if ';' in line}
                    coords[station] = (float(meta['LATITUDE'].replace(',', '.')), float(meta['LONGITUDE'].replace(',', '.')))
    return coords


def rain_inmet(year=2025, token=None):
    import os
    from data.ingest import cohort_v5 as v5
    from data.serving.live_features import fetch_imerg_late, fetch_power
    token = token or os.environ['EARTHDATA_TOKEN']
    coords = station_coordinates(year)
    points = [coords[s] for s in sorted(coords)]
    cache = ROOT / 'data/cache/v8_station_late'
    cache.mkdir(parents=True, exist_ok=True)
    late, granules = {}, {}
    day = date(year, 1, 1)
    while day <= date(year, 12, 31):
        path = cache / f'{day}.json'
        if not path.exists():
            values = fetch_imerg_late(points, day, token)
            path.write_text(json.dumps({'granule': values.pop('_granule'), 'values': {f'{k[0]},{k[1]}': v for k, v in values.items()}}), encoding='utf-8')
        stored = json.loads(path.read_text(encoding='utf-8'))
        granules[day.isoformat()] = stored['granule']
        late[day.isoformat()] = stored['values']
        day += timedelta(days=1)
    rows, per_station = [], []
    for station in sorted(coords):
        lat, lon = coords[station]
        final, _ = v5.parse_power(fetch_power(lat, lon, date(year, 1, 1), date(year, 12, 31)), station, date(year, 1, 1), date(year, 12, 31))
        gauge = station_daily(year, station)
        pairs = []
        for d, observed in gauge.items():
            f = final.get(d, {}).get('rain_mm')
            l = late.get(d, {}).get(f'{lat},{lon}')
            if observed is not None and f is not None and l is not None:
                pairs.append({'day': d, 'station': observed, 'final': f, 'late': l})
        for product in ('final', 'late'):
            if pairs:
                per_station.append({'station_id': station, 'name': STATIONS[station], 'product': product, **stats(pairs, 'station', product)})
        rows.extend({'station_id': station, **p} for p in pairs)
    write_csv(OUT / 'rain_inmet_2025_pairs.csv', rows)
    write_csv(OUT / 'rain_inmet_2025_by_station.csv', per_station)
    pooled = [{'product': product, **stats(rows, 'station', product)} for product in ('final', 'late')]
    write_csv(OUT / 'rain_inmet_2025_pooled.csv', pooled)
    versions = sorted({g.split('.')[-2] for g in granules.values()})
    save_json(OUT / 'rain_inmet_2025_meta.json', {'stations': {s: {'name': STATIONS[s], 'lat_lon': coords[s]} for s in coords},
                                                   'imerg_late_versions': versions, 'days': len(granules),
                                                   'note': 'Same days and station cells for both products; INMET hours shifted to UTC days'})
    return pooled, per_station, versions


def et0_mvp():
    import numpy as np
    from data.ingest import cohort_v5 as v5
    from data.ingest.cohort_v6 import RAW as V6_RAW
    from data.serving.live_features import fetch_era5_radiation, swap_sources
    locations = json.loads((ROOT / 'data/raw/training_v1/locations.json').read_text(encoding='utf-8'))
    era5 = fetch_era5_radiation([(l['latitude'], l['longitude']) for l in locations], date(2016, 1, 1), date(2021, 12, 31))
    rows = []
    for loc in locations:
        weather, elevation = v5.daily_weather(loc['municipality_code'], root=V6_RAW)
        weather = {d: r for d, r in weather.items() if '2016-01-01' <= d <= '2021-12-31'}
        swapped = swap_sources(weather, loc['latitude'], elevation, radiation=era5[(loc['latitude'], loc['longitude'])])
        a = np.array([weather[d]['et0_mm'] for d in weather if weather[d]['et0_mm'] is not None and swapped[d]['et0_mm'] is not None])
        b = np.array([swapped[d]['et0_mm'] for d in weather if weather[d]['et0_mm'] is not None and swapped[d]['et0_mm'] is not None])
        ra = np.array([weather[d]['radiation_mj_m2'] for d in weather if weather[d]['radiation_mj_m2'] is not None and swapped[d]['radiation_mj_m2'] is not None])
        rb = np.array([swapped[d]['radiation_mj_m2'] for d in weather if weather[d]['radiation_mj_m2'] is not None and swapped[d]['radiation_mj_m2'] is not None])
        rows.append({'municipality_code': loc['municipality_code'], 'days': len(a), 'et0_mae_mm': float(np.abs(b - a).mean()),
                     'et0_bias_mm': float((b - a).mean()), 'et0_mean_ceres_mm': float(a.mean()),
                     'radiation_bias_mj_m2': float((rb - ra).mean()), 'radiation_mae_mj_m2': float(np.abs(rb - ra).mean())})
    write_csv(OUT / 'et0_mvp_ceres_vs_era5.csv', rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', default='rain_mvp,et0_mvp,rain_inmet')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for part in args.parts.split(','):
        result = globals()[part]()
        print(part, json.dumps(result, default=str)[:1500], flush=True)


if __name__ == '__main__':
    from data.ingest.source_report_v8 import main as entry
    entry()
