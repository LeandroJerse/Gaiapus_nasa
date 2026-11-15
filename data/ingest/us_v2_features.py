"""US v2 features: label availability replay (available_at <= issued_at) and pre-cutoff weather features.

Weather features use five POWER variables. Shortwave radiation is excluded because its measured latency
(75 days on 2026-11-14) makes it unavailable at an in-season issue on the cutoff day.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from data.ingest import us_v2_weather as wx

ROOT = Path(__file__).resolve().parents[2]
AVAILABILITY_PATH = ROOT / 'data/frozen/us_v2_availability.json'
OUT = ROOT / 'data/processed/us_training_v2'
FEATURE_VARS = ['PRECTOTCORR', 'T2M_MIN', 'T2M_MAX', 'RH2M', 'WS2M']
ISSUE_KINDS = ('in_season', 'pre_6m', 'pre_18m')
HIST_KEYS = ['hist_last', 'hist_mean3', 'hist_mean10', 'hist_trend', 'hist_count', 'hist_age', 'hist_suppressed_in_window', 'last_label_year']
Availability = Callable[[str, int], tuple['date | None', str]]


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(AVAILABILITY_PATH.read_text(encoding='utf-8'))['nass_county_estimates']


def label_available_at(crop: str, crop_year: int) -> tuple[date | None, str]:
    key = 'wheat_winter' if crop == 'wheat_winter' else 'maize|soy'
    for entry in _table()[key]:
        lo, hi = entry['crop_years']
        if lo <= crop_year <= hi:
            if entry['available_at'] is None:
                return None, entry['status']
            text = entry['available_at'].replace('{Y+1}', str(crop_year + 1)).replace('{Y}', str(crop_year))
            return date.fromisoformat(text), entry['status']
    return None, 'unknown'


def history_at_issue(history: pd.DataFrame, crop: str, target_year: int, issued_at: date,
                     availability: Availability = label_available_at, window: int = 10) -> dict:
    """History features from crop years published by issued_at and strictly before target_year.

    The window is anchored on the latest published crop year (published = release happened, even if the
    county value is suppressed); suppressed values are excluded but counted.
    """
    empty = {k: None for k in HIST_KEYS} | {'hist_count': 0, 'hist_suppressed_in_window': 0, 'availability_status': None}
    published, statuses = [], set()
    for year in sorted(set(int(y) for y in history.year)) if len(history) else []:
        if year >= target_year:
            continue
        at, status = availability(crop, year)
        if at is not None and at <= issued_at:
            published.append(year)
            statuses.add(status)
    if not published:
        return empty
    anchor = max(published)
    in_window = history[(history.year > anchor - window) & (history.year <= anchor) & history.year.isin(published)].sort_values('year')
    valid = in_window.dropna(subset=['yield_kg_ha'])
    worst = 'estimated' if 'estimated' in statuses else sorted(statuses)[0]
    if valid.empty:
        return empty | {'hist_suppressed_in_window': int(len(in_window)), 'availability_status': worst}
    y = valid.yield_kg_ha.to_numpy(float)
    years = valid.year.to_numpy(float)
    trend = float(np.polyval(np.polyfit(years - target_year, y, 1), 0)) if len(valid) >= 3 else float(y.mean())
    last_year = int(years[-1])
    return {'hist_last': float(y[-1]), 'hist_mean3': float(y[-3:].mean()), 'hist_mean10': float(y.mean()), 'hist_trend': max(0.0, trend),
            'hist_count': int(len(valid)), 'hist_age': int(target_year - last_year), 'hist_suppressed_in_window': int(len(in_window) - len(valid)),
            'last_label_year': last_year, 'availability_status': worst}


def history_lag(history: pd.DataFrame, target_year: int, lag: int, ceiling: int | None = None, window: int = 10) -> dict:
    """Fixed-lag comparator: crop years <= min(target_year - lag, ceiling); window anchored on that limit."""
    limit = target_year - lag if ceiling is None else min(target_year - lag, ceiling)
    def avail(crop, year):
        return (date(1900, 1, 1), 'fixed_lag') if year <= limit else (None, 'fixed_lag')
    return history_at_issue(history, 'any', target_year, date(1900, 1, 2), avail, window)


def issue_date(kind: str, window_start: date, window_end: date) -> date:
    if kind == 'in_season':
        return window_end + timedelta(days=1)
    months = {'pre_6m': 6, 'pre_18m': 18}[kind]
    y, m = window_start.year, window_start.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, min(window_start.day, 28))


def group_id(fips: str, crop: str, year: int) -> str:
    return f'{fips}:{crop}:{year}'


def weather_features(frame: pd.DataFrame, start: date, end: date) -> dict:
    """Three equal pre-cutoff segments; identical definitions to v1 minus radiation. Raises on any gap."""
    w = frame[(frame.date >= start.isoformat()) & (frame.date <= end.isoformat())].sort_values('date')
    expected = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if list(w.date) != expected:
        raise ValueError('missing_or_duplicate_day')
    x = w[FEATURE_VARS].to_numpy(float)
    if not np.isfinite(x).all():
        raise ValueError('missing_weather_value')
    if (w.PRECTOTCORR < 0).any() or (w.T2M_MIN > w.T2M_MAX).any() or ((w.RH2M < 0) | (w.RH2M > 100)).any() or (w.WS2M < 0).any():
        raise ValueError('physical_range')
    out = {}
    for i, idx in enumerate(np.array_split(np.arange(len(w)), 3)):
        s = w.iloc[idx]
        p = f'w{i}_'
        out[p + 'rain_sum'] = float(s.PRECTOTCORR.sum())
        for col in FEATURE_VARS[1:]:
            out[p + col.lower() + '_mean'] = float(s[col].mean())
        out[p + 'hot_days'] = int((s.T2M_MAX > 32).sum())
        out[p + 'frost_days'] = int((s.T2M_MIN < 0).sum())
        out[p + 'dry_days'] = int((s.PRECTOTCORR < 1).sum())
        run = best = 0
        for dry in (s.PRECTOTCORR < 1):
            run = run + 1 if dry else 0
            best = max(best, run)
        out[p + 'max_dry_run'] = best
    return out


WEATHER_COLUMNS = [f'w{i}_{n}' for i in range(3) for n in ['rain_sum', 't2m_min_mean', 't2m_max_mean', 'rh2m_mean', 'ws2m_mean', 'hot_days', 'frost_days', 'dry_days', 'max_dry_run']]


def build_weather_table(years: range = range(2001, 2027)) -> Path:
    rows, excluded = [], []
    for c in wx.counties():
        frame = wx.read_county(c['fips'])
        for crop in wx.CROPS_BY_STATE[c['state']]:
            for year in years:
                start, end = wx.window(c['state'], crop, year)
                try:
                    feats = weather_features(frame, start, end)
                except ValueError as exc:
                    excluded.append({'fips': c['fips'], 'crop': crop, 'year': year, 'reason': str(exc)})
                    continue
                rows.append({'fips': c['fips'], 'state': c['state'], 'crop': crop, 'year': year, 'latitude': c['latitude'], 'longitude': c['longitude'],
                             'window_start': start.isoformat(), 'window_end': end.isoformat(), **feats})
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'weather_features_power.csv.gz'
    pd.DataFrame(rows).to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
    wx.save_json(OUT / 'weather_features_power.manifest.json', {'rows': len(rows), 'excluded': excluded, 'sha256': wx.sha(path), 'variables': FEATURE_VARS,
                                                               'radiation_excluded_reason': 'POWER ALLSKY latency 75 days at 2026-11-14 retrieval'})
    print(json.dumps({'rows': len(rows), 'excluded': len(excluded)}))
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['weather'])
    ap.parse_args()
    build_weather_table()
