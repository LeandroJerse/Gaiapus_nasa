"""Transfer package of the US v2 publication for the Supabase operator.

Contents: the publication directory (catalog, index, runs/<id>/run.json + series.json.gz), the product summaries and
examples (yield results, including 2026 and KS 2027), the results contract, and MANIFEST.sha256.json listing every
file with bytes and sha256. No models, training matrices, sealed labels or credentials. The zip uses no compression
(series are already gzip); the package is written once and verified by re-reading every member.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/processed/us_training_v2'
PUBLISH, PRODUCT = OUT / 'publish', OUT / 'product'
FORBIDDEN = ('sealed', '.env', 'secret', '.joblib', '.pt', 'c1_score', 'development.jsonl')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(tag: str) -> list[tuple[str, Path]]:
    files = [(f'publication/{p.relative_to(PUBLISH).as_posix()}', p) for p in sorted(PUBLISH.rglob('*')) if p.is_file()]
    for name in (f'us_scenarios_summary_{tag}.csv.gz', f'us_scenarios_members_{tag}.csv.gz', f'us_scenarios_manifest_{tag}.json'):
        files.append((f'product/{name}', PRODUCT / name))
    files += [(f'product/examples/{p.name}', p) for p in sorted((PRODUCT / 'examples').glob('*.json'))]
    files.append(('contracts/us_yield_scenarios_contract_v1.schema.json', ROOT / 'data/frozen/us_yield_scenarios_contract_v1.schema.json'))
    files.append(('docs/2026-11-15-gaiapus-eua-v2-resultados.md', ROOT / 'docs/superpowers/plans/2026-11-15-gaiapus-eua-v2-resultados.md'))
    for arc, path in files:
        if any(word in arc.lower() for word in FORBIDDEN):
            raise ValueError(f'forbidden file in package: {arc}')
        if not path.is_file():
            raise FileNotFoundError(path)
    return files


def build(tag: str, out_dir: Path) -> dict:
    files = collect(tag)
    entries = []
    for arc, path in files:
        data = path.read_bytes()
        entries.append({'path': arc, 'bytes': len(data), 'sha256': sha(data)})
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    index = json.loads((PUBLISH / 'index.json').read_text(encoding='utf-8'))
    catalog = json.loads((PUBLISH / 'catalog.json').read_text(encoding='utf-8'))
    manifest = {'package': 'gaiapus-us-v2-publication', 'created_at': datetime.now(timezone.utc).isoformat(), 'science_commit': commit,
                'publication': {'as_of': index['as_of'], 'runs': len(index['publications']), 'places': len(catalog['places']),
                                'harvest_years': catalog['harvest_years'], 'revision': catalog['inputs'].get('publication_revision')},
                'files': len(entries), 'bytes': sum(e['bytes'] for e in entries), 'entries': entries,
                'excluded': 'models, training matrices, sealed labels, credentials, farmer data',
                'verify': 'sha256 of each extracted file must equal entries[].sha256; publication/ is validated with the APP tool (validate --dir publication)'}
    body = json.dumps(manifest, indent=1).encode('utf-8')
    version = sha(body)[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f'gaiapus-us-v2-publication-{version}.zip'
    with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_STORED) as z:
        z.writestr('MANIFEST.sha256.json', body)
        for arc, path in files:
            z.write(path, arc)
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for e in entries:
            if e['path'] not in names or sha(z.read(e['path'])) != e['sha256']:
                problems.append(e['path'])
    record = {'zip': str(zip_path), 'zip_sha256': sha(zip_path.read_bytes()), 'zip_bytes': zip_path.stat().st_size, 'manifest_sha256': sha(body),
              'files': len(entries), 'content_bytes': manifest['bytes'], 'science_commit': commit, 'members_failing_verification': problems}
    zip_path.with_suffix('.sha256.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    return record


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--out', default=str(Path.home() / 'Backups/gaiapus/entrega-eua'))
    a = ap.parse_args()
    print(json.dumps(build(a.tag, Path(a.out)), indent=1))
