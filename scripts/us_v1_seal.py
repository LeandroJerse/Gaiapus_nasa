"""Seal the frozen US v1 experiment and guard Brazilian artifacts.

Commands:
  write   build data/frozen/us_training_v1_seal.json (create-once)
  verify  re-hash every sealed file; exit 1 on any divergence
  backup  write a zip of all sealed files to a directory outside OneDrive and verify it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEAL_PATH = ROOT / 'data/frozen/us_training_v1_seal.json'
EXPECTED = {
    'data/frozen/us_training_v1_protocol.json': '60f40fc6fd0f9feade9adfb392618923c79531bfe68bb640af812118ab7f096d',
    'data/processed/us_training_v1/test_score.json': '97689340eb5ea9769d65b98598ba2248f3d071eb124280375c61f171b45ddc81',
}
GROUPS = {
    'v1': [
        'data/raw/us_training_v1', 'data/processed/us_training_v1', 'data/models/research_us_v1',
        'data/frozen/us_training_v1_protocol.json',
        'data/ingest/us_data.py', 'data/ingest/us_features.py', 'data/ingest/us_train.py', 'data/ingest/gpu.py',
        'tests/test_us_data.py', 'tests/test_us_features.py', 'tests/test_us_train.py',
        'scripts/report_us_training.py',
        'docs/superpowers/plans/2026-11-15-gaiapus-eua-usda-treinamento.md',
        'docs/superpowers/plans/2026-11-15-gaiapus-eua-resultados.md',
    ],
    'br_guard': [
        *[f'data/models/research_v{i}' for i in range(1, 9)],
        'data/models/water_balance.py',
        'data/frozen/training_v8_protocol.json', 'data/frozen/training_v9_protocol.json',
        'data/frozen/prospective_v9_protocol.json', 'data/frozen/prospective_v9_protocol_versions.jsonl',
        'data/frozen/test_2022_2024_v7.json', 'data/frozen/v8_frozen_choices.json',
        'data/frozen/regional_rule_soy_mg_mt_g30.json', 'data/frozen/source_availability_v9.json',
    ],
}
SKIP_PARTS = {'__pycache__'}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path, entry: str) -> list[str]:
    p = root / entry
    if p.is_file():
        return [entry]
    if not p.is_dir():
        raise FileNotFoundError(entry)
    return sorted(f.relative_to(root).as_posix() for f in p.rglob('*') if f.is_file() and not SKIP_PARTS & set(f.parts))


def build_seal(root: Path, groups: dict[str, list[str]] = GROUPS) -> dict:
    root = Path(root)
    out = {'created_at': datetime.now(timezone.utc).isoformat(), 'groups': {}, 'entries': {}}
    for group, entries in groups.items():
        out['groups'][group] = {}
        for entry in entries:
            out['entries'][entry] = group
            for rel in _files(root, entry):
                out['groups'][group][rel] = {'sha256': sha_file(root / rel), 'bytes': (root / rel).stat().st_size}
    return out


def verify_seal(root: Path, seal: dict) -> list[str]:
    root = Path(root)
    problems = []
    for files in seal['groups'].values():
        for rel, meta in files.items():
            p = root / rel
            if not p.is_file() or sha_file(p) != meta['sha256']:
                problems.append(rel)
    sealed = {rel for files in seal['groups'].values() for rel in files}
    for entry in seal.get('entries', {}):
        try:
            current = _files(root, entry)
        except FileNotFoundError:
            continue
        problems.extend(rel for rel in current if rel not in sealed)
    return problems


def write_once(path: Path, obj: dict) -> None:
    with Path(path).open('x', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write('\n')


def write_backup(root: Path, seal: dict, zip_path: Path) -> None:
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for files in seal['groups'].values():
            for rel in files:
                z.write(Path(root) / rel, rel)
        z.writestr('SEAL.json', json.dumps(seal, indent=1))


def verify_backup(zip_path: Path, seal: dict) -> list[str]:
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        names = {}
        for info in z.infolist():
            names[info.filename] = info  # last duplicate wins, as an extractor would
        for files in seal['groups'].values():
            for rel, meta in files.items():
                if rel not in names or sha_bytes(z.read(names[rel])) != meta['sha256']:
                    problems.append(rel)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=['write', 'verify', 'backup'])
    ap.add_argument('--backup-dir', default=str(Path.home() / 'Backups/gaiapus'))
    a = ap.parse_args(argv)
    if a.command == 'write':
        for rel, expected in EXPECTED.items():
            if sha_file(ROOT / rel) != expected:
                raise SystemExit(f'Refusing to seal: {rel} does not match the v1 report hash')
        write_once(SEAL_PATH, build_seal(ROOT))
        print(f'sealed -> {SEAL_PATH}')
        return 0
    seal = json.loads(SEAL_PATH.read_text(encoding='utf-8'))
    if a.command == 'verify':
        problems = verify_seal(ROOT, seal)
        print(json.dumps({'divergent': problems, 'files': sum(len(v) for v in seal['groups'].values())}))
        return 1 if problems else 0
    zip_path = Path(a.backup_dir) / f"us_training_v1_seal_{sha_file(SEAL_PATH)[:12]}.zip"
    write_backup(ROOT, seal, zip_path)
    problems = verify_backup(zip_path, seal)
    record = {'zip': str(zip_path), 'zip_sha256': sha_file(zip_path), 'bytes': zip_path.stat().st_size,
              'seal_sha256': sha_file(SEAL_PATH), 'verified_members_divergent': problems,
              'verified_at': datetime.now(timezone.utc).isoformat()}
    zip_path.with_suffix('.verify.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    print(json.dumps(record))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
