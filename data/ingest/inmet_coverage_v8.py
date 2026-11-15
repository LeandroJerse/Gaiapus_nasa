"""Coverage, gaps and block-bootstrap uncertainty of the 2025 INMET rain comparison (review item 6).

Station-days are not independent (same storms, same station, consecutive days), so the
uncertainty of MAE(Late) - MAE(Final) and of the bias is estimated by resampling whole
station x month blocks (2,000 draws) and, separately, whole stations.
"""
import json

from data.ingest.water_balance_run import ROOT, save_json

SOURCES = ROOT / 'data/processed/training_v8/sources'


def main(draws=2000, seed=42):
    import numpy as np
    import pandas as pd
    pairs = pd.read_csv(SOURCES / 'rain_inmet_2025_pairs.csv')
    meta = json.loads((SOURCES / 'rain_inmet_2025_meta.json').read_text(encoding='utf-8'))
    pairs['month'] = pairs.day.str[5:7].astype(int)
    pairs['season'] = np.where(pairs.month.isin([10, 11, 12, 1, 2, 3]), 'wet Oct-Mar', 'dry Apr-Sep')
    coverage = pairs.pivot_table(index='station_id', columns='month', values='day', aggfunc='count', fill_value=0)
    stations = {}
    for sid, info in meta['stations'].items():
        part = pairs[pairs.station_id == sid]
        days = pd.to_datetime(part.day)
        gaps = days.sort_values().diff().dt.days.dropna()
        stations[sid] = {'name': info['name'], 'lat_lon': info['lat_lon'], 'state': 'MG' if sid in ('A507', 'A542', 'A568', 'A571') else 'MT',
                         'paired_days': int(len(part)), 'first': part.day.min() if len(part) else None, 'last': part.day.max() if len(part) else None,
                         'wet_season_days': int((part.season == 'wet Oct-Mar').sum()), 'dry_season_days': int((part.season == 'dry Apr-Sep').sum()),
                         'longest_gap_days': int(gaps.max() - 1) if len(gaps) else None,
                         'months_without_pairs': [int(m) for m in range(1, 13) if not len(part) or coverage.loc[sid].get(m, 0) == 0]}
    pairs['err_final'] = (pairs.final - pairs.station).abs()
    pairs['err_late'] = (pairs.late - pairs.station).abs()
    pairs['block'] = pairs.station_id + ':' + pairs.month.astype(str)
    blocks = {k: g for k, g in pairs.groupby('block')}
    names = list(blocks)
    station_groups = {k: g for k, g in pairs.groupby('station_id')}
    rng = np.random.default_rng(seed)

    def stats(frame):
        return {'mae_late_minus_final': float(frame.err_late.mean() - frame.err_final.mean()),
                'bias_final': float((frame.final - frame.station).mean()), 'bias_late': float((frame.late - frame.station).mean()),
                'sum_ratio_final': float(frame.final.sum() / frame.station.sum()), 'sum_ratio_late': float(frame.late.sum() / frame.station.sum())}

    def ci(groups, keys):
        samples = [stats(pd.concat([groups[k] for k in rng.choice(keys, size=len(keys))])) for _ in range(draws)]
        return {m: [float(np.percentile([s[m] for s in samples], 2.5)), float(np.percentile([s[m] for s in samples], 97.5))] for m in samples[0]}
    by_season = {s: {**stats(p), 'pairs': int(len(p))} for s, p in pairs.groupby('season')}
    result = {'period': '2025-01-01..2025-12-31 (UTC days; day kept only with 24 INMET hours)', 'pairs': int(len(pairs)),
              'stations_selected': len(meta['stations']), 'stations_with_pairs': int(pairs.station_id.nunique()),
              'stations': stations, 'pairs_by_station_and_month': coverage.to_dict(orient='index'),
              'point': stats(pairs), 'by_season': by_season,
              'ci95_station_month_blocks': ci(blocks, names), 'station_month_blocks': len(names),
              'ci95_whole_stations': ci(station_groups, list(station_groups)), 'stations_resampled': len(station_groups),
              'note': 'pairs are not independent; intervals resample station-month blocks or whole stations'}
    save_json(SOURCES / 'rain_inmet_2025_coverage.json', result)
    return result


if __name__ == '__main__':
    out = main()
    print(json.dumps({k: out[k] for k in ('pairs', 'stations_with_pairs', 'point', 'by_season', 'ci95_station_month_blocks', 'ci95_whole_stations')}, indent=1))
    for sid, s in out['stations'].items():
        print(sid, s)
