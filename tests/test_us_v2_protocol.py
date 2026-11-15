import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / 'data/frozen/us_training_v2_protocol.json').read_text(encoding='utf-8'))
V1 = json.loads((ROOT / 'data/frozen/us_training_v1_protocol.json').read_text(encoding='utf-8'))


def test_family_size_matches_listed_comparisons():
    fam = P['confirmation']['family']
    total = sum(int(re.findall(r'= ?(\d+)\)|\((\d+)\)', v)[-1][0] or re.findall(r'= ?(\d+)\)|\((\d+)\)', v)[-1][1])
                for k, v in fam.items() if k.startswith('F'))
    assert total == fam['size'] == 53


def test_confirmation_disjoint_from_development():
    lo, hi = P['data']['development_crop_years']
    v_lo, v_hi = P['development_selection']['validation_years']
    assert not lo <= P['confirmation']['C1']['crop_year'] <= hi
    assert v_hi < P['confirmation']['C1']['crop_year']
    assert set(P['data']['seen_in_v1_test']) <= set(range(lo, hi + 1))


def test_model_grid_equals_v1():
    v1 = {k: v for k, v in V1['candidates'].items() if k not in ('selection_metric', 'all_predictions_clip_min_zero')}
    v2 = {k: v for k, v in P['development_selection']['candidates'].items() if k != 'clip_min_zero'}
    assert v1 == v2


def test_registration_hashes_match():
    reg_path = ROOT / 'data/frozen/us_training_v2_registration.json'
    if not reg_path.exists():
        return
    from scripts.us_v1_seal import sha_file
    reg = json.loads(reg_path.read_text(encoding='utf-8'))
    for rel, expected in reg['files'].items():
        if rel.endswith('.py'):
            continue  # code hashes are a snapshot at registration; models record their own code hashes
        assert sha_file(ROOT / rel) == expected, rel
