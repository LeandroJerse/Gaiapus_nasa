"""Verify immutable acquisition payloads and freeze the v2 research handoff."""
import csv
import json
from datetime import datetime,timezone
from pathlib import Path
from data.ingest.v2_common import RAW,OUT,sha,save_json


def main():
    for p in RAW.rglob('*.manifest.json'):
        meta=json.loads(p.read_bytes())
        if 'sha256' not in meta:continue
        if p.parent.name=='esa' and not p.name.startswith('items_'):
            target=p.with_suffix('').with_suffix('.tif')
        elif p.parent.name=='inmet':target=p.with_suffix('').with_suffix('.zip')
        else:target=p.with_name(p.name.removesuffix('.manifest.json'))
        if not target.exists() or sha(target)!=meta['sha256']:raise ValueError(f'Raw integrity mismatch: {p}')
    prior=json.loads(Path('data/processed/training_v1/delivery_manifest.json').read_bytes())
    for name,entry in prior['files'].items():
        target=Path(name)
        if name.startswith('docs/superpowers/plans/'):
            target=Path('docs/archive/training_v1')/target.name
        if sha(target)!=entry['sha256']:raise ValueError(f'Changed v1 artifact: {name}')
    paths=list(RAW.rglob('*'))+list(OUT.glob('*'))+list(Path('data/models/research_v2').glob('*'))
    paths+=list(Path('data/ingest').glob('*v2*.py'))+[Path('tests/test_training_v2.py')]
    paths+=list(Path('docs/archive/training_v1').glob('*.md'))
    paths += [Path('docs/superpowers/plans')/name for name in ['gaiapus-17-validacao-espacial-v2.md','gaiapus-18-resultados-v2.md','2026-11-14-gaiapus-v2-execution.md']]
    entries={}
    for p in sorted(set(paths)):
        if not p.is_file() or p.name=='delivery_manifest.json' or p.suffix=='.part':continue
        entry={'bytes':p.stat().st_size,'sha256':sha(p)}
        if p.suffix=='.csv' and p.parent==OUT:
            with p.open(encoding='utf-8-sig',newline='') as stream:entry['rows']=sum(1 for _ in csv.DictReader(stream))
        entries[p.as_posix()]=entry
    save_json(OUT/'delivery_manifest.json',{'frozen_at':datetime.now(timezone.utc).isoformat(),'version':'training_v2',
              'files':entries,'listed_total_bytes':sum(v['bytes'] for v in entries.values()),
              'v1_integrity_verified':True,'v1_document_snapshots':'docs/archive/training_v1',
              'status':'research_only','yield_test_evaluated':False,'crop_specific_mask':False,
              'local_campo_rain_gaps_preserved':True})
    print('Verified/frozen',len(entries),'files;',sum(v['bytes'] for v in entries.values()),'bytes')


if __name__=='__main__':main()
