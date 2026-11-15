"""Copy representative responses from a daily snapshot into contracts/examples (schema 1.1.0)."""
import json
import sys
from pathlib import Path

from data.ingest.water_balance_run import ROOT, save_json

CASES = [('soy', '5107925', 'ready'), ('soy', '4100103', 'ready'), ('maize1', '3170206', 'ready'), ('wheat', None, 'outside_season'),
         ('rice', '4300406', 'ready'), ('bean1', None, 'ready'), ('bean2', None, 'before_first_cutoff'), ('coffee_arabica', '3100302', 'ready'),
         ('coffee_conilon', None, 'ready'), ('maize2', '5107925', 'before_first_cutoff')]


def main(snapshot):
    folder = Path(snapshot)
    written = []
    for crop, code, status in CASES:
        responses = json.loads((folder / 'yield' / f'{crop}.json').read_text(encoding='utf-8'))['responses']
        match = [r for r in responses if r.get('status') == status and (code is None or r['municipality_code'] == code)]
        if not match:
            continue
        item = match[0]
        name = f'yield-v7-{crop}-{item["municipality_code"]}-{item["as_of"]}-{status}.json'
        save_json(ROOT / 'contracts/examples' / name, item)
        written.append((name, item.get('estimate_kg_ha'), item['model']['name']))
    for row in written:
        print(row)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'dist/snapshots/2021-01-20')
