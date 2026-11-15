"""Score v8 development predictions (folds 2019-2021) and apply the registered promotion criteria."""
import argparse
import json

from data.ingest.train_v8 import OUT, PROTOCOL, SCOPES
from data.ingest.water_balance_run import save_json, write_csv

SIMPLE = ('ref_lag_mean', 'history', 'ref_cycle_mean', 'ref_last_available', 'ref_same_phase', 'ref_biennial_mean')


def harvest_table(table):
    """One row per candidate x harvest: absolute error averaged over the cutoffs."""
    import numpy as np
    t = table.assign(abs_error=np.abs(table.predicted_kg_ha - table.yield_kg_ha), error=table.predicted_kg_ha - table.yield_kg_ha)
    return t.groupby(['candidate', 'sample_id', 'municipality_code', 'state_code', 'harvest_year', 'spatial_holdout'], as_index=False).agg(
        abs_error=('abs_error', 'mean'), error=('error', 'mean'), yield_kg_ha=('yield_kg_ha', 'first'))


def summary(h, weighting):
    import numpy as np
    by_state = h.groupby('state_code').abs_error.mean()
    mae = float(by_state.mean()) if weighting == 'state50' else float(h.abs_error.mean())
    return {'mae_kg_ha': mae, 'rmse_kg_ha': float(np.sqrt((h.error ** 2).mean())), 'bias_kg_ha': float(h.error.mean()),
            'mae_pct_of_mean_yield': 100 * mae / float(h.yield_kg_ha.mean()), 'p90_abs_error_kg_ha': float(np.percentile(h.abs_error, 90)),
            'harvests': int(h.sample_id.nunique()), 'municipalities': int(h.municipality_code.nunique()),
            **{f'mae_{s}': float(v) for s, v in by_state.items()}}


def bootstrap(a, b, weighting, draws=2000, seed=42):
    """Two-sided 95% interval of MAE(a) - MAE(b), municipalities resampled within each state."""
    import numpy as np
    paired = a.merge(b, on=['sample_id', 'municipality_code', 'state_code'], suffixes=('_a', '_b'))
    paired['delta'] = paired.abs_error_a - paired.abs_error_b
    rng = np.random.default_rng(seed)
    groups = {s: [g.delta.to_numpy() for _, g in part.groupby('municipality_code')] for s, part in paired.groupby('state_code')}

    def stat(chosen):
        means = {s: np.concatenate(v).mean() for s, v in chosen.items()}
        if weighting == 'state50':
            return float(np.mean(list(means.values())))
        allv = np.concatenate([np.concatenate(v) for v in chosen.values()])
        return float(allv.mean())
    point = stat(groups)
    samples = [stat({s: [g[i] for i in rng.integers(len(g), size=len(g))] for s, g in groups.items()}) for _ in range(draws)]
    return point, float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5)), len(paired)


def evaluate(name):
    import pandas as pd
    folder = OUT / name
    table = pd.read_parquet(folder / 'predictions.parquet')
    scope = SCOPES[name]
    weighting = 'state50' if scope['states'] and len(scope['states']) > 1 else 'harvest'
    h = harvest_table(table)
    rows = []
    for candidate, part in h.groupby('candidate'):
        if '_seed' in candidate:
            continue
        for spatial, sub in (('in_training', part[~part.spatial_holdout]), ('held_out', part[part.spatial_holdout]), ('all', part)):
            if len(sub):
                rows.append({'scope': name, 'candidate': candidate, 'municipalities_set': spatial, **summary(sub, weighting)})
        for year, sub in part[~part.spatial_holdout].groupby('harvest_year'):
            rows.append({'scope': name, 'candidate': candidate, 'municipalities_set': f'in_training_{year}', **summary(sub, weighting)})
    by_cutoff = []
    t = table[~table.spatial_holdout & ~table.candidate.str.contains('_seed')]
    for (candidate, cutoff), part in t.groupby(['candidate', 'horizon_days']):
        hh = harvest_table(part)
        by_cutoff.append({'scope': name, 'candidate': candidate, 'cutoff': int(cutoff), **summary(hh, weighting)})
    write_csv(folder / 'metrics.csv', rows)
    write_csv(folder / 'metrics_by_cutoff.csv', by_cutoff)
    keys = {c: set(zip(p.sample_id, p.horizon_days)) for c, p in table.groupby('candidate') if '_seed' not in c}
    common = set.intersection(*keys.values())
    coverage = {c: {'rows': len(k), 'rows_on_common_keys': len(k & common)} for c, k in keys.items()}
    if any(len(k) != len(common) for k in keys.values()):
        raise ValueError(f'{name}: candidates do not share the same keys: {coverage}')
    inside = h[~h.spatial_holdout]
    present = sorted(inside.candidate.unique())
    simple = [c for c in SIMPLE if c in present]
    mae = {c: summary(inside[inside.candidate == c], weighting)['mae_kg_ha'] for c in present if '_seed' not in c}
    strongest = min(simple, key=mae.get)
    decisions = []
    criteria = PROTOCOL['promotion_criteria']
    for candidate in [c for c in present if c.startswith('v8_') and '_seed' not in c]:
        point, low, high, n = bootstrap(inside[inside.candidate == candidate], inside[inside.candidate == strongest], weighting)
        reduction = (mae[strongest] - mae[candidate]) / mae[strongest]
        states = {}
        for state, sub in inside.groupby('state_code'):
            a = sub[sub.candidate == candidate].abs_error.mean()
            b = sub[sub.candidate == strongest].abs_error.mean()
            states[state] = float((a - b) / b)
        held = h[h.spatial_holdout]
        held_delta = None
        if len(held):
            held_delta = bootstrap(held[held.candidate == candidate], held[held.candidate == strongest], weighting)[:3]
        seeds = [c for c in present if c.startswith(candidate + '_seed')]
        seed_maes = [summary(inside[inside.candidate == s], weighting)['mae_kg_ha'] for s in seeds]
        each = {}
        for ref in simple + [c for c in present if c.startswith('v7_')]:
            p, lo, hi, _ = bootstrap(inside[inside.candidate == candidate], inside[inside.candidate == ref], weighting)
            each[ref] = {'reference_mae': mae[ref], 'relative_reduction': (mae[ref] - mae[candidate]) / mae[ref], 'delta_kg_ha': p, 'ci95': [lo, hi]}
        passed = reduction >= criteria['relative_mae_reduction_min'] and high < 0 and all(v <= criteria['no_priority_state_worse_than'] for v in states.values())
        decisions.append({'scope': name, 'candidate': candidate, 'strongest_reference': strongest, 'reference_mae': mae[strongest],
                          'candidate_mae': mae[candidate], 'relative_reduction': reduction, 'delta_kg_ha': point, 'ci95': [low, high],
                          'paired_harvests': n, 'relative_change_by_state': states, 'held_out_delta_ci95': held_delta,
                          'seed_mae_min': min(seed_maes) if seed_maes else None, 'seed_mae_max': max(seed_maes) if seed_maes else None,
                          'vs_each_reference': each, 'promoted': passed, 'status': 'promotable' if passed else 'experimental'})
    save_json(folder / 'decision.json', {'scope': name, 'weighting': weighting, 'mae_in_training': mae, 'decisions': decisions,
                                         'simple_references': simple, 'strongest_simple_reference': strongest, 'key_coverage': coverage,
                                         'criteria': criteria, 'data': 'development folds 2019-2021 only'})
    return mae, decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scopes', default=','.join(SCOPES))
    args = parser.parse_args()
    for name in args.scopes.split(','):
        if not (OUT / name / 'predictions.parquet').exists():
            print(name, 'not run yet')
            continue
        mae, decisions = evaluate(name)
        print(f'== {name}')
        for c, v in sorted(mae.items(), key=lambda kv: kv[1]):
            print(f'   {c:28s} {v:8.1f}')
        for d in decisions:
            print(f'   {d["candidate"]} vs {d["strongest_reference"]}: {100 * d["relative_reduction"]:.1f}% Δ {d["delta_kg_ha"]:.1f} '
                  f'[{d["ci95"][0]:.1f}; {d["ci95"][1]:.1f}] states {json.dumps({k: round(100 * v, 1) for k, v in d["relative_change_by_state"].items()})} '
                  f'held-out {None if not d["held_out_delta_ci95"] else [round(x, 1) for x in d["held_out_delta_ci95"]]} -> {d["status"]}')


if __name__ == '__main__':
    main()
