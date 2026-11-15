from datetime import date

import numpy as np
import pandas as pd
import pytest

from data.ingest import us_v2_extras as x


def practice_records():
    rows = []
    for year in (2016, 2017, 2018):
        rows += [{'fips': '31185', 'crop': 'maize', 'year': year, 'statistic': 'AREA HARVESTED', 'practice': 'IRRIGATED', 'value': 60.0},
                 {'fips': '31185', 'crop': 'maize', 'year': year, 'statistic': 'AREA HARVESTED', 'practice': 'NON-IRRIGATED', 'value': 40.0}]
    rows.append({'fips': '31185', 'crop': 'maize', 'year': 2018, 'statistic': 'AREA PLANTED', 'practice': 'IRRIGATED', 'value': 999.0})
    rows.append({'fips': '31001', 'crop': 'maize', 'year': 2018, 'statistic': 'AREA HARVESTED', 'practice': 'IRRIGATED', 'value': None})
    rows.append({'fips': '31001', 'crop': 'maize', 'year': 2018, 'statistic': 'AREA HARVESTED', 'practice': 'NON-IRRIGATED', 'value': 50.0})
    return pd.DataFrame(rows)


def test_irrigated_share_uses_harvested_area_only_and_respects_availability():
    shares = x.irrigation_table(practice_records())
    s, age = x.irrigated_share(shares, '31185', 'maize', 2025, date(2025, 8, 4))
    assert s == pytest.approx(0.6) and age == 7
    # 2018 published 2019-05-15 (estimated): an issue in March 2019 sees 2017
    s2, age2 = x.irrigated_share(shares, '31185', 'maize', 2019, date(2019, 3, 1))
    assert age2 == 2


def test_missing_practice_is_nan_never_rainfed():
    shares = x.irrigation_table(practice_records())
    s, age = x.irrigated_share(shares, '31001', 'maize', 2025, date(2025, 8, 4))
    assert s is None and age is None
    f = pd.DataFrame({'fips': ['31001'], 'crop': ['maize'], 'year': [2025], 'issued_at': ['2025-08-04'],
                      **{c: [1.0] for c in ['w0_rain_sum', 'w1_rain_sum', 'w2_rain_sum', 'w0_hot_days', 'w1_hot_days', 'w2_hot_days',
                                            'w0_max_dry_run', 'w1_max_dry_run', 'w2_max_dry_run']}})
    out = x.add_irrigation(f, shares)
    assert np.isnan(out.irr_share.iloc[0]) and out.irr_missing.iloc[0] == 1
    assert np.isnan(out.dry_w0_rain_sum.iloc[0])


def progress():
    weeks = pd.date_range('2018-09-02', periods=8, freq='7D')
    vals = [1, 3, 7, 21, 41, 58, 62, 67]
    return pd.DataFrame({'state': 'KS', 'level': 'STATE', 'commodity': 'WHEAT', 'class': 'WINTER', 'unit': 'PCT PLANTED', 'year': 2019,
                         'week_ending': weeks.strftime('%Y-%m-%d'), 'value': vals})


def test_sowing_date_interpolates_50pct_with_published_reports_only():
    d = x.observed_sowing_date(progress(), 2019, date(2019, 3, 31))
    # 41% on 2018-09-30, 58% on 2018-10-07 -> 50% at 9/17 of the week
    assert d == date(2018, 9, 30) + pd.Timedelta(days=round(7 * 9 / 17)).to_pytimedelta()
    assert x.observed_sowing_date(progress(), 2019, date(2018, 10, 1)) is None  # 2018-09-30 report published 10-01; 50% not yet reached


def test_wheat_windows_contiguous_and_end_at_cutoff():
    segs = x.wheat_segments(date(2018, 10, 4), date(2019, 3, 30))
    assert segs[0][0] == date(2018, 10, 4) and segs[-1][1] == date(2019, 3, 30)
    for (a, b), (c, d) in zip(segs, segs[1:]):
        assert (c - b).days == 1


def test_gdd_base0_never_negative():
    w = pd.DataFrame({'T2M_MIN': [-10.0, 2.0], 'T2M_MAX': [-2.0, 12.0]})
    assert x.gdd0(w) == pytest.approx(7.0)
