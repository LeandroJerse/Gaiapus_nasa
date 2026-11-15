"""Assemble the self-contained integration package (dist/handoff/gaiapus-integracao and its zip).

Copies only what live serving needs: code, portable models, catalog, municipality locations,
water-projection evaluation files, frozen records and contracts. No research data, caches,
credentials or binaries other than the MLP weights.
"""
import shutil
from pathlib import Path

from data.ingest.water_balance_run import ROOT

TARGET = ROOT / 'dist/handoff/gaiapus-integracao'
FILES = ['data/raw/training_v1/locations.json', 'data/raw/training_v6/locations.json', 'data/raw/training_v7/locations.json',
         'data/processed/water_projection_v1/operational_limit.json', 'data/processed/water_projection_v1/backtest_by_lead.csv',
         'data/processed/water_projection_v1/climatology.json']
FOLDERS = {'data/ingest': '*.py', 'data/models': '*.py', 'data/serving': '*.py', 'data/frozen': '*.json',
           'contracts': '*.json', 'contracts/examples': '*.json', 'contracts/catalog': '*.json',
           'dist/gaiapus-models-v7': '*.json', 'dist/gaiapus-models-v7/models': '*'}
HANDOFF = ['README.md', 'requirements.txt', 'smoke_test.py', 'example_api.py', 'run_daily.ps1', 'run_daily.sh']


def main():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    for folder, pattern in FOLDERS.items():
        for path in sorted((ROOT / folder).glob(pattern)):
            if path.is_file():
                destination = TARGET / folder / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
    for name in FILES:
        destination = TARGET / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    for name in HANDOFF:
        shutil.copy2(ROOT / 'handoff' / name, TARGET / name)
    (TARGET / '.gitignore').write_text('.venv/\n__pycache__/\ndata/cache/\ndist/snapshots_live/\nlogs/\n.env\n', encoding='utf-8')
    archive = shutil.make_archive(str(ROOT / 'dist/release/gaiapus-integracao-v7'), 'zip', TARGET.parent, TARGET.name)
    files = [p for p in TARGET.rglob('*') if p.is_file()]
    print(f'{len(files)} files, {sum(p.stat().st_size for p in files) / 2 ** 20:.1f} MB -> {archive}')


if __name__ == '__main__':
    main()
