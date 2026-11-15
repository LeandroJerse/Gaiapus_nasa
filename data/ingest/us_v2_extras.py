"""E3 (Nebraska irrigation) and E4 (Kansas winter-wheat calendar) feature builders. Development data only."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from data.ingest import us_v2_features as F
from data.ingest import us_v2_weather as wx

DRY_BASE = ['rain_sum', 'hot_days', 'max_dry_run']
IRR_COLUMNS = ['irr_share', 'irr_share_age', 'irr_missing'] + [f'dry_w{i}_{n}' for i in range(3) for n in DRY_BASE]
E4_COLUMNS = [f'e{i}_{n}' for i in range(3) for n in ['rain_sum', 't2m_min_mean', 't2m_max_mean', 'rh2m_mean', 'ws2m_mean', 'hot_days',
                                                      'frost_days', 'dry_days', 'max_dry_run', 'gdd0']] + ['e_sowing_offset_days', 'e_sowing_fallback']


# ---------------------------------------------------------------- E3
def irrigation_table(dev: pd.DataFrame) -> pd.DataFrame:
    """Irrigated share of HARVESTED area (GRAIN for maize) by fips/crop/crop year; requires both practices numeric."""
    h = dev[(dev.statistic == 'AREA HARVESTED') & dev.practice.isin(['IRRIGATED', 'NON-IRRIGATED'])]
    w = h.pivot_table(index=['fips', 'crop', 'year'], columns='practice', values='value', aggfunc='first')
    w = w.dropna(subset=['IRRIGATED', 'NON-IRRIGATED'], how='any') if {'IRRIGATED', 'NON-IRRIGATED'} <= set(w.columns) else w.iloc[0:0]
    total = w['IRRIGATED'] + w['NON-IRRIGATED']
    w = w[total > 0]
    return (w['IRRIGATED'] / (w['IRRIGATED'] + w['NON-IRRIGATED'])).rename('share').reset_index()


def irrigated_share(table: pd.DataFrame, fips: str, crop: str, target_year: int, issued_at: date) -> tuple[float | None, int | None]:
    t = table[(table.fips == fips) & (table.crop == crop) & (table.year < target_year)].sort_values('year', ascending=False)
    for r in t.itertuples():
        at, _ = F.label_available_at(crop, int(r.year))
        if at is not None and at <= issued_at:
            return float(r.share), int(target_year - r.year)
    return None, None


def add_irrigation(frame: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    vals = [irrigated_share(table, r.fips, r.crop, int(r.year), date.fromisoformat(r.issued_at)) for r in out.itertuples()]
    out['irr_share'] = [np.nan if s is None else s for s, _ in vals]
    out['irr_share_age'] = [np.nan if a is None else a for _, a in vals]
    out['irr_missing'] = out.irr_share.isna().astype(int)
    for i in range(3):
        for n in DRY_BASE:
            out[f'dry_w{i}_{n}'] = (1 - out.irr_share) * out[f'w{i}_{n}']
    return out


# ---------------------------------------------------------------- E4
def observed_sowing_date(progress: pd.DataFrame, harvest_year: int, issued_at: date) -> date | None:
    """State-level 50% planted date of the fall before harvest_year, from reports published (week_ending + 1 day) by issued_at."""
    p = progress[(progress.state == 'KS') & (progress.unit == 'PCT PLANTED') & (progress.level == 'STATE') & (progress.year == harvest_year)]
    p = p.dropna(subset=['value']).copy()
    p['we'] = pd.to_datetime(p.week_ending).dt.date
    p = p[[we + timedelta(days=1) <= issued_at for we in p.we]].sort_values('we')
    prev = None
    for r in p.itertuples():
        if r.value >= 50:
            if prev is None or prev.value >= 50:
                return r.we
            frac = (50 - prev.value) / (r.value - prev.value)
            return prev.we + timedelta(days=round((r.we - prev.we).days * frac))
        prev = r
    return None


def wheat_segments(sowing: date, cutoff: date) -> list[tuple[date, date]]:
    y = sowing.year
    est_end = date(y, 11, 30)
    winter_end = date(y + 1, 3, 1) - timedelta(days=1)
    return [(sowing, est_end), (est_end + timedelta(days=1), winter_end), (winter_end + timedelta(days=1), cutoff)]


def gdd0(w: pd.DataFrame) -> float:
    return float(np.maximum(0.0, (w.T2M_MIN + w.T2M_MAX) / 2).sum())


def _segment(w: pd.DataFrame, p: str) -> dict:
    out = {p + 'rain_sum': float(w.PRECTOTCORR.sum())}
    for col in F.FEATURE_VARS[1:]:
        out[p + col.lower() + '_mean'] = float(w[col].mean())
    out[p + 'hot_days'] = int((w.T2M_MAX > 32).sum())
    out[p + 'frost_days'] = int((w.T2M_MIN < 0).sum())
    out[p + 'dry_days'] = int((w.PRECTOTCORR < 1).sum())
    run = best = 0
    for dry in (w.PRECTOTCORR < 1):
        run = run + 1 if dry else 0
        best = max(best, run)
    out[p + 'max_dry_run'] = best
    out[p + 'gdd0'] = gdd0(w)
    return out


def add_wheat_calendar(frame: pd.DataFrame, progress: pd.DataFrame, reader=wx.read_county) -> pd.DataFrame:
    cache: dict[str, pd.DataFrame] = {}
    rows = []
    for r in frame.itertuples():
        issued = date.fromisoformat(r.issued_at)
        _, cutoff = wx.window('KS', 'wheat_winter', int(r.year))
        base_start, _ = wx.window('KS', 'wheat_winter', int(r.year))
        sow = observed_sowing_date(progress, int(r.year), issued) if r.issue_kind == 'in_season' else None
        fallback = sow is None
        sow = sow or base_start
        if r.fips not in cache:
            cache[r.fips] = reader(r.fips)
        w = cache[r.fips]
        feats = {'e_sowing_offset_days': (sow - base_start).days, 'e_sowing_fallback': int(fallback)}
        for i, (a, b) in enumerate(wheat_segments(sow, cutoff)):
            s = w[(w.date >= a.isoformat()) & (w.date <= b.isoformat())]
            if len(s) != (b - a).days + 1 or not np.isfinite(s[F.FEATURE_VARS].to_numpy(float)).all():
                raise ValueError('missing_weather_in_calendar_window')
            feats |= _segment(s, f'e{i}_')
        rows.append(feats)
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
