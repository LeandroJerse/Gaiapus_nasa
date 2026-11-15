import numpy as np
import pandas as pd
import pytest

from data.ingest import us_v2_train as t


def frame():
    rows = []
    for year in range(2010, 2021):
        for fips, hold in [('19001', False), ('19003', True)]:
            rows.append({'fips': fips, 'year': year, 'issue_kind': 'in_season', 'spatial_holdout': hold, 'yield_kg_ha': 9000.0,
                         'label_available_at': f'{year + 1}-05-15'})
    return pd.DataFrame(rows)


def test_fit_rows_exclude_validation_year_future_holdout_and_unpublished():
    f = frame()
    fit = t.fit_rows(f, 2020, 'in_season', '2020-08-04')
    assert fit.year.max() == 2019  # 2019 published 2020-05-15 <= issue
    assert not fit.spatial_holdout.any()
    early = t.fit_rows(f, 2020, 'in_season', '2020-03-01')
    assert early.year.max() == 2018  # 2019 not yet published at a March issue


def test_fit_rows_skip_missing_labels_and_eval_role():
    f = frame()
    f.loc[f.year == 2018, 'yield_kg_ha'] = np.nan
    f['role'] = np.where(f.year == 2017, 'eval', 'fit')
    fit = t.fit_rows(f, 2020, 'in_season', '2020-08-04')
    assert 2018 not in set(fit.year) and 2017 not in set(fit.year)


def test_rows_refuse_labels_after_2024():
    class Stub(t.RowBuilder):
        def __init__(self):
            pass
    b = Stub()
    b.counties = {'19001': {'state': 'IA', 'spatial_holdout': False, 'in_v1_population': True}}
    b._wx = pd.DataFrame([{'fips': '19001', 'crop': 'maize', 'year': 2025, 'window_start': '2025-05-06', 'window_end': '2025-08-03',
                           'latitude': 1.0, 'longitude': 1.0, **{c: 0.0 for c in t.F.WEATHER_COLUMNS}}]).set_index(['fips', 'crop', 'year'])
    b.hist = {('19001', 'maize'): pd.DataFrame({'year': [2020], 'yield_kg_ha': [9000.0]})}
    with pytest.raises(ValueError):
        b.rows('IA', 'maize', [2025], ['in_season'], with_label=True)
    out = b.rows('IA', 'maize', [2025], ['in_season'], with_label=False)
    assert 'yield_kg_ha' not in out.columns


def test_county_mae_balances_counties():
    f = pd.DataFrame({'fips': ['a', 'a', 'b'], 'yield_kg_ha': [10.0, 10.0, 10.0]})
    assert t.county_mae(f, np.array([0.0, 10.0, 10.0])) == pytest.approx(2.5)
