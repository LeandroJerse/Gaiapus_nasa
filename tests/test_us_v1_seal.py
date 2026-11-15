import json
import zipfile

import pytest

from scripts import us_v1_seal as seal


def _tree(root):
    (root / 'a').mkdir()
    (root / 'a' / 'x.bin').write_bytes(b'abc')
    (root / 'b.txt').write_text('hello', encoding='utf-8')
    return {'v1': ['a', 'b.txt'], 'br_guard': []}


def test_seal_detects_change(tmp_path):
    groups = _tree(tmp_path)
    s = seal.build_seal(tmp_path, groups)
    assert seal.verify_seal(tmp_path, s) == []
    (tmp_path / 'a' / 'x.bin').write_bytes(b'abd')
    assert seal.verify_seal(tmp_path, s) == ['a/x.bin']


def test_seal_detects_missing_and_extra(tmp_path):
    groups = _tree(tmp_path)
    s = seal.build_seal(tmp_path, groups)
    (tmp_path / 'b.txt').unlink()
    (tmp_path / 'a' / 'new.bin').write_bytes(b'z')
    assert sorted(seal.verify_seal(tmp_path, s)) == ['a/new.bin', 'b.txt']


def test_seal_file_is_create_once(tmp_path):
    out = tmp_path / 'seal.json'
    seal.write_once(out, {'k': 1})
    with pytest.raises(FileExistsError):
        seal.write_once(out, {'k': 2})
    assert json.loads(out.read_text(encoding='utf-8')) == {'k': 1}


def test_backup_roundtrip_verifies_every_member(tmp_path):
    groups = _tree(tmp_path)
    s = seal.build_seal(tmp_path, groups)
    zpath = tmp_path / 'backup.zip'
    seal.write_backup(tmp_path, s, zpath)
    assert seal.verify_backup(zpath, s) == []
    # tamper one member
    with zipfile.ZipFile(zpath, 'a') as z:
        z.writestr('a/x.bin', b'tampered')
    assert 'a/x.bin' in seal.verify_backup(zpath, s)


def test_current_tree_matches_seal():
    path = seal.ROOT / 'data/frozen/us_training_v1_seal.json'
    if not path.exists():
        pytest.skip('seal not written yet')
    s = json.loads(path.read_text(encoding='utf-8'))
    assert seal.verify_seal(seal.ROOT, s) == []
