"""Check the OPeNDAP subset against every cell already extracted from full global files."""
import csv
import json
from datetime import datetime, timezone

from data.ingest.water_balance_run import ROOT, save_json

FULL = ROOT / 'data/processed/imerg_operational_v1/extractions'
SUBSET = ROOT / 'data/processed/imerg_subset_v1'
FIELDS = ('rain_mm', 'valid_half_hours', 'cell_lon', 'cell_lat', 'units', 'product', 'production_time')


def compare(full_rows, subset_rows):
    """Both inputs map (municipality, day, run) to a row of strings."""
    report = {'cells_compared': 0, 'max_abs_rain_difference_mm': 0.0, 'mismatches': [], 'missing_in_subset': 0}
    for key, full in full_rows.items():
        subset = subset_rows.get(key)
        if subset is None:
            report['missing_in_subset'] += 1
            continue
        report['cells_compared'] += 1
        difference = abs(float(full['rain_mm'] or 'nan') - float(subset['rain_mm'] or 'nan')) if full['rain_mm'] and subset['rain_mm'] else (
            0.0 if full['rain_mm'] == subset['rain_mm'] else float('inf'))
        report['max_abs_rain_difference_mm'] = max(report['max_abs_rain_difference_mm'], difference)
        same = difference <= 1e-6 and int(full['valid_half_hours']) == int(subset['valid_half_hours']) \
            and abs(float(full['cell_lon']) - float(subset['cell_lon'])) <= 1e-5 \
            and abs(float(full['cell_lat']) - float(subset['cell_lat'])) <= 1e-5 \
            and all(full[f] == subset[f] for f in ('units', 'product', 'production_time'))
        if not same:
            report['mismatches'].append({'key': list(key), **{f: [full[f], subset[f]] for f in FIELDS}})
    return report


def main():
    full = {}
    for path in sorted(FULL.glob('*.csv')):
        with path.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                full[(row['municipality_code'], row['day_utc'], row['run'])] = row
    with (SUBSET / 'points.csv').open(encoding='utf-8', newline='') as stream:
        subset = {(r['municipality_code'], r['day_utc'], r['run']): r for r in csv.DictReader(stream)}
    report = compare(full, subset)
    report.update(checked_at=datetime.now(timezone.utc).isoformat(), full_file_cells=len(full),
                  full_file_granules=len({(k[1], k[2]) for k in full}), full_file_days=len({k[1] for k in full}),
                  subset_cells=len(subset), mismatch_count=len(report['mismatches']), mismatches=report['mismatches'][:20],
                  equivalent=not report['mismatches'] and not report['missing_in_subset'])
    save_json(SUBSET / 'equivalence_with_full_files.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'mismatches'}, indent=2))


if __name__ == '__main__':
    main()
