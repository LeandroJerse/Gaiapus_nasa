"""Inventory the water-balance delivery after tests and numerical validation."""
import json
from datetime import datetime,timezone
from data.ingest.water_balance_run import ROOT,OUT,sha,save_json


def main():
    validation=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    if validation['status']!='passed_numerical_checks_not_field_validation':raise ValueError('Validation incomplete')
    files=[p for p in OUT.iterdir() if p.is_file() and p.name!='delivery_manifest.json']
    files.extend(ROOT/p for p in ('data/models/water_balance.py','data/ingest/water_balance_run.py','data/ingest/freeze_water_balance.py','tests/test_water_balance.py','docs/superpowers/plans/gaiapus-19-balanco-hidrico-et0.md','docs/superpowers/plans/2026-11-14-water-balance-execution.md'))
    inventory={p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(files)}
    save_json(OUT/'delivery_manifest.json',{'created_at':datetime.now(timezone.utc).isoformat(),'version':'water_balance_v1','status':'numerically_checked_not_field_calibrated','files':inventory,'listed_total_bytes':sum(x['bytes'] for x in inventory.values())})
    print(f'Inventoried {len(inventory)} files; {sum(x["bytes"] for x in inventory.values())} bytes')


if __name__=='__main__':main()
