import json

import numpy as np
import pandas as pd
import pytest

from data.ingest.us_train import county_mae, split_development, numeric_columns, verify_files, write_score_once, preprocessing


def test_spatial_and_temporal_fit_boundaries():
    f = pd.DataFrame([dict(year=y, spatial_holdout=h) for y in [2015, 2016, 2017, 2020, 2022] for h in [False, True]])
    fit, val = split_development(f)
    assert fit.year.max() == 2015
    assert not fit.spatial_holdout.any()
    assert set(val.year) == {2017, 2020}
    assert not val.spatial_holdout.any()


def test_county_metric_does_not_overweight_repeated_county():
    f = pd.DataFrame({'fips': ['A', 'A', 'B'], 'yield_kg_ha': [0., 0., 0.]})
    assert county_mae(f, np.array([1., 1., 9.])) == 5


def test_feature_allowlist_excludes_target_and_identifiers():
    f = pd.DataFrame({'hist_last': [1.], 'w0_rain_sum': [1.], 'year': [2015], 'latitude': [40.], 'longitude': [-90.], 'yield_kg_ha': [999.], 'spatial_holdout': [False], 'fips': ['19001']})
    assert numeric_columns(f) == ['hist_last', 'w0_rain_sum', 'year', 'latitude', 'longitude']


def test_preprocessing_is_fit_only_on_training():
    prep = preprocessing().fit(np.array([[0., 1.], [2., 3.]]))
    before = prep.named_steps['scale'].mean_.copy()
    prep.transform(np.array([[999999., 999999.]]))
    assert np.array_equal(before, prep.named_steps['scale'].mean_)


def test_modified_file_rejected(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{}')
    with pytest.raises(ValueError):
        verify_files(tmp_path, {'x.json': 'invalid'})


def test_score_cannot_be_overwritten(tmp_path):
    p = tmp_path / 'score.json'
    write_score_once(p, {'status': 'first'})
    with pytest.raises(FileExistsError):
        write_score_once(p, {'status': 'second'})
    assert json.loads(p.read_text())['status'] == 'first'


def test_consumed_test_blocks_training_even_without_score(tmp_path):
    from data.ingest.us_train import assert_test_unopened
    assert_test_unopened(tmp_path)
    (tmp_path / 'test_consumed.json').write_text('{}')
    with pytest.raises(ValueError):
        assert_test_unopened(tmp_path)
