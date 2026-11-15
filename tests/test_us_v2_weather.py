import gzip
import json
from datetime import date

import pandas as pd
import pytest

from data.ingest import us_v2_weather as w


def _payload(days, values=None):
    values = values or {}
    params = {}
    for p in w.PARAMETERS:
        params[p] = {d: values.get((p, d), 1.0 if p != 'T2M_MAX' else 5.0) for d in days}
    return {'properties': {'parameter': params}, 'parameters': {p: {'units': 'x'} for p in w.PARAMETERS}}


def test_trailing_fill_is_not_yet_available_interior_is_gap():
    days = ['20261110', '20261111', '20261112', '20261113', '20261114']
    p = _payload(days, {('T2M_MAX', '20261111'): -999.0, ('T2M_MAX', '20261114'): -999.0, ('RH2M', '20261114'): -999.0})
    frame = w.payload_frame(p)
    q = w.quality(frame)
    t = q.set_index('variable')
    assert t.loc['T2M_MAX', 'last_valid'] == '2026-11-13'
    assert t.loc['T2M_MAX', 'interior_gaps'] == 1
    assert t.loc['T2M_MAX', 'trailing_unavailable'] == 1
    assert t.loc['RH2M', 'interior_gaps'] == 0


def test_missing_never_becomes_zero():
    p = _payload(['20250101', '20250102'], {('PRECTOTCORR', '20250102'): -999.0})
    frame = w.payload_frame(p)
    assert pd.isna(frame.loc[frame.date == '2025-01-02', 'PRECTOTCORR']).all()


def test_physical_violation_flagged():
    p = _payload(['20250101'], {('T2M_MIN', '20250101'): 10.0, ('T2M_MAX', '20250101'): 5.0})
    q = w.quality(w.payload_frame(p)).set_index('variable')
    assert q.loc['T2M_MIN', 'physical_violations'] == 1


def test_window_coverage_states():
    observed_through = date(2026, 11, 13)
    soy26 = w.window_status('IA', 'soy', 2026, observed_through)
    assert soy26['status'] == 'complete' and soy26['window_end'] == '2026-08-17'
    wheat27 = w.window_status('KS', 'wheat_winter', 2027, observed_through)
    assert wheat27['status'] == 'partial' and wheat27['window_start'] == '2026-10-02'
    assert wheat27['observed_days'] == 43
    soy27 = w.window_status('IA', 'soy', 2027, observed_through)
    assert soy27['status'] == 'not_started' and soy27['observed_days'] == 0


def test_copy_from_v1_refuses_hash_mismatch(tmp_path):
    src = tmp_path / 'v1'
    dst = tmp_path / 'v2'
    src.mkdir()
    (src / '19169.json.gz').write_bytes(gzip.compress(b'{}'))
    (src / '19169.manifest.json').write_text(json.dumps({'sha256': 'deadbeef'}))
    with pytest.raises(ValueError):
        w.copy_v1_cache('19169', src, dst)
    assert not (dst / '19169_2000-2024_v1.json.gz').exists()


def test_merge_chunks_rejects_overlap():
    a = w.payload_frame(_payload(['20241231']))
    b = w.payload_frame(_payload(['20241231', '20250101']))
    with pytest.raises(ValueError):
        w.merge_chunks([a, b])
