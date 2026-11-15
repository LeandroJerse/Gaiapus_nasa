"""Freeze every confirmation arm: final fit (labels published by the 2025 issue), target-free 2025 predictions, bundles, hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from data.ingest import us_v2_chain as C
from data.ingest import us_v2_features as F
from data.ingest import us_v2_scenarios as S
from data.ingest import us_v2_train as T
from data.ingest import us_v2_weather as wx

FROZEN = T.OUT / 'frozen'
CONSUMED = T.OUT / 'c1_consumed.json'
C1_YEAR = 2025


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def assert_unopened():
    if CONSUMED.exists():
        raise ValueError('C1 already opened: no refit or refreeze of confirmation arms')


def final_fit_rows(frame, kinds, fips_filter=None):
    parts = []
    for k in kinds:
        cut = frame[(frame.year == C1_YEAR) & (frame.issue_kind == k)].issued_at.min()
        part = frame[(frame.issue_kind == k) & (frame.year < C1_YEAR) & ~frame.spatial_holdout & frame.label_available_at.notna() & (frame.label_available_at <= cut)]
        if fips_filter is not None:
            part = part[part.fips.isin(fips_filter)]
        parts.append(part[part.yield_kg_ha.notna()])
    return pd.concat(parts)


def fit_final(fit, features, model_name, device):
    hm = T.history_model(fit)
    if model_name.startswith('base:'):
        return {'family': 'baseline', 'name': model_name, 'history': hm, 'fallback': float(fit.yield_kg_ha.median())}
    kind, param = C.parse_model(model_name)
    if kind == 'mlp':
        last = fit.year.max()
        probe = T.fit_candidate(kind, param, fit[fit.year < last], features, T.history_model(fit[fit.year < last]), device, stop=fit[fit.year == last])
        b = T.fit_candidate(kind, param, fit, features, hm, device, epochs=probe['epochs'], seeds=T.GRID['mlp_final_seeds'])
        b['probe_epochs'] = probe['epochs']
        return b
    return T.fit_candidate(kind, param, fit, features, hm, device)


def predict_any(bundle, frame):
    if bundle['family'] == 'baseline':
        return T.baselines(frame, bundle['history'], bundle['fallback'])[bundle['name'].split(':', 1)[1]]
    return T.predict(bundle, frame)


def cuda_parity(bundle, frame, device):
    if bundle['family'] != 'mlp' or str(device) == 'cpu':
        return None
    import torch
    x = torch.as_tensor(bundle['prep'].transform(frame[bundle['features']]), dtype=torch.float32, device=device)
    preds = []
    for st in bundle['states']:
        net = T._network(x.shape[1], bundle['hidden'], device, 0)
        net.load_state_dict({k: torch.from_numpy(v).to(device) for k, v in st.items()})
        net.eval()
        with torch.no_grad():
            preds.append(net(x).squeeze(1).cpu().numpy())
    gpu = np.maximum(0, bundle['history'].predict(frame[T.HIST]) + np.mean(preds, axis=0) * bundle['scale'])
    return float(np.max(np.abs(gpu - T.predict(bundle, frame))))


def save_bundle(bundle, arm, scope, kind):
    folder = T.MODELS / arm
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{scope}_{kind}.joblib'
    joblib.dump(bundle, path)
    files = {path.relative_to(T.ROOT).as_posix(): sha(path)}
    if bundle.get('family') == 'mlp':
        import torch
        pt = folder / f'{scope}_{kind}_mlp.pt'
        torch.save([{k: torch.from_numpy(v) for k, v in st.items()} for st in bundle['states']], pt)
        files[pt.relative_to(T.ROOT).as_posix()] = sha(pt)
    return files


def freeze_arm(arm, builder, scope, kinds, model_by_kind, policy='A', mode='availability_replay', fips_filter=None, extra=None, device=None):
    assert_unopened()
    state, crop = T.SCOPES[scope]
    frame = builder.rows(state, crop, range(2001, C1_YEAR), kinds, mode=mode)
    target = builder.rows(state, crop, [C1_YEAR], kinds, mode=mode, with_label=False)
    if extra is not None:
        frame, target = extra['fn'](frame), extra['fn'](target)
    # the 2025 issue dates must exist for the label-availability cut
    frame = pd.concat([frame, target.assign(yield_kg_ha=np.nan, label_available_at=None)], ignore_index=True)
    if extra is not None and extra.get('replace_weather'):
        features = T.HIST + T.SPACE + extra['columns']
    else:
        features = T.HIST + T.SPACE + F.WEATHER_COLUMNS + (extra['columns'] if extra else [])
    if policy == 'B':
        features = features + T.POLICY_B_EXTRA
    out, files, meta = [], {}, {}
    groups = [[k] for k in kinds] if policy == 'A' else [list(kinds)]
    for ks in groups:
        fit = final_fit_rows(frame, ks, fips_filter)
        for k in ks:
            name = model_by_kind[k]
            bundle = fit_final(fit, features, name, device)
            tk = target[target.issue_kind == k]
            pred = predict_any(bundle, tk)
            base = T.baselines(tk, bundle['history'] if 'history' in bundle else T.history_model(fit), float(fit.yield_kg_ha.median()))
            df = tk[['fips', 'state', 'crop', 'year', 'issue_kind', 'issued_at', 'group', 'spatial_holdout', 'in_v1_population', 'hist_age', 'last_label_year']].copy()
            df['pred'] = pred
            for bn, bv in base.items():
                df[f'base_{bn}'] = bv
            out.append(df.assign(arm=arm, scope=scope, model=name))
            files |= save_bundle(bundle, arm, scope, k)
            meta[k] = {'model': name, 'fit_rows': int(len(fit)), 'fit_years': [int(fit.year.min()), int(fit.year.max())], 'features': features,
                       'cuda_parity_max_abs_kg_ha': cuda_parity(bundle, tk, device), 'mlp_seed_log': bundle.get('seed_log'), 'probe_epochs': bundle.get('probe_epochs')}
    pred = pd.concat(out)
    if {'yield_kg_ha', 'value_raw', 'production'} & set(pred.columns):
        raise ValueError('target-bearing column in frozen predictions')
    FROZEN.mkdir(parents=True, exist_ok=True)
    path = FROZEN / f'{arm}__{scope}.csv'
    with path.open('x', encoding='utf-8', newline='') as f:
        pred.to_csv(f, index=False)
    files[path.relative_to(T.ROOT).as_posix()] = sha(path)
    record = {'arm': arm, 'scope': scope, 'policy': policy, 'mode': mode if isinstance(mode, str) else str(mode), 'kinds': list(kinds), 'meta': meta, 'files': files,
              'created_at': datetime.now(timezone.utc).isoformat(), 'confirmation_labels_read': False}
    rec = FROZEN / f'{arm}__{scope}.json'
    with rec.open('x', encoding='utf-8') as f:
        json.dump(record, f, indent=1, default=str)
    return record


def freeze_chain(builder, scope, sel, device):
    """pre_6m / pre_18m chain for crop year 2025 with the main selection; analog years complete before each issue."""
    assert_unopened()
    state, crop = T.SCOPES[scope]
    policy = sel['policy']
    frame = builder.rows(state, crop, range(2001, C1_YEAR), F.ISSUE_KINDS)
    target = builder.rows(state, crop, [C1_YEAR], F.ISSUE_KINDS, with_label=False)
    frame = pd.concat([frame, target.assign(yield_kg_ha=np.nan, label_available_at=None)], ignore_index=True)
    features = T.HIST + T.SPACE + F.WEATHER_COLUMNS + (T.POLICY_B_EXTRA if policy == 'B' else [])
    fips_list = sorted(target.fips.unique())
    histories = {f: S.weather_history(f) for f in fips_list}
    observed = {f: wx.read_county(f) for f in fips_list}
    rows, files = [], {}
    for kind in C.PRE_KINDS:
        group = [kind] if policy == 'A' else list(F.ISSUE_KINDS)
        fit = final_fit_rows(frame, group)
        bundle = fit_final(fit, features, sel[f'{kind}_weather_model'], device)
        files |= save_bundle(bundle, 'chain', scope, kind)
        tk = target[target.issue_kind == kind].assign(yield_kg_ha=np.nan)
        for r in C.chain_rows(tk, bundle, features, histories, observed):
            r.pop('yield_kg_ha')
            rows.append(dict(r, scope=scope, weather_model=sel[f'{kind}_weather_model']))
    path = FROZEN / f'chain__{scope}.csv'
    with path.open('x', encoding='utf-8', newline='') as f:
        pd.DataFrame(rows).to_csv(f, index=False)
    files[path.relative_to(T.ROOT).as_posix()] = sha(path)
    rec = FROZEN / f'chain__{scope}.json'
    with rec.open('x', encoding='utf-8') as f:
        json.dump({'arm': 'chain', 'scope': scope, 'policy': policy, 'files': files, 'created_at': datetime.now(timezone.utc).isoformat()}, f, indent=1)
    return files


def _dev(name):
    p = T.OUT / 'dev' / f'dev_{name}_predictions.csv.gz'
    return pd.read_csv(p, dtype={'fips': str}) if p.exists() else None


def _gain(cand: pd.DataFrame, comp: pd.DataFrame, on=('group',)) -> dict:
    m = cand.merge(comp, on=list(on), suffixes=('_c', '_p'))
    m = m[m.y_c.notna()]
    if m.empty:
        return {'gain': None, 'pairs': 0}
    ec, ep = (m.pc - m.y_c).abs(), (m.pp - m.y_c).abs()
    mc = ec.groupby(m.fips_c).mean().mean()
    mp = ep.groupby(m.fips_c).mean().mean()
    return {'gain': float((mp - mc) / mp), 'pairs': int(len(m)), 'mae_candidate': float(mc), 'mae_comparator': float(mp)}


def _pick(long, model, kind='in_season', policy=None, holdout=True, v1=False):
    if long is None:
        return None
    g = long[(long.issue_kind == kind) & (long.model == model) & (long.spatial_holdout == holdout)]
    if policy is not None:
        g = g[g.policy == policy]
    if v1:
        g = g[g.in_v1_population]
    return g.rename(columns={'pred': 'p', 'yield_kg_ha': 'y'})[['group', 'fips', 'p', 'y']]


def dev_spatial_and_comparisons(sel_main, sel_arms):
    main = _dev('main')
    chain = pd.read_csv(T.OUT / 'dev' / 'dev_chain_predictions.csv.gz', dtype={'fips': str})
    comps, spatial = [], {}
    fz = lambda arm, scope: f'data/processed/us_training_v2/frozen/{arm}__{scope}.csv'
    for scope in T.SCOPES:
        s = sel_main[scope]
        a_main = sel_arms.get('main_A', {}).get(scope)
        for base in T.BASELINES:
            cid_ = f'F1:{scope}:{base}'
            cand = _pick(main, s['F1_in_season'], policy=s['policy'])
            comp = _pick(main, f'base:{base}', policy=s['policy'])
            spatial[cid_] = _gain(cand.rename(columns={'p': 'pc'}), comp.rename(columns={'p': 'pp'}))
            comps.append({'id': cid_, 'family': 'F1', 'scope': scope, 'issue_kind': 'in_season', 'candidate': s['F1_in_season'], 'comparator': f'base:{base}',
                          'cand_file': fz('main', scope), 'cand_col': 'pred', 'comp_file': fz('main', scope), 'comp_col': f'base_{base}', 'keys': 'seen',
                          'candidate_is_reference': s['F1_in_season'].startswith('base:'), 'executed': True})
        pairs = [('F2', 'v1pop_avail', 'v1pop_lag2', 'v1_seen'), ('F3', 'main_A', 'v1pop_avail', 'v1_seen'),
                 ('F4', 'e2a_gridmet_point', 'main_A', 'seen'), ('F5', 'e2b_gridmet_cdl', 'e2a_gridmet_point', 'seen'),
                 ('F6', 'e3_ne_irrigation', 'main_A', 'seen'), ('F7', 'e4_ks_calendar', 'main_A', 'seen')]
        for fam, ca, co, keys in pairs:
            if fam == 'F6' and scope not in ('NE_soy', 'NE_maize'):
                continue
            if fam == 'F7' and scope != 'KS_wheat_winter':
                continue
            cm, pm = sel_arms.get(ca, {}).get(scope), sel_arms.get(co, {}).get(scope)
            executed = cm is not None and pm is not None
            cid_ = f'{fam}:{scope}'
            if executed:
                dl = lambda arm: main if arm == 'main_A' else _dev(arm)
                cand = _pick(dl(ca), cm, policy='A', v1=(keys == 'v1_seen')).rename(columns={'p': 'pc'})
                comp = _pick(dl(co), pm, policy='A', v1=(keys == 'v1_seen')).rename(columns={'p': 'pp'})
                spatial[cid_] = _gain(cand, comp)
            comps.append({'id': cid_, 'family': fam, 'scope': scope, 'issue_kind': 'in_season', 'candidate': f'{ca}:{cm}', 'comparator': f'{co}:{pm}',
                          'cand_file': fz(ca, scope), 'cand_col': 'pred', 'comp_file': fz(co, scope), 'comp_col': 'pred', 'keys': keys,
                          'candidate_is_reference': False, 'executed': executed})
        for fam, kind in (('F8', 'pre_6m'), ('F9', 'pre_18m')):
            cid_ = f'{fam}:{scope}'
            ch = chain[(chain.scope == scope) & (chain.issue_kind == kind) & chain.spatial_holdout].rename(columns={'p50': 'pc', 'yield_kg_ha': 'y'})[['group', 'fips', 'pc', 'y']]
            comp = _pick(main, s[f'{kind}_history_only'], kind=kind, policy=s['policy']).rename(columns={'p': 'pp'})
            spatial[cid_] = _gain(ch, comp)
            comps.append({'id': cid_, 'family': fam, 'scope': scope, 'issue_kind': kind, 'candidate': f"chain:{s[f'{kind}_weather_model']}",
                          'comparator': s[f'{kind}_history_only'], 'cand_file': f'data/processed/us_training_v2/frozen/chain__{scope}.csv', 'cand_col': 'p50',
                          'comp_file': fz('main', scope), 'comp_col': 'pred', 'keys': 'seen', 'candidate_is_reference': False, 'executed': True})
    return comps, spatial


def run_all(stage='all', log=print):
    assert_unopened()
    dev = T.device()
    b = T.RowBuilder()
    sel_main = json.loads((T.OUT / 'dev' / 'selection_main.json').read_text())
    main_scores = pd.read_csv(T.OUT / 'dev' / 'dev_main_scores.csv')
    sel_arms = {'main_A': C.select_arm(main_scores)}
    for arm in ('v1pop_avail', 'v1pop_lag2', 'e3_ne_irrigation', 'e4_ks_calendar', 'e2a_gridmet_point', 'e2b_gridmet_cdl'):
        p = T.OUT / 'dev' / f'dev_{arm}_scores.csv'
        if p.exists():
            sel_arms[arm] = C.select_arm(pd.read_csv(p))
    (T.OUT / 'dev' / 'selection_arms.json').write_text(json.dumps(sel_arms, indent=1), encoding='utf-8')
    v1 = {f for f, c in b.counties.items() if c['in_v1_population']}
    for scope in T.SCOPES if stage in ('all', 'base', 'e2') else []:
        s = sel_main[scope]
        if stage == 'e2':
            for arm, wfile in (('e2a_gridmet_point', 'weather_features_gridmet_point.csv.gz'), ('e2b_gridmet_cdl', 'weather_features_gridmet_cdl.csv.gz')):
                if arm in sel_arms:
                    bw = T.RowBuilder(weather=pd.read_csv(T.OUT / wfile, dtype={'fips': str}))
                    freeze_arm(arm, bw, scope, ('in_season',), {'in_season': sel_arms[arm][scope]}, device=dev)
            continue
        freeze_arm('main', b, scope, F.ISSUE_KINDS, {'in_season': s['F1_in_season'], 'pre_6m': s['pre_6m_history_only'], 'pre_18m': s['pre_18m_history_only']},
                   policy=s['policy'], device=dev)
        freeze_arm('main_A', b, scope, ('in_season',), {'in_season': sel_arms['main_A'][scope]}, device=dev)
        if 'v1pop_avail' in sel_arms:
            freeze_arm('v1pop_avail', b, scope, ('in_season',), {'in_season': sel_arms['v1pop_avail'][scope]}, fips_filter=v1, device=dev)
        if 'v1pop_lag2' in sel_arms:
            freeze_arm('v1pop_lag2', b, scope, ('in_season',), {'in_season': sel_arms['v1pop_lag2'][scope]}, mode='fixed_lag2', fips_filter=v1, device=dev)
        for arm in ('e3_ne_irrigation', 'e4_ks_calendar'):
            if arm in sel_arms and scope in sel_arms[arm]:
                freeze_arm(arm, b, scope, ('in_season',), {'in_season': sel_arms[arm][scope]}, extra=T.extra_spec(arm), device=dev)
        if stage == 'all':
            for arm, wfile in (('e2a_gridmet_point', 'weather_features_gridmet_point.csv.gz'), ('e2b_gridmet_cdl', 'weather_features_gridmet_cdl.csv.gz')):
                if arm in sel_arms:
                    bw = T.RowBuilder(weather=pd.read_csv(T.OUT / wfile, dtype={'fips': str}))
                    freeze_arm(arm, bw, scope, ('in_season',), {'in_season': sel_arms[arm][scope]}, device=dev)
        freeze_chain(b, scope, s, dev)
        log(json.dumps({'frozen_scope': scope}))
    if stage in ('base', 'e2'):
        return
    comps, spatial = dev_spatial_and_comparisons(sel_main, sel_arms)
    for name, obj in (('comparisons.json', comps), ('dev_spatial.json', spatial)):
        with (FROZEN / name).open('x', encoding='utf-8') as f:
            json.dump(obj, f, indent=1)
    path = freeze_all_manifest()
    log(json.dumps({'all_frozen': str(path), 'comparisons': len(comps), 'executed': sum(c['executed'] for c in comps)}))


def freeze_all_manifest():
    assert_unopened()
    recs = sorted(FROZEN.glob('*.json'))
    files = {}
    for r in recs:
        files[r.relative_to(T.ROOT).as_posix()] = sha(r)
        body = json.loads(r.read_text())
        if isinstance(body, dict) and isinstance(body.get('files'), dict):
            files |= body['files']
    for extra in ['data/frozen/us_training_v2_protocol.json', 'data/frozen/us_training_v2_selection_addendum.json', 'data/frozen/us_training_v2_registration.json',
                  'data/frozen/us_v2_availability.json', 'data/processed/us_training_v2/counties_v2.json']:
        files[extra] = sha(T.ROOT / extra)
    for p in sorted((T.OUT / 'dev').glob('*')):
        files[p.relative_to(T.ROOT).as_posix()] = sha(p)
    for p in sorted((T.ROOT / 'data/ingest').glob('us_v2_*.py')) + [T.ROOT / 'data/forecasting/weather_ensemble.py']:
        files[p.relative_to(T.ROOT).as_posix()] = sha(p)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'files': files, 'confirmation_labels_read': False}
    path = T.OUT / 'all_frozen.json'
    with path.open('x', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1)
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['run_all'])
    ap.add_argument('--stage', choices=['all', 'base', 'e2', 'finalize'], default='all')
    run_all(ap.parse_args().stage)
