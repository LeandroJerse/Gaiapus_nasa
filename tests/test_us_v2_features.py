from datetime import date

import numpy as np
import pandas as pd
import pytest

from data.ingest import us_v2_features as f


def avail_fixed_month(month_day: str):
    """Toy availability: crop year Y published on <month_day> of Y+1."""
    def fn(crop: str, crop_year: int):
        return date.fromisoformat(f'{crop_year + 1}-{month_day}'), 'estimated'
    return fn


def series(years, values=None):
    values = values if values is not None else [8000.0 + 50 * (y - 2000) for y in years]
    return pd.DataFrame({'year': years, 'yield_kg_ha': values})


def test_history_uses_only_published_years():
    h = series(list(range(2005, 2025)))
    out = f.history_at_issue(h, 'maize', 2025, date(2025, 3, 1), avail_fixed_month('05-05'))
    # 2024 publishes 2025-05-05 > issue 2025-03-01 -> last published = 2023
    assert out['last_label_year'] == 2023 and out['hist_age'] == 2
    out2 = f.history_at_issue(h, 'maize', 2025, date(2025, 8, 4), avail_fixed_month('05-05'))
    assert out2['last_label_year'] == 2024 and out2['hist_age'] == 1


def test_never_uses_target_or_future_year_even_if_marked_available():
    h = series(list(range(2005, 2026)))
    out = f.history_at_issue(h, 'maize', 2025, date(2030, 1, 1), avail_fixed_month('01-01'))
    assert out['last_label_year'] == 2024


def test_suppressed_latest_year_resolved_per_county():
    h = series(list(range(2005, 2025)))
    h.loc[h.year == 2023, 'yield_kg_ha'] = np.nan  # published but suppressed
    out = f.history_at_issue(h, 'soy', 2025, date(2025, 3, 1), avail_fixed_month('05-05'))
    assert out['last_label_year'] == 2022 and out['hist_age'] == 3
    assert out['hist_suppressed_in_window'] == 1


def test_window_anchored_on_latest_published_year():
    h = series(list(range(1995, 2025)))
    a = f.history_at_issue(h, 'soy', 2025, date(2025, 8, 4), avail_fixed_month('05-05'), window=10)
    b = f.history_at_issue(h, 'soy', 2027, date(2026, 11, 15), avail_fixed_month('05-05'), window=10)
    assert a['hist_count'] == 10 and b['hist_count'] == 10
    assert b['hist_age'] == 3


def test_unknown_availability_is_not_published():
    def fn(crop, year):
        return (None, 'unknown') if year == 2024 else (date(year + 1, 2, 1), 'estimated')
    out = f.history_at_issue(series(list(range(2010, 2025))), 'maize', 2025, date(2025, 12, 1), fn)
    assert out['last_label_year'] == 2023
    assert out['availability_status'] == 'estimated'


def test_empty_history_returns_missing_not_zero():
    out = f.history_at_issue(series([]), 'maize', 2025, date(2025, 8, 1), avail_fixed_month('05-05'))
    assert out['hist_count'] == 0 and out['hist_last'] is None and out['hist_age'] is None


def test_issue_dates_per_kind():
    start, end = date(2025, 5, 6), date(2025, 8, 3)
    assert f.issue_date('in_season', start, end) == date(2025, 8, 4)
    assert f.issue_date('pre_6m', start, end) == date(2024, 11, 6)
    assert f.issue_date('pre_18m', start, end) == date(2023, 11, 6)


def test_group_id_shared_across_issue_kinds():
    assert f.group_id('19169', 'maize', 2020) == f.group_id('19169', 'maize', 2020)
    rows = pd.DataFrame({'fips': ['19169'] * 3, 'crop': ['maize'] * 3, 'year': [2020] * 3, 'issue_kind': ['in_season', 'pre_6m', 'pre_18m']})
    assert rows.apply(lambda r: f.group_id(r.fips, r.crop, r.year), axis=1).nunique() == 1
