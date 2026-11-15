"""Guide 30: the only regional selection rule for soy in MG + MT (validation 2019-2021 only).

Implements docs/superpowers/plans/gaiapus-30-regra-regional-mg-mt.md literally:
1. Candidates on the fixed MG+MT set: fitted history (Ridge on trend and lagged yields),
   national Ridge 'site' and the national v7 MLP ensembles 'weather', 'hydro', 'site'.
   Existing walk-forward predictions only; no refit.
2. Each candidate is paired with the history on municipality/harvest/cutoff keys and must
   cover all of them. MAE: mean absolute error per municipal harvest (cutoffs equally
   weighted), then mean over harvests within each state, then 50% weight per state.
   Temporal and spatiotemporal sets separately; each state needs >= 10 municipalities and
   the three harvest years in each set.
3. Eligible: temporal MAE strictly below the history in each state and spatiotemporal MAE
   not above the history in each state. Winner: lowest MG+MT temporal MAE, ties within
   0.001 kg/ha broken by history > Ridge > MLP weather > hydro > site. Promote only if the
   two-sided 95% bootstrap interval (10,000 resamples, seed 42, municipality clusters drawn
   within each state, all years and cutoffs kept) of the MG+MT temporal MAE difference
   winner - history has an upper bound strictly below zero. Otherwise select the history;
   the runner-up is never tried.
Second-crop maize keeps the national Ridge. The 2022-2024 test is not opened here.
"""
import json
import subprocess
from datetime import datetime, timezone

from data.ingest.water_balance_run import ROOT, save_json, sha

PREDICTIONS = ROOT / 'data/processed/training_v7/predictions_soy.parquet'
OUT = ROOT / 'data/frozen'
STATES = ('31', '51')
YEARS = (2019, 2020, 2021)
HISTORY = 'history+history'
CANDIDATES = ('history+history', 'site+ridge', 'weather+mlp', 'hydro+mlp', 'site+mlp')  # also the tie-break order
MIN_MUNICIPALITIES = 10
TOLERANCE = 1e-3
DRAWS, SEED = 10_000, 42


def harvest_errors(frame):
    """Absolute error averaged over the cutoffs of each municipal harvest."""
    frame = frame.assign(abs_error=(frame.predicted_kg_ha - frame.yield_kg_ha).abs())
    return frame.groupby(['candidate', 'state_code', 'municipality_code', 'harvest_year'], as_index=False).abs_error.mean()


def weighted_mae(harvests):
    """Mean over harvests within each state, then 50% per state. Returns (combined, by_state)."""
    by_state = harvests.groupby('state_code').abs_error.mean()
    return float(by_state.reindex(STATES).mean()), {s: float(v) for s, v in by_state.items()}


def coverage(frame, history_keys):
    keys = set(map(tuple, frame[['municipality_code', 'harvest_year', 'horizon_days']].to_numpy()))
    return len(keys & history_keys) / len(history_keys)


def set_is_valid(frame):
    for state in STATES:
        part = frame[frame.state_code == state]
        if part.municipality_code.nunique() < MIN_MUNICIPALITIES or set(part.harvest_year) != set(YEARS):
            return False
    return True


def bootstrap(candidate, history, draws=DRAWS, seed=SEED):
    """Two-sided 95% interval of weighted MAE(candidate) - MAE(history), municipalities resampled within each state."""
    import numpy as np
    rng = np.random.default_rng(seed)
    paired = candidate.merge(history, on=['state_code', 'municipality_code', 'harvest_year'], suffixes=('_c', '_h'))
    paired['delta'] = paired.abs_error_c - paired.abs_error_h
    per_state = {}
    for state in STATES:
        part = paired[paired.state_code == state]
        groups = [g.delta.to_numpy() for _, g in part.groupby('municipality_code')]
        per_state[state] = groups
    point = float(np.mean([paired[paired.state_code == s].delta.mean() for s in STATES]))
    samples = np.empty(draws)
    for i in range(draws):
        means = []
        for state in STATES:
            groups = per_state[state]
            chosen = rng.integers(len(groups), size=len(groups))
            means.append(np.concatenate([groups[j] for j in chosen]).mean())
        samples[i] = np.mean(means)
    return point, float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))


def evaluate(predictions):
    region = predictions[predictions.state_code.isin(STATES) & predictions.harvest_year.isin(YEARS)].copy()
    region['candidate'] = region.sources + '+' + region.model
    region = region[region.candidate.isin(CANDIDATES)]
    table, eligible, scopes = [], [], {}
    for scope, held in (('temporal', False), ('spatiotemporal', True)):
        part = region[region.spatial_holdout == held]
        history_keys = set(map(tuple, part[part.candidate == HISTORY][['municipality_code', 'harvest_year', 'horizon_days']].to_numpy()))
        scopes[scope] = {'valid_set': set_is_valid(part[part.candidate == HISTORY]),
                         'municipalities': {s: int(part[(part.candidate == HISTORY) & (part.state_code == s)].municipality_code.nunique()) for s in STATES},
                         'harvests': {s: int(part[(part.candidate == HISTORY) & (part.state_code == s)][['municipality_code', 'harvest_year']].drop_duplicates().shape[0]) for s in STATES},
                         'years': sorted(int(y) for y in part.harvest_year.unique())}
        harvests = harvest_errors(part)
        for candidate in CANDIDATES:
            rows = part[part.candidate == candidate]
            combined, by_state = weighted_mae(harvests[harvests.candidate == candidate])
            table.append({'scope': scope, 'candidate': candidate, 'coverage': coverage(rows, history_keys),
                          'mae_mg_mt': combined, 'mae_mg': by_state.get('31'), 'mae_mt': by_state.get('51')})
    lookup = {(r['scope'], r['candidate']): r for r in table}
    for candidate in CANDIDATES[1:]:
        t, s = lookup[('temporal', candidate)], lookup[('spatiotemporal', candidate)]
        th, sh = lookup[('temporal', HISTORY)], lookup[('spatiotemporal', HISTORY)]
        checks = {'full_coverage': t['coverage'] == 1.0 and s['coverage'] == 1.0,
                  'valid_sets': scopes['temporal']['valid_set'] and scopes['spatiotemporal']['valid_set'],
                  'temporal_below_history_each_state': all(t[k] < th[k] for k in ('mae_mg', 'mae_mt')),
                  'spatial_not_above_history_each_state': all(s[k] <= sh[k] for k in ('mae_mg', 'mae_mt'))}
        for row in (t, s):
            row['checks'] = checks
        if all(checks.values()):
            eligible.append(candidate)
    decision = {'selected': HISTORY, 'winner': None, 'reason': 'no candidate passed the per-state checks', 'ci95': None}
    if eligible:
        order = {c: i for i, c in enumerate(CANDIDATES)}
        best = min(lookup[('temporal', c)]['mae_mg_mt'] for c in eligible)
        winner = min((c for c in eligible if lookup[('temporal', c)]['mae_mg_mt'] <= best + TOLERANCE), key=order.get)
        temporal = region[~region.spatial_holdout]
        harvests = harvest_errors(temporal)
        point, low, high = bootstrap(harvests[harvests.candidate == winner], harvests[harvests.candidate == HISTORY])
        promoted = high < 0
        decision = {'selected': winner if promoted else HISTORY, 'winner': winner, 'delta_vs_history_kg_ha': point, 'ci95': [low, high],
                    'reason': 'upper bound strictly negative' if promoted else 'interval upper bound not strictly negative; history kept'}
    return table, scopes, eligible, decision


def main():
    import pandas as pd
    table, scopes, eligible, decision = evaluate(pd.read_parquet(PREDICTIONS))
    code_version = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    OUT.mkdir(parents=True, exist_ok=True)
    record = {'frozen_at': datetime.now(timezone.utc).isoformat(), 'rule_document': 'docs/superpowers/plans/gaiapus-30-regra-regional-mg-mt.md',
              'rule_document_sha256': sha(ROOT / 'docs/superpowers/plans/gaiapus-30-regra-regional-mg-mt.md'),
              'code': 'data/ingest/regional_rule_g30.py', 'code_sha256': sha(ROOT / 'data/ingest/regional_rule_g30.py'),
              'git_head_before_freeze': code_version, 'crop': 'soy', 'region': 'MG (31) + MT (51)', 'validation_years': list(YEARS),
              'inputs': {str(PREDICTIONS.relative_to(ROOT)): sha(PREDICTIONS)},
              'keys': 'municipality_code, harvest_year, horizon_days', 'candidates': list(CANDIDATES),
              'metric': 'MAE per municipal harvest (cutoffs equally weighted) -> mean per state -> 50% per state',
              'bootstrap': {'draws': DRAWS, 'seed': SEED, 'cluster': 'municipality', 'stratified_by': 'state'},
              'sets': scopes, 'table': table, 'eligible': eligible, 'decision': decision,
              'maize2': 'national Ridge (site+ridge), no regional selection',
              'features': {'site+ridge': 'trend, lagged yields, weather, water balance, site', 'weather+mlp': 'trend lags + weather, cutoff as input',
                           'hydro+mlp': '+ water balance', 'site+mlp': '+ site', 'history+history': 'trend and lagged yields'},
              'availability': 'retrospective; publication vintages unverified', 'test_2022_2024_evaluated': False,
              'superseded_exploratory_rule': 'contracts/catalog/regional_rule_v7.json (commit 27c0b9d) is exploratory and not active'}
    save_json(OUT / 'regional_rule_soy_mg_mt_g30.json', record)
    print(pd.DataFrame([{k: v for k, v in r.items() if k != 'checks'} for r in table]).round(2).to_string(index=False))
    print('sets', json.dumps(scopes))
    print('eligible', eligible)
    print('decision', json.dumps(decision))


if __name__ == '__main__':
    from data.ingest.regional_rule_g30 import main as entry
    entry()
