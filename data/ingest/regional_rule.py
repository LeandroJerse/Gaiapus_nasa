"""EXPLORATORY regional analysis for MG + MT (not an active selection rule).

Superseded by the guide 30 rule (data/ingest/regional_rule_g30.py), the only regional
selection in force: soy only, second-crop maize keeps the national Ridge. The output of
this module (contracts/catalog/regional_rule_v7.json, commit 27c0b9d) is kept as evidence.

Rule (declared before computing it, written to regional_rule.json with the input hashes):
1. Scope: municipalities in MG (31) and MT (51); crops soy and maize2.
2. Candidates: every model already fitted on the national cohort in the v7 run
   (references, history, Ridge, HGB and CUDA MLP per source set). No refit.
3. Evidence: walk-forward validation 2019-2021 only, rows of the region.
4. Switch from the national choice to a regional one only if, in the region,
   (a) the regional candidate has the lowest temporal MAE (municipalities used in training),
   (b) its paired gain against the national choice has a municipality-clustered 95% interval below zero,
   (c) there are at least 100 temporal validation harvests in the region, and
   (d) on held-out municipalities of the region its MAE is not above the national choice.
5. Otherwise keep the national choice. The 2022-2024 test is scored once, after this file is committed.
"""
import json
from datetime import datetime, timezone

from data.ingest.train_mlp_v4 import cluster_bootstrap_ci
from data.ingest.water_balance_run import ROOT, save_json, sha

V7 = ROOT / 'data/processed/training_v7'
MODELS = ROOT / 'data/models/research_v7'
REGION = ('31', '51')
CROPS = ('soy', 'maize2')
MIN_HARVESTS = 100


def candidate_table(predictions):
    region = predictions[predictions.state_code.isin(REGION)].copy()
    region['abs_error'] = (region.predicted_kg_ha - region.yield_kg_ha).abs()
    region['candidate'] = region.sources + '+' + region.model
    return region


def decide(region, national):
    """Return the decision record for one crop; `national` is the national champion name."""
    temporal = region[~region.spatial_holdout]
    spatial = region[region.spatial_holdout]
    mae = temporal.groupby('candidate').abs_error.mean().sort_values()
    mae_spatial = spatial.groupby('candidate').abs_error.mean()
    best = mae.index[0]
    harvests = int(temporal[temporal.candidate == national].sample_id.nunique())
    record = {'national_choice': national, 'regional_best': best, 'validation_harvests_region': harvests,
              'mae_region_temporal': {k: round(float(v), 1) for k, v in mae.items()},
              'mae_region_spatiotemporal': {k: round(float(v), 1) for k, v in mae_spatial.items()},
              'held_out_harvests_region': int(spatial[spatial.candidate == national].sample_id.nunique())}
    if best == national:
        return {**record, 'regional_choice': national, 'switched': False, 'reason': 'national choice is already the best in the region'}
    a = temporal[temporal.candidate == best].sort_values(['sample_id', 'horizon_days'])
    b = temporal[temporal.candidate == national].sort_values(['sample_id', 'horizon_days'])
    delta = a.abs_error.to_numpy() - b.abs_error.to_numpy()
    low, high = cluster_bootstrap_ci(delta, a.municipality_code.to_numpy(), draws=2000)
    checks = {'interval_below_zero': high < 0, 'enough_harvests': harvests >= MIN_HARVESTS,
              'not_worse_out_of_training': bool(mae_spatial.get(best, float('inf')) <= mae_spatial.get(national, float('inf')))}
    switched = all(checks.values())
    return {**record, 'paired_delta_kg_ha': round(float(delta.mean()), 1), 'ci95': [round(low, 1), round(high, 1)], 'checks': checks,
            'regional_choice': best if switched else national, 'switched': switched,
            'reason': 'all conditions met' if switched else 'failed: ' + ', '.join(k for k, v in checks.items() if not v)}


def main():
    import pandas as pd
    decisions, inputs = {}, {}
    for crop in CROPS:
        info = json.loads((V7 / f'validation/{crop}/cohort.json').read_text(encoding='utf-8'))
        path = V7 / f'predictions_{crop}.parquet'
        inputs[str(path.relative_to(ROOT))] = sha(path)
        decisions[crop] = decide(candidate_table(pd.read_parquet(path)), info['champion'])
        print(crop, {k: v for k, v in decisions[crop].items() if not k.startswith('mae_')})
    save_json(MODELS / 'regional_rule.json', {
        'frozen_at': datetime.now(timezone.utc).isoformat(), 'rule': __doc__, 'region': {'states': list(REGION), 'name': 'MG + MT (MVP)'},
        'min_validation_harvests': MIN_HARVESTS, 'inputs': inputs, 'decisions': decisions,
        'test_2022_2024_evaluated': False})


if __name__ == '__main__':
    from data.ingest.regional_rule import main as entry
    entry()
