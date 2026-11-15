"""Post-opening technical correction of the C1 spatial gate (documented; c1_score.json is never rewritten).

Defect: us_v2_freeze._pick did not filter development predictions by scope, so the development spatial-holdout
gain of families F1-F6 was pooled across scopes. The registered rule is per comparison. The gate depends only on
development predictions frozen (and hashed) before the opening, never on 2025 labels, so it is recomputed here
mechanically. Second fix, conservative only: an experiment arm whose own selection is a baseline does not test
its added features and is reported as reference_selected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from data.ingest import us_v2_confirm as C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/processed/us_training_v2'
DEV = OUT / 'dev'


def _long(arm: str) -> pd.DataFrame:
    name = 'main' if arm in ('main', 'main_A') else arm
    return pd.read_csv(DEV / f'dev_{name}_predictions.csv.gz', dtype={'fips': str})


def _pick(arm, model, scope, kind, policy, v1):
    g = _long(arm)
    g = g[(g.scope == scope) & (g.issue_kind == kind) & (g.model == model) & (g.policy == policy) & g.spatial_holdout & g.yield_kg_ha.notna()]
    if v1:
        g = g[g.in_v1_population]
    return g[['group', 'fips', 'pred', 'yield_kg_ha']]


def gain(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    m = a.merge(b[['group', 'pred']], on='group', suffixes=('_c', '_p'))
    if m.empty:
        return {'gain': None, 'pairs': 0}
    mc = (m.pred_c - m.yield_kg_ha).abs().groupby(m.fips).mean().mean()
    mp = (m.pred_p - m.yield_kg_ha).abs().groupby(m.fips).mean().mean()
    return {'gain': float((mp - mc) / mp), 'pairs': int(len(m))}


def main() -> None:
    score = json.loads((OUT / 'c1_score.json').read_text())
    sel = json.loads((DEV / 'selection_main.json').read_text())
    out = []
    for r in score['results']:
        fam, scope = r['family'], r['scope']
        policy_main = sel[scope]['policy']
        row = {'id': r['id'], 'status_as_scored': r['status'], 'dev_spatial_as_scored': {'gain': r.get('dev_spatial_gain_pct'), 'pairs': r.get('dev_spatial_pairs')}}
        if fam in ('F8', 'F9'):
            sp = {'gain': r.get('dev_spatial_gain_pct'), 'pairs': r.get('dev_spatial_pairs')}  # already per scope
            cand_is_ref = False
        elif fam == 'F1':
            sp = gain(_pick('main', r['candidate'], scope, 'in_season', policy_main, False), _pick('main', r['comparator'], scope, 'in_season', policy_main, False))
            cand_is_ref = r['candidate'].startswith('base:')
        else:
            ca, cm = r['candidate'].split(':', 1)
            co, pm = r['comparator'].split(':', 1)
            v1 = fam in ('F2', 'F3')
            sp = gain(_pick(ca, cm, scope, 'in_season', 'A', v1), _pick(co, pm, scope, 'in_season', 'A', v1))
            cand_is_ref = cm.startswith('base:')
        row['dev_spatial_corrected'] = sp
        row['status_corrected'] = C.status(r.get('stats_seen'), sp['gain'], sp['pairs'], cand_is_ref, True)
        row['changed'] = row['status_corrected'] != r['status']
        out.append(row)
    doc = {'c1_score_sha256': C.sha(OUT / 'c1_score.json'), 'reason': __doc__.strip(), 'uses_confirmation_labels': False, 'results': out}
    path = OUT / 'c1_score_correction.json'
    with path.open('x', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    for row in out:
        if row['changed'] or row['status_corrected'] == 'research_confirmed':
            sp = row['dev_spatial_corrected']
            print(row['id'].ljust(30), row['status_as_scored'].ljust(19), '->', row['status_corrected'].ljust(19),
                  'spatial', None if sp['gain'] is None else round(100 * sp['gain'], 1), sp['pairs'])


if __name__ == '__main__':
    main()
