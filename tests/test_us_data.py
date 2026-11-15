import pandas as pd
import pytest

from data.ingest.us_data import normalize_record, choose_counties, split_name, history_features, validate_prediction_inputs


def record(**kwargs):
    row = dict(source_desc='SURVEY', agg_level_desc='COUNTY', state_alpha='IA', state_ansi='19', county_ansi='015', county_name='BOONE', year='2010', commodity_desc='CORN', class_desc='ALL CLASSES', util_practice_desc='GRAIN', prodn_practice_desc='ALL PRODUCTION PRACTICES', statisticcat_desc='YIELD', unit_desc='BU / ACRE', domain_desc='TOTAL', domaincat_desc='NOT SPECIFIED', freq_desc='ANNUAL', reference_period_desc='YEAR', Value='100.0', load_time='2026-01-01')
    return {**row, **kwargs}


def test_units_and_fips():
    r = normalize_record(record())
    assert r['fips'] == '19015'
    assert r['yield_kg_ha'] == pytest.approx(6276.76647469, abs=.00001)


@pytest.mark.parametrize('value', ['(D)', '(Z)', '(NA)', '', '--'])
def test_suppression_is_missing(value):
    r = normalize_record(record(Value=value))
    assert r['yield_kg_ha'] is None
    assert r['value_raw'] == value


@pytest.mark.parametrize('changes', [dict(source_desc='CENSUS'), dict(county_ansi='998'), dict(commodity_desc='WHEAT', class_desc='ALL CLASSES'), dict(prodn_practice_desc='IRRIGATED'), dict(util_practice_desc='SILAGE'), dict(unit_desc='TONS / ACRE'), dict(reference_period_desc='YEAR - AUG FORECAST')])
def test_filter_incompatible_records(changes):
    assert normalize_record(record(**changes)) is None


def test_test_partition_is_separate():
    assert split_name(2020) == 'development'
    assert split_name(2022) == 'sealed_test'
    assert split_name(2021) is None


def test_history_does_not_read_future_or_test_labels():
    history = pd.DataFrame([dict(year=y, yield_kg_ha=float(y)) for y in range(2000, 2025)])
    a = history_features(history, target_year=2024, history_ceiling=2020)
    history.loc[history.year >= 2021, 'yield_kg_ha'] = 999999
    assert history_features(history, 2024, 2020) == a
    assert a['hist_last'] == 2020
    assert a['hist_age'] == 4


def test_prediction_input_rejects_labels_and_after_cutoff():
    with pytest.raises(ValueError):
        validate_prediction_inputs(pd.DataFrame([dict(year=2022, yield_kg_ha=1)]))
    with pytest.raises(ValueError):
        validate_prediction_inputs(pd.DataFrame([dict(year=2022, feature_end='2022-08-02', cutoff='2022-08-01')]))


def test_selection_not_affected_by_test():
    rows = []
    for county in range(1, 20, 2):
        for crop in ['soy', 'maize']:
            for year in range(2001, 2016):
                rows.append(dict(state='IA', fips=f'19{county:03d}', crop=crop, year=year, yield_kg_ha=1000))
    frame = pd.DataFrame(rows)
    a = choose_counties(frame, per_state=5, holdout_count=1)
    extra = frame.assign(year=2024, yield_kg_ha=99999)
    assert choose_counties(pd.concat([frame, extra]), per_state=5, holdout_count=1) == a
