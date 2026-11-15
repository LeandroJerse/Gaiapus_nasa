import numpy as np
import pandas as pd
import pytest

from data.ingest import us_v2_confirm as c


def frame(n=40, gain=100.0):
    rng = np.random.default_rng(1)
    y = rng.normal(10000, 500, n)
    return pd.DataFrame({'fips': [f'{i:05d}' for i in range(n)], 'y': y, 'cand': y + rng.normal(0, 50, n), 'comp': y + rng.normal(0, 50, n) + gain})


def test_bootstrap_detects_clear_gain_and_uses_family_quantile():
    s = c.paired_bootstrap(frame(gain=800.0), 'cand', 'comp')
    assert s['diff'] < 0 and s['upper_adjusted'] < 0 and s['gain_pct'] > 0.05
    s0 = c.paired_bootstrap(frame(gain=0.0), 'cand', 'comp')
    assert s0['upper_adjusted'] > s0['ci95'][1] - 1e-9


def test_status_rules():
    good = {'counties': 40, 'gain_pct': 0.1, 'upper_adjusted': -5.0}
    assert c.status(good, 0.02, 100, False, True) == 'research_confirmed'
    assert c.status(good, -0.01, 100, False, True) == 'gain_not_confirmed'
    assert c.status(good, 0.02, 10, False, True) == 'underpowered'
    assert c.status(good, 0.02, 100, True, True) == 'reference_selected'
    assert c.status(None, None, 0, False, False) == 'not_executed'
    assert c.status({'counties': 40, 'gain_pct': 0.04, 'upper_adjusted': -5.0}, 0.1, 100, False, True) == 'gain_not_confirmed'


def test_open_requires_all_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'OUT', tmp_path)
    monkeypatch.setattr(c, 'CONSUMED', tmp_path / 'c1_consumed.json')
    with pytest.raises(ValueError):
        c.open_c1([], {})
    assert not (tmp_path / 'c1_consumed.json').exists()


def test_consumed_marker_is_create_once(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(c, 'OUT', tmp_path)
    monkeypatch.setattr(c, 'CONSUMED', tmp_path / 'c1_consumed.json')
    monkeypatch.setattr(c, 'SCORE', tmp_path / 'c1_score.json')
    (tmp_path / 'all_frozen.json').write_text(json.dumps({'files': {}}))
    (tmp_path / 'c1_consumed.json').write_text('{}')
    with pytest.raises(FileExistsError):
        c.open_c1([], {})
