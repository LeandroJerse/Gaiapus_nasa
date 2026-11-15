"""US v2 scenario adapter over data/forecasting/weather_ensemble.py (no competing generator).

For a crop window [start, end] and an issue date: observed days <= issue-1 come from the POWER series;
the rest of the window comes from every eligible analog-year block (climatological scenario, not a forecast).
Features per member use exactly us_v2_features.weather_features (training definitions).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from data.forecasting.weather_ensemble import DailyWeather, WeatherHistory, build_ensemble
from data.ingest import us_v2_features as F
from data.ingest import us_v2_weather as wx

SEED = 42


def weather_history(fips: str) -> WeatherHistory:
    main = wx.read_county(fips)
    aux, elevation = wx.read_aux(fips)
    m = main.merge(aux, on='date', how='left')
    val = lambda v: None if v is None or not np.isfinite(v) else float(v)
    days = tuple(DailyWeather(date.fromisoformat(r.date), val(r.PRECTOTCORR), val(r.T2M_MIN), val(r.T2M_MAX), val(r.ALLSKY_SFC_SW_DWN),
                              val(r.WS2M), val(r.T2MDEW), val(r.PS), extras={'rh2m_pct': val(r.RH2M)}) for r in m.itertuples())
    c = next(c for c in wx.counties() if c['fips'] == fips)
    return WeatherHistory(f'US:fips:{fips}', c['latitude'], elevation, days,
                          {'provider': 'NASA POWER', 'product': 'POWER daily point AG (PRECTOTCORR, T2M_MIN/MAX, ALLSKY, WS2M, T2MDEW, PS, RH2M)',
                           'chunks': 'data/raw/us_training_v2/power*/' + fips + '_*', 'availability': 'retrospective snapshot; observation date <= as_of enforced'})


def _member_frame(member) -> pd.DataFrame:
    return pd.DataFrame([{'date': d.simulated_date.isoformat(), 'PRECTOTCORR': d.precipitation_mm, 'T2M_MIN': d.tmin_c, 'T2M_MAX': d.tmax_c,
                          'RH2M': d.extras.get('rh2m_pct'), 'WS2M': d.wind_m_s, 'et0_mm': d.et0_mm,
                          'source_observation_date': d.source_observation_date.isoformat()} for d in member.days])


def member_features(history: WeatherHistory, observed: pd.DataFrame, state: str, crop: str, harvest_year: int, issued_at: date,
                    seed: int = SEED, return_daily: bool = False):
    """Feature rows (one per member) for the crop window of harvest_year as known at issued_at."""
    start, end = wx.window(state, crop, harvest_year)
    as_of = issued_at - timedelta(days=1)  # data through the day before issue
    obs_end = min(as_of, end)
    prefix = observed[(observed.date >= start.isoformat()) & (observed.date <= obs_end.isoformat())].sort_values('date') if obs_end >= start else observed.iloc[0:0]
    n_obs = max(0, (obs_end - start).days + 1)
    if len(prefix) != n_obs or (n_obs and not np.isfinite(prefix[F.FEATURE_VARS].to_numpy(float)).all()):
        raise ValueError('observed_prefix_incomplete')
    manifest = {'window_start': start.isoformat(), 'window_end': end.isoformat(), 'issued_at': issued_at.isoformat(), 'as_of': as_of.isoformat(),
                'observed_days': n_obs, 'seed': seed}
    if as_of >= end:
        feats = F.weather_features(prefix, start, end)
        manifest |= {'analog_years': [], 'scenario_start': None, 'members': 1}
        rows = pd.DataFrame([{'member_id': 'observed', 'analog_year': None, **feats}])
        return (rows, manifest, {'observed': prefix}) if return_daily else (rows, manifest)
    scen_start = max(start, as_of + timedelta(days=1))
    ens = build_ensemble(history, target_start=scen_start, target_end=end, as_of=datetime.combine(as_of, time(23, 59), tzinfo=timezone.utc), seed=seed)
    rows, daily = [], {}
    for m in ens.members:
        mf = _member_frame(m)
        full = pd.concat([prefix[['date'] + F.FEATURE_VARS], mf[['date'] + F.FEATURE_VARS]], ignore_index=True)
        rows.append({'member_id': m.member_id, 'analog_year': m.analog_year, **F.weather_features(full, start, end)})
        if return_daily:
            daily[m.member_id] = mf
    manifest |= {'analog_years': ens.manifest['analog_years'], 'scenario_start': scen_start.isoformat(), 'members': len(ens.members),
                 'coverage_class': ens.manifest['coverage_class'], 'ensemble_sha256': ens.manifest['content_sha256'],
                 'calendar_mapping': ens.manifest['calendar_mapping'], 'excluded_years': ens.manifest['excluded_years']}
    out = pd.DataFrame(rows)
    return (out, manifest, daily) if return_daily else (out, manifest)
