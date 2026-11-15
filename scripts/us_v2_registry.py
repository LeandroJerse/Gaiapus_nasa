"""US v2 prospective registry: create-once record of every scenario issued by the product run (no backdating).

Each entry separates the real issue instant, the intended agricultural cutoff, the information used, the position of the
issue relative to harvest and to the label release, and the eligibility for operational evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from data.ingest import us_v2_features as F
from data.ingest import us_v2_weather as wx

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / 'data/processed/us_training_v2/product'
REGISTRY = ROOT / 'data/frozen/us_v2_prospective_registry'
ELIGIBILITY = {
    'pre_season': ('prospective_pre_season', True),
    'in_season_partial': ('prospective_in_season_partial', True),
    'post_harvest_pre_release': ('post_harvest_estimate_not_skill_evidence', False),
    'post_cutoff': ('retrospective_only', False),
}


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(tag: str) -> dict:
    summ = pd.read_csv(PRODUCT / f'us_scenarios_summary_{tag}.csv.gz', dtype={'fips': str})
    if 'error' in summ:
        summ = summ[summ.error.isna()]
    man_path = PRODUCT / f'us_scenarios_manifest_{tag}.json'
    man = json.loads(man_path.read_text())
    entries = []
    for r in summ.to_dict('records'):
        start, cutoff = wx.window(r['state'], r['crop'], int(r['year']))
        release, release_status = F.label_available_at(r['crop'], int(r['year']))
        kind, eligible = ELIGIBILITY[r['position_vs_season']]
        entries.append({
            'location_id': f"US:fips:{r['fips']}", 'crop_id': r['crop'], 'harvest_year': int(r['year']),
            'issued_at': man['issued_at'], 'intended_cutoff': cutoff.isoformat(), 'sowing_hypothesis': start.isoformat(),
            'issue_horizon': r['horizon'], 'days_issue_to_cutoff': (cutoff - date.fromisoformat(man['issued_at'][:10])).days,
            'information_used': {'weather_observed_through': r['weather_as_of'], 'observed_days_in_window': int(r['observed_days']),
                                 'scenario_members': int(r['members']),
                                 'last_label_year': None if pd.isna(r['last_label_year']) else int(r['last_label_year']),
                                 'label_availability_status': None if pd.isna(r['availability_status']) else r['availability_status'],
                                 'model': r['model'], 'model_kind': r['model_kind']},
            'position_vs_harvest': r['position_vs_season'],
            'label_release': {'date': release.isoformat() if release else None, 'status': release_status},
            'evaluation_kind': kind, 'operational_evaluation_eligible': eligible,
            'prediction': {'yield_p50_kg_ha': float(r['yield_p50']), 'scenario_p10': float(r['yield_p10']), 'scenario_p90': float(r['yield_p90']),
                           'predictive_p10': None if pd.isna(r['predictive_p10']) else float(r['predictive_p10']),
                           'predictive_p90': None if pd.isna(r['predictive_p90']) else float(r['predictive_p90']),
                           'reference_kg_ha': float(r['reference_kg_ha']), 'reference_model': r['reference_model']},
            'research_status_at_issue': r['scientific_status']})
    return {'registry': 'us_v2_prospective_registry', 'issued_at': man['issued_at'], 'no_backdating': True,
            'product_manifest_sha256': sha(man_path),
            'files': {k: v for k, v in man['files'].items()},
            'scoring_rule': 'each entry is scored once against the first USDA county estimate of its crop year; entries with '
                            'operational_evaluation_eligible=false are reported separately and never as evidence of pre-harvest skill',
            'entries': entries}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    a = ap.parse_args()
    doc = build(a.tag)
    REGISTRY.mkdir(parents=True, exist_ok=True)
    path = REGISTRY / f'batch_{a.tag}.json'
    with path.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, indent=1)
    kinds = pd.Series([e['evaluation_kind'] for e in doc['entries']]).value_counts().to_dict()
    print(json.dumps({'batch': path.name, 'sha256': sha(path), 'entries': len(doc['entries']), 'by_kind': kinds}))


if __name__ == '__main__':
    main()
