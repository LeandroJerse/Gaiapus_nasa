from datetime import date, timedelta

import pandas as pd
import pytest

from data.ingest.us_features import season_window, climate_features, build_row


def weather(start, count):
    return pd.DataFrame([{'date': (start + timedelta(days=i)).isoformat(), 'PRECTOTCORR': 2., 'T2M_MIN': 10., 'T2M_MAX': 25., 'RH2M': 60., 'WS2M': 2., 'ALLSKY_SFC_SW_DWN': 15.} for i in range(count)])


def test_winter_wheat_uses_previous_calendar_year():
    start, end = season_window('KS', 'wheat_winter', 2024)
    assert start == date(2023, 10, 2)
    assert (end - start).days == 179


def test_future_weather_does_not_change_features():
    start, end = season_window('IA', 'soy', 2022)
    frame = weather(start, 120)
    first = climate_features(frame, start, end)
    frame.loc[frame.date > end.isoformat(), 'PRECTOTCORR'] = 99999
    assert climate_features(frame, start, end) == first
    assert first['w0_rain_sum'] == 60


def test_missing_climate_is_not_zero():
    start = date(2022, 5, 20)
    frame = weather(start, 90)
    frame.loc[10, 'PRECTOTCORR'] = -999
    with pytest.raises(ValueError):
        climate_features(frame, start, start + timedelta(days=89))
    with pytest.raises(ValueError):
        climate_features(frame.drop(index=10), start, start + timedelta(days=89))


def test_test_row_has_no_yield_and_no_future_history():
    start, _ = season_window('IA', 'soy', 2024)
    h = pd.DataFrame([dict(year=2020, yield_kg_ha=1000), dict(year=2022, yield_kg_ha=9999)])
    row = build_row(dict(state='IA', fips='19169', spatial_holdout=False, latitude=42., longitude=-93.), 'soy', 2024, h, weather(start, 90), 2020)
    assert 'yield_kg_ha' not in row
    assert row['hist_last'] == 1000
