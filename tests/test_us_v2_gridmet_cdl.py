import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from data.ingest import us_v2_cdl as cdl
from data.ingest import us_v2_gridmet as g


# ---------------------------------------------------------------- mask rule

def test_cdl_availability_from_frozen_table():
    assert g.cdl_available_at(2024) == date(2025, 2, 27)
    assert g.cdl_available_at(2021) == date(2022, 3, 1)


def test_issue_date_is_window_end_plus_one():
    assert g.issued_at('KS', 'wheat_winter', 2025) == date(2025, 3, 31)
    assert g.issued_at('IA', 'maize', 2025) == date(2025, 8, 4)


def test_wheat_2025_uses_2024_mask_released_before_issue():
    issued = g.issued_at('KS', 'wheat_winter', 2025)
    assert g.select_mask_year(2025, issued) == 2024


def test_late_availability_falls_back_to_earlier_mask():
    def late(m):
        return date(2025, 4, 15) if m == 2024 else date(m + 1, 3, 1)
    assert g.select_mask_year(2025, date(2025, 3, 31), available_at=late) == 2021


@pytest.mark.parametrize('year', range(2001, 2026))
@pytest.mark.parametrize('crop,state', [('maize', 'IA'), ('soy', 'NE'), ('wheat_winter', 'KS')])
def test_mask_year_always_before_target_and_available(year, crop, state):
    issued = g.issued_at(state, crop, year)
    m = g.select_mask_year(year, issued)
    if m is not None:
        assert m <= year - 1
        assert m in g.MASK_YEARS
        assert g.cdl_available_at(m) <= issued


def test_no_mask_before_first_mask_year():
    assert g.select_mask_year(2006, g.issued_at('KS', 'wheat_winter', 2006)) is None
    # 2007 wheat issued 2007-03-31; 2006 CDL available 2007-03-01
    assert g.select_mask_year(2007, g.issued_at('KS', 'wheat_winter', 2007)) == 2006


def test_future_or_unavailable_mask_raises():
    with pytest.raises(ValueError):
        g.validate_mask_year(2025, 2025, date(2026, 8, 4))
    with pytest.raises(ValueError):
        g.validate_mask_year(2024, 2025, date(2025, 2, 1))  # released 2025-02-27
    g.validate_mask_year(2024, 2025, date(2025, 3, 31))


# ---------------------------------------------------------------- weights

def _counts():
    return pd.DataFrame({'fips': ['19001'] * 3 + ['19003'] * 2, 'crop': ['maize'] * 5, 'mask_year': [2021] * 5,
                         'cell_lat': [41.0, 41.0, 41.04166667, 41.0, 41.04166667],
                         'cell_lon': [-94.0, -93.95833333, -94.0, -94.0, -94.0], 'crop_pixels': [10, 30, 60, 1, 3]})


def test_weights_sum_to_one_per_county_crop_mask():
    w = cdl.weights_from_counts(_counts())
    sums = w.groupby(['fips', 'crop', 'mask_year']).weight.sum()
    assert np.allclose(sums.to_numpy(), 1.0)
    assert w.loc[(w.fips == '19001') & (w.crop_pixels == 60), 'weight'].item() == pytest.approx(0.6)


def test_weights_drop_zero_pixel_groups():
    c = _counts()
    c.loc[c.fips == '19003', 'crop_pixels'] = 0
    w = cdl.weights_from_counts(c)
    assert set(w.fips) == {'19001'}


def test_zero_pixel_county_falls_back_to_point_with_flag():
    w = cdl.weights_from_counts(_counts())
    cells, fallback = g.resolve_weights(w, '19999', 'maize', 2021)
    assert fallback is True and cells is None
    cells, fallback = g.resolve_weights(w, '19001', 'maize', None)
    assert fallback is True and cells is None
    cells, fallback = g.resolve_weights(w, '19001', 'maize', 2021)
    assert fallback is False and cells.weight.sum() == pytest.approx(1.0)


def test_weighted_series_and_nan_propagates():
    values = np.array([[1.0, 3.0], [2.0, np.nan]])  # days x cells
    out = g.weighted_series(values, np.array([0.25, 0.75]))
    assert out[0] == pytest.approx(2.5)
    assert np.isnan(out[1])


def test_weighted_series_never_exceeds_cell_range_by_rounding():
    counts = np.array([3.0, 63.0, 5.0])
    w = counts / counts.sum()
    assert (np.full((1, 3), 100.0) @ w)[0] > 100.0  # plain dot product overshoots by rounding
    values = np.full((3, 3), 100.0, dtype='float32')
    out = g.weighted_series(values, w)
    assert (out <= 100.0).all() and (out >= 100.0).all()


# ---------------------------------------------------------------- conversions

def test_unit_conversions():
    assert g.kelvin_to_celsius(273.15) == pytest.approx(0.0)
    assert g.WIND_10M_TO_2M == pytest.approx(4.87 / math.log(67.8 * 10 - 5.42))
    assert g.WIND_10M_TO_2M == pytest.approx(0.748, abs=1e-3)


def test_power_columns_mapping_keeps_nan():
    raw = pd.DataFrame({'date': ['2020-01-01', '2020-01-02'], 'pr': [0.0, np.nan], 'tmmn': [263.15, 270.0], 'tmmx': [283.15, np.nan],
                        'rmax': [90.0, 80.0], 'rmin': [50.0, np.nan], 'vs': [4.0, np.nan]})
    out = g.to_power_columns(raw)
    assert list(out.columns) == ['date', 'PRECTOTCORR', 'T2M_MIN', 'T2M_MAX', 'RH2M', 'WS2M']
    assert out.PRECTOTCORR.iloc[0] == 0.0 and np.isnan(out.PRECTOTCORR.iloc[1])
    assert out.T2M_MIN.iloc[0] == pytest.approx(-10.0)
    assert out.T2M_MAX.iloc[0] == pytest.approx(10.0)
    assert out.RH2M.iloc[0] == pytest.approx(70.0)
    assert out.WS2M.iloc[0] == pytest.approx(4.0 * g.WIND_10M_TO_2M)
    assert out.iloc[1, 1:].drop('T2M_MIN').isna().all()


def test_decode_fill_values_become_nan_not_zero():
    masked = np.ma.masked_array([1.0, 2.0, 0.0], mask=[False, True, False])
    out = g.masked_to_nan(masked)
    assert np.isnan(out[1]) and out[2] == 0.0 and out[0] == 1.0


# ---------------------------------------------------------------- grid / point

def test_cell_index_matches_gridmet_centres():
    lats = g.LAT0 - np.arange(585) * g.RES
    lons = g.LON0 + np.arange(1386) * g.RES
    r, c = g.cell_index(43.60833333, -96.72499997)
    assert lats[r] == pytest.approx(43.60833333) and lons[c] == pytest.approx(-96.72499997)


def test_point_picks_containing_cell():
    lats = np.array([41.125, 41.08333333, 41.04166667])
    lons = np.array([-94.04166667, -94.0, -93.95833333])
    # 41.064 is inside the cell centred at 41.0833 (edges 41.0625..41.1042); -93.979 inside -93.9583 (edges -93.9792..-93.9375)
    i, j = g.point_cell(41.064, -93.979, lats, lons)
    assert (i, j) == (1, 2)
    i, j = g.point_cell(41.0626, -94.0207, lats, lons)
    assert (i, j) == (1, 1)


def test_point_outside_grid_raises():
    with pytest.raises(ValueError):
        g.point_cell(45.0, -94.0, np.array([41.0, 40.95833333]), np.array([-94.0]))


def test_cdl_codes_are_primary_only():
    assert cdl.CROP_CODES == {'maize': 1, 'soy': 5, 'wheat_winter': 24}
