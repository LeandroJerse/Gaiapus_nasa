"""Freeze file inventory and verify acquisition manifests for the training handoff."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def main():
    raw = Path('data/raw/training_v1')
    out = Path('data/processed/training_v1')
    for path in raw.glob('*.manifest.json'):
        metadata = json.loads(path.read_bytes())
        target = path.with_name(path.name.removesuffix('.manifest.json'))
        if digest(target) != metadata['sha256']:
            raise ValueError(f'Changed source: {target}')
    inmet = json.loads((raw / 'inmet/manifest.json').read_bytes())
    if digest(raw / 'inmet/2024.zip') != inmet['sha256']:
        raise ValueError('Changed INMET archive')
    candidates = list(raw.rglob('*')) + list(out.glob('*')) + list(Path('data/models/research_v1').glob('*'))
    candidates += [Path('data/ingest') / f'{name}.py' for name in
                   ('training_foundation', 'training_baseline', 'soil_context', 'station_reference', 'freeze_training_delivery')]
    candidates += list(Path('data/templates').glob('*.csv'))
    candidates += list(Path('tests').glob('test_training*.py'))
    candidates += [p for p in Path('docs/superpowers/plans').glob('gaiapus-*.md') if p.name[8:10] in ('13', '14', '15', '16')]
    paths = sorted({p for p in candidates if p.is_file() and p.name != 'delivery_manifest.json' and p.suffix != '.part'})
    files = {}
    for path in paths:
        entry = {'bytes': path.stat().st_size, 'sha256': digest(path)}
        if path.suffix == '.csv' and path.parent == out:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                entry['data_rows'] = sum(1 for _ in csv.DictReader(stream))
        files[path.as_posix()] = entry
    result = {'frozen_at': datetime.now(timezone.utc).isoformat(), 'dataset_version': 'training_v1',
              'scope': 'MG_MT_soy_maize2_research', 'files': files,
              'new_raw_bytes': sum(p.stat().st_size for p in raw.rglob('*') if p.is_file()),
              'listed_total_bytes': sum(r['bytes'] for r in files.values()),
              'authorized_download_ceiling_bytes': 300_000_000_000,
              'status': 'research_ready_NOT_production_validated'}
    (out / 'delivery_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
