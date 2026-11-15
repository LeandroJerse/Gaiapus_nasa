from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from data.forecasting.weather_ensemble import DailyWeather, WeatherHistory
from data.ingest import us_v2_scenarios as s


def synthetic_history(start=date(2000, 1, 1), end=date(2026, 11, 13)):
    days, d, rng = [], start, np.random.default_rng(0)
    while d <= end:
        doy = d.timetuple().tm_yday
        t = 10 + 15 * np.sin((doy - 100) / 365 * 2 * np.pi)
        days.append(DailyWeather(d, float(rng.gamma(0.5, 4)), t - 5, t + 5, 18.0, 3.0, t - 8, 97.0, extras={'rh2m_pct': 65.0}))
        d += timedelta(days=1)
    return WeatherHistory('US:fips:19169', 42.0, 300.0, tuple(days), {'provider': 'synthetic'})


def observed_frame(h):
    return pd.DataFrame([{'date': x.date.isoformat(), 'PRECTOTCORR': x.precipitation_mm, 'T2M_MIN': x.tmin_c, 'T2M_MAX': x.tmax_c,
                          'RH2M': x.extras['rh2m_pct'], 'WS2M': x.wind_m_s} for x in h.days])


def test_pre_season_members_use_only_complete_years_before_issue():
    h = synthetic_history()
    rows, man = s.member_features(h, observed_frame(h), 'IA', 'maize', 2027, date(2026, 11, 15))
    assert man['observed_days'] == 0
    assert max(man['analog_years']) <= 2026 and len(rows) == len(man['analog_years'])
    assert set(s.F.WEATHER_COLUMNS) <= set(rows.columns)


def test_partial_season_prefix_is_observed_and_identical_across_members():
    h = synthetic_history()
    rows, man = s.member_features(h, observed_frame(h), 'KS', 'wheat_winter', 2027, date(2026, 11, 14))
    assert man['observed_days'] == 43  # 2026-10-02 .. 2026-11-13
    assert man['scenario_start'] == '2026-11-14'
    # first segment (60 days) mixes observed+scenario; members differ only by the scenario part
    assert rows.w0_rain_sum.nunique() > 1


def test_complete_window_returns_single_observed_member():
    h = synthetic_history()
    rows, man = s.member_features(h, observed_frame(h), 'IA', 'soy', 2026, date(2026, 11, 15))
    assert len(rows) == 1 and man['observed_days'] == 90 and rows.member_id.iloc[0] == 'observed'


def test_target_year_never_an_analog_for_itself():
    h = synthetic_history()
    rows, man = s.member_features(h, observed_frame(h), 'IA', 'maize', 2020, date(2019, 11, 6))
    assert all(y < 2020 for y in man['analog_years'])


def test_observed_gap_raises_not_zero_filled():
    h = synthetic_history()
    obs = observed_frame(h)
    obs.loc[obs.date == '2026-10-10', 'PRECTOTCORR'] = np.nan
    with pytest.raises(ValueError):
        s.member_features(h, obs, 'KS', 'wheat_winter', 2027, date(2026, 11, 14))
