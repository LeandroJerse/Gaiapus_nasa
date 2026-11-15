"""Mechanical selection (selection addendum) and full-chain development evaluation for pre-season issues."""
from __future__ import annotations

import argparse
import json
import time
from datetime import date

import numpy as np
import pandas as pd

from data.ingest import us_v2_features as F
from data.ingest import us_v2_scenarios as S
from data.ingest import us_v2_train as T
from data.ingest import us_v2_weather as wx

DEV = T.OUT / 'dev'
PRE_KINDS = ('pre_6m', 'pre_18m')


def _candidates(models):
    return [m for m in models if not m.startswith('base:')]


def select_main(scores: pd.DataFrame) -> dict:
    """scores = dev_main_scores.csv. Returns per-scope selection following the addendum."""
    seen = scores[~scores.spatial_holdout]
    out = {}
    for scope, g in seen.groupby('scope'):
        pol_score = {}
        for policy, gp in g.groupby('policy'):
            cand = gp[gp.model.isin(_candidates(gp.model.unique()))]
            pol_score[policy] = cand.groupby('model').mae.mean().min()
        policy = 'B' if pol_score.get('B', np.inf) <= pol_score.get('A', np.inf) else 'A'
        gp = g[g.policy == policy]
        ins = gp[gp.issue_kind == 'in_season']
        pool = ins[ins.model.str.startswith('base:') | ins.model.isin(_candidates(ins.model.unique()))]
        f1 = pool.loc[pool.mae.idxmin(), 'model']
        ins_base = ins[ins.model.str.startswith('base:')]
        sel = {'policy': policy, 'policy_scores': {k: float(v) for k, v in pol_score.items()}, 'F1_in_season': f1,
               'F1_status_hint': 'reference_selected' if f1.startswith('base:') else 'candidate_selected',
               'in_season_history_only': ins_base.loc[ins_base.mae.idxmin(), 'model']}
        for kind in PRE_KINDS:
            gk = gp[gp.issue_kind == kind]
            cand = gk[gk.model.isin(_candidates(gk.model.unique()))]
            base = gk[gk.model.str.startswith('base:')]
            sel[f'{kind}_weather_model'] = cand.loc[cand.mae.idxmin(), 'model']
            sel[f'{kind}_history_only'] = base.loc[base.mae.idxmin(), 'model']
        out[scope] = sel
    return out


def select_arm(scores: pd.DataFrame) -> dict:
    seen = scores[(~scores.spatial_holdout) & (scores.issue_kind == 'in_season') & (scores.policy == 'A')]
    return {scope: g.loc[g.mae.idxmin(), 'model'] for scope, g in seen.groupby('scope')}


def parse_model(name: str):
    kind, param = name.split(':', 1)
    if kind == 'ridge':
        return 'ridge', float(param)
    if kind == 'hgb':
        return 'hgb', int(param)
    if kind == 'mlp':
        return 'mlp', tuple(int(x) for x in param.split('x'))
    raise ValueError(name)


def fit_fold_model(frame_all, V, kinds_group, features, model_name, device):
    issued = {k: frame_all[(frame_all.year == V) & (frame_all.issue_kind == k)].issued_at.min() for k in kinds_group}
    fit = pd.concat([T.fit_rows(frame_all, V, k, issued[k]) for k in kinds_group])
    hm = T.history_model(fit)
    kind, param = parse_model(model_name)
    if kind == 'mlp':
        last = fit.year.max()
        probe = T.fit_candidate(kind, param, fit[fit.year < last], features, T.history_model(fit[fit.year < last]), device, stop=fit[fit.year == last])
        return T.fit_candidate(kind, param, fit, features, hm, device, epochs=probe['epochs'])
    return T.fit_candidate(kind, param, fit, features, hm, device)


def chain_rows(val: pd.DataFrame, bundle, features, histories, observed):
    """All members of all rows predicted in one batch (avoids per-row thread-pool overhead)."""
    blocks, metas = [], []
    for i, r in enumerate(val.itertuples()):
        mf, man = S.member_features(histories[r.fips], observed[r.fips], r.state, r.crop, int(r.year), date.fromisoformat(r.issued_at))
        base = pd.DataFrame([r._asdict()] * len(mf)).drop(columns=[c for c in F.WEATHER_COLUMNS if c in r._fields])
        blocks.append(pd.concat([base.reset_index(drop=True), mf.reset_index(drop=True)], axis=1).assign(_row=i))
        metas.append((r, len(mf), man))
    if not blocks:
        return []
    X = pd.concat(blocks, ignore_index=True)
    X['_p'] = T.predict(bundle, X)
    out = []
    for i, (r, n, man) in enumerate(metas):
        p = X.loc[X._row == i, '_p'].to_numpy()
        out.append({'fips': r.fips, 'state': r.state, 'crop': r.crop, 'year': r.year, 'issue_kind': r.issue_kind, 'group': r.group,
                    'spatial_holdout': r.spatial_holdout, 'yield_kg_ha': getattr(r, 'yield_kg_ha', np.nan), 'members': n, 'analog_min': min(man['analog_years']),
                    'analog_max': max(man['analog_years']), 'p10': float(np.quantile(p, .1)), 'p50': float(np.median(p)), 'p90': float(np.quantile(p, .9))})
    return out


def run_chain_dev(scopes=None, log=print):
    scores = pd.read_csv(DEV / 'dev_main_scores.csv')
    sel = select_main(scores)
    path = DEV / 'selection_main.json'
    text = json.dumps(sel, indent=1)
    if path.exists() and path.read_text(encoding='utf-8') != text:
        raise ValueError('selection changed')
    path.write_text(text, encoding='utf-8')
    b = T.RowBuilder()
    dev = T.device()
    for scope, (state, crop) in T.SCOPES.items():
        if scopes and scope not in scopes:
            continue
        rows = []
        t0 = time.perf_counter()
        frame = b.rows(state, crop, range(2001, 2025), F.ISSUE_KINDS)
        fips_list = sorted(frame.fips.unique())
        histories = {f: S.weather_history(f) for f in fips_list}
        observed = {f: wx.read_county(f) for f in fips_list}
        policy = sel[scope]['policy']
        for kind in PRE_KINDS:
            group = [kind] if policy == 'A' else list(F.ISSUE_KINDS)
            features = T.HIST + T.SPACE + F.WEATHER_COLUMNS + (T.POLICY_B_EXTRA if policy == 'B' else [])
            for V in T.VAL_YEARS:
                bundle = fit_fold_model(frame, V, group, features, sel[scope][f'{kind}_weather_model'], dev)
                val = frame[(frame.year == V) & (frame.issue_kind == kind)]
                rows += [dict(x, scope=scope) for x in chain_rows(val, bundle, features, histories, observed)]
        pd.DataFrame(rows).to_csv(DEV / f'chain_parts_{scope}.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
        log(json.dumps({'chain_scope': scope, 'seconds': round(time.perf_counter() - t0, 1)}))
        del histories


def merge_chain():
    parts = [pd.read_csv(DEV / f'chain_parts_{s}.csv.gz', dtype={'fips': str}) for s in T.SCOPES]
    out = pd.concat(parts, ignore_index=True)
    out.to_csv(DEV / 'dev_chain_predictions.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['chain_dev', 'merge'])
    ap.add_argument('--scope', action='append')
    a = ap.parse_args()
    run_chain_dev(a.scope) if a.action == 'chain_dev' else merge_chain()
