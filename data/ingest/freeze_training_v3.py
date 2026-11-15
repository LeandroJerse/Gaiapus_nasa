"""Freeze reproducible hydrology-training artifacts without touching earlier versions."""
import json
from datetime import datetime,timezone
from data.ingest.train_hydro_v3 import ROOT,OUT,MODELS
from data.ingest.water_balance_run import sha,save_json


def main():
    status=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    if status['test_rows'] or status['future_event_rows_used'] or status['production_approved']:
        raise ValueError('Unexpected delivery state')
    files=[p for folder in (OUT,MODELS) for p in folder.iterdir() if p.is_file() and p.name!='delivery_manifest.json']
    files.extend(ROOT/p for p in ('data/ingest/hydro_training_features.py','data/ingest/train_hydro_v3.py','data/ingest/freeze_training_v3.py','data/models/water_balance.py','tests/test_hydro_training.py','docs/superpowers/plans/gaiapus-20-treinamento-com-deficit-hidrico.md','docs/superpowers/plans/2026-11-14-hydro-training-execution.md'))
    inventory={p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(files)}
    save_json(OUT/'delivery_manifest.json',{'version':'training_v3','created_at':datetime.now(timezone.utc).isoformat(),'status':'retrospective_research_not_operational','files':inventory,'listed_total_bytes':sum(x['bytes'] for x in inventory.values())})
    print(f'Inventoried {len(inventory)} files; {sum(x["bytes"] for x in inventory.values())} bytes')


if __name__=='__main__':main()
