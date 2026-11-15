"""Inventory the actual pilot, catalog, code and release-time audit."""
from datetime import datetime,timezone
from data.ingest.imerg_operational import RAW,OUT,ROOT
from data.ingest.water_balance_run import sha,save_json


def main():
    files=[p for folder in (RAW,OUT) for p in folder.rglob('*') if p.is_file() and p.name!='delivery_manifest.json']
    files.extend(ROOT/p for p in ('data/ingest/imerg_operational.py','data/ingest/operational_availability.py','data/ingest/report_imerg_operational.py','data/ingest/freeze_imerg_operational.py','tests/test_operational_availability.py','docs/superpowers/plans/gaiapus-21-imerg-operacional-e-disponibilidade.md','docs/superpowers/plans/2026-11-14-operational-imerg-execution.md'))
    inventory={p.relative_to(ROOT).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(files)}
    save_json(OUT/'delivery_manifest.json',{'version':'imerg_operational_v1_pilot','created_at':datetime.now(timezone.utc).isoformat(),
                                         'production_approved':False,'files':inventory,'listed_total_bytes':sum(r['bytes'] for r in inventory.values())})
    print(f'{len(inventory)} files; {sum(r["bytes"] for r in inventory.values())} bytes')


if __name__=='__main__':main()
