"""Audit of every prior exposure to PAM 2025 yields before the v8 2025 evaluation (review item 1).

Scans the project data folders for PAM 2025 values (SIDRA payloads with period 2025, tables with
harvest_year 2025), lists live snapshots whose estimates were computed with 2025 lags (harvest
2027 decisions of the live job), records the known debugging print, and checks that none of
these reached the inputs of the v8 development or of the 2025 predict stage.
"""
import json
from pathlib import Path

from data.ingest.water_balance_run import ROOT, save_json, sha

OUT = ROOT / 'data/processed/training_v8/pam2025_exposure.json'
SCAN = ['data/raw', 'data/cache', 'data/processed', 'dist', 'logs']
SKIP = ['data/raw/v8_eval_2025/power']  # weather only
MIN_LAG_COFFEE = {'pre_flowering': 3, 'post_flowering': 2, 'grain_filling': 2}
DEBUG_PRINT = {'process': 'interactive debugging of the live feature builder (this session, before the v8 plan)',
               'crops': ['coffee_arabica', 'coffee_total_mg'], 'municipality_code': '3170206', 'harvest_year': 2025,
               'value_kg_ha': 1920.0, 'context': 'printed history dict {2021: 1780, 2022: 1682, 2023: 1800, 2024: 1920, 2025: 1920}',
               'used_in': 'nothing: no selection, feature, matrix or threshold was computed from it'}


def sidra_2025(payload):
    rows = payload[1:] if isinstance(payload, list) and payload and isinstance(payload[0], dict) and 'D3C' in payload[0] else []
    return [r for r in rows if str(r.get('D3C')) == '2025' or str(r.get('D3N', '')).startswith('2025')]


def scan_files():
    import pandas as pd
    found = []
    for folder in SCAN:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob('*'):
            rel = path.relative_to(ROOT).as_posix()
            if not path.is_file() or any(rel.startswith(s) for s in SKIP) or path.stat().st_size > 400 * 2 ** 20:
                continue
            try:
                if path.suffix == '.json' and not rel.startswith('dist/snapshots'):
                    hits = sidra_2025(json.loads(path.read_text(encoding='utf-8')))
                    if hits:
                        found.append({'file': rel, 'kind': 'SIDRA payload with period 2025', 'rows': len(hits),
                                      'municipalities': sorted({r.get('D1C') for r in hits}),
                                      'crops': sorted({r.get('D4N') or r.get('D5N') or '' for r in hits})})
                elif path.suffix in ('.parquet', '.csv'):
                    frame = pd.read_parquet(path, columns=None) if path.suffix == '.parquet' else pd.read_csv(path, low_memory=False)
                    year = next((c for c in ('harvest_year', 'year') if c in frame), None)
                    if year and 'yield_kg_ha' in frame:
                        late = frame[(pd.to_numeric(frame[year], errors='coerce') == 2025) & frame.yield_kg_ha.notna()]
                        if len(late):
                            code = late.municipality_code.astype(str) if 'municipality_code' in late else None
                            found.append({'file': rel, 'kind': 'table with 2025 yields', 'rows': len(late),
                                          'municipalities': sorted(code.unique().tolist()) if code is not None else None,
                                          'crops': sorted(late.crop.astype(str).unique().tolist()) if 'crop' in late else None})
            except (ValueError, UnicodeDecodeError, OSError):
                continue
    return found


def snapshots():
    """Live estimates whose inputs include PAM 2025 (lag Y-2 of harvest 2027, or a coffee milestone allowing lag 2)."""
    out = {}
    for path in sorted((ROOT / 'dist').rglob('snapshots*/**/yield/*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        for r in payload.get('responses', []):
            year = r.get('harvest_year')
            if r.get('status') != 'ready' or not year:
                continue
            uses = year - 2 == 2025 and (not r['crop'].startswith('coffee') or MIN_LAG_COFFEE.get(r.get('milestone'), 3) <= 2)
            uses = uses or (r['crop'].startswith('coffee') and year - 3 == 2025)
            if uses:
                key = path.relative_to(ROOT).as_posix()
                item = out.setdefault(key, {'crop': r['crop'], 'ready_estimates_using_pam2025': 0, 'municipalities': set(),
                                            'model_types': set(), 'harvest_years': set()})
                item['ready_estimates_using_pam2025'] += 1
                item['municipalities'].add(r['municipality_code'])
                item['model_types'].add(r['model']['type'])
                item['harvest_years'].add(year)
    return {k: {**v, 'municipalities': len(v['municipalities']), 'model_types': sorted(v['model_types']),
                'harvest_years': sorted(v['harvest_years']),
                'note': 'reference models return a published lag: the estimate may equal a PAM 2025 value'} for k, v in out.items()}


def evaluation_inputs():
    """The inputs of the v8 development and of the 2025 predict stage, with their latest harvest."""
    import pandas as pd
    from data.ingest.evaluate_2025_v8 import label_history, training_frame
    report = {}
    for crop in ('soy', 'maize2', 'coffee_arabica', 'coffee_total_mg', 'coffee_conilon'):
        frame = training_frame(crop)
        history = label_history(crop)
        report[crop] = {'training_last_harvest': int(frame.harvest_year.max()),
                        'lag_history_last_harvest': max(y for h in history.values() for y in h),
                        'newest_lag_year_in_training_rows': int((frame.harvest_year - 2).max())}
    selection = {}
    for path in (ROOT / 'data/processed/training_v8').glob('*/predictions.parquet'):
        t = pd.read_parquet(path, columns=['harvest_year'])
        selection[path.parent.name] = int(t.harvest_year.max())
    return report, selection


def main():
    files = scan_files()
    live = snapshots()
    inputs, development = evaluation_inputs()
    clean = all(v['training_last_harvest'] <= 2023 and v['lag_history_last_harvest'] <= 2024 for v in inputs.values()) \
        and all(y <= 2021 for y in development.values())
    result = {
        'files_with_pam2025': files,
        'live_snapshots_using_pam2025': live,
        'live_job': {'process': 'data/serving/daily_job.py via providers.LiveProvider.history -> live_features.fetch_pam_history',
                     'storage': 'PAM values kept in memory per run (no PAM cache on disk); estimates written to dist/snapshots_live',
                     'purpose': 'lags of harvest 2027 decisions (live job of the 2026/27 season)',
                     'separation': 'the 2025 predict stage reads labels only through evaluate_2025_v8.label_history (v6/v7 files <= 2024) '
                                   'and refuses any 2025 year; it never reads dist/ or the live provider'},
        'debug_print': DEBUG_PRINT,
        'margins_round': {'process': 'data/ingest/margins.py (guide 11, 2026-11-13, before v8)',
                          'files': ['data/raw/margins/pam.json', 'data/raw/margins/pam839.json', 'data/raw/margins/pam_history.json',
                                    'data/processed/margins/pam_history.csv'],
                          'municipalities': ['3170206 Uberlandia', '5107925 Sorriso'], 'crops': ['soy', 'maize total', 'maize 1st', 'maize 2nd'],
                          'used_in': 'margin tables use 2024 only; no v6/v7/v8 cohort, matrix, selection or threshold reads these files'},
        'v8_development_last_harvest_by_scope': development,
        'v8_2025_inputs': inputs,
        'reached_v8_inputs_or_selection': not clean,
        'classification': 'evaluation with documented prior exposure; no known influence on selection' if clean
                          else 'exploratory: PAM 2025 reached v8 inputs; prospective confirmation required',
    }
    save_json(OUT, result)
    return result


if __name__ == '__main__':
    out = main()
    print(json.dumps({k: out[k] for k in ('files_with_pam2025', 'live_snapshots_using_pam2025', 'reached_v8_inputs_or_selection',
                                          'classification', 'v8_2025_inputs')}, indent=1, default=str))
