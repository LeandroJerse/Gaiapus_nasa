"""US v2 development (rolling-origin with availability replay), selection and freezing.

No function here reads confirmation labels. Rows for crop year 2025 are built without targets.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from data.ingest import us_v2_features as F
from data.ingest import us_v2_weather as wx
from data.ingest.us_v2_data import load_yields

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/processed/us_training_v2'
MODELS = ROOT / 'data/models/research_us_v2'
PROTOCOL_PATH = ROOT / 'data/frozen/us_training_v2_protocol.json'
PROTOCOL = json.loads(PROTOCOL_PATH.read_text(encoding='utf-8'))
GRID = PROTOCOL['development_selection']['candidates']
SCOPES = {k: tuple(v) for k, v in PROTOCOL['scopes'].items()}
VAL_YEARS = list(range(PROTOCOL['development_selection']['validation_years'][0], PROTOCOL['development_selection']['validation_years'][1] + 1))
HIST = ['hist_last', 'hist_mean3', 'hist_mean10', 'hist_trend', 'hist_count', 'hist_age', 'hist_suppressed_in_window', 'year']
SPACE = ['latitude', 'longitude']
POLICY_B_EXTRA = ['lead_days']
BASELINES = ['last', 'mean3', 'trend', 'history_ridge']


# ---------------------------------------------------------------- rows
class RowBuilder:
    def __init__(self, weather: pd.DataFrame | None = None, yields: pd.DataFrame | None = None):
        self.yields = load_yields() if yields is None else yields
        self.weather = pd.read_csv(OUT / 'weather_features_power.csv.gz', dtype={'fips': str}) if weather is None else weather
        self.hist = {k: g[['year', 'yield_kg_ha']] for k, g in self.yields.groupby(['fips', 'crop'])}
        self.counties = {c['fips']: c for c in json.loads((OUT / 'counties_v2.json').read_text())}
        self._wx = self.weather.set_index(['fips', 'crop', 'year'])

    def history(self, fips, crop, year, issued_at, mode):
        h = self.hist.get((fips, crop), pd.DataFrame({'year': [], 'yield_kg_ha': []}))
        if mode == 'availability_replay':
            return F.history_at_issue(h, crop, year, issued_at)
        if mode == 'fixed_lag2':
            return F.history_lag(h, year, 2)
        if isinstance(mode, tuple) and mode[0] == 'lag2_ceiling':
            return F.history_lag(h, year, 2, ceiling=mode[1])
        raise ValueError(mode)

    def rows(self, state, crop, years, kinds, mode='availability_replay', fips_filter=None, with_label=True, max_label_year=2024):
        out = []
        for fips, c in self.counties.items():
            if c['state'] != state or (fips_filter is not None and fips not in fips_filter):
                continue
            for year in years:
                if (fips, crop, year) not in self._wx.index:
                    continue
                w = self._wx.loc[(fips, crop, year)]
                start, end = date.fromisoformat(w.window_start), date.fromisoformat(w.window_end)
                for kind in kinds:
                    issued = F.issue_date(kind, start, end)
                    m = mode(year) if callable(mode) else mode
                    h = self.history(fips, crop, year, issued, m)
                    row = {'fips': fips, 'state': state, 'crop': crop, 'year': year, 'issue_kind': kind, 'issued_at': issued.isoformat(),
                           'lead_days': (end - issued).days, 'group': F.group_id(fips, crop, year), 'spatial_holdout': c['spatial_holdout'],
                           'in_v1_population': c['in_v1_population'], **h, **w[SPACE + F.WEATHER_COLUMNS].to_dict()}
                    if with_label:
                        if year > max_label_year or (max_label_year > 2024 and not (OUT / 'c1_score.json').exists()):
                            raise ValueError('labels after 2024 are sealed')
                        lab = self.hist.get((fips, crop))
                        v = lab[lab.year == year].yield_kg_ha if lab is not None else pd.Series([], dtype=float)
                        row['yield_kg_ha'] = float(v.iloc[0]) if len(v) and pd.notna(v.iloc[0]) else np.nan
                        at, _ = F.label_available_at(crop, year)
                        row['label_available_at'] = at.isoformat() if at else None
                    out.append(row)
        frame = pd.DataFrame(out)
        for col in HIST:
            if col in frame:
                frame[col] = pd.to_numeric(frame[col], errors='coerce')
        return frame


# ---------------------------------------------------------------- models
def prep():
    return Pipeline([('impute', SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)), ('scale', StandardScaler())])


def county_mae(frame, pred):
    e = pd.DataFrame({'fips': frame.fips.to_numpy(), 'e': np.abs(frame.yield_kg_ha.to_numpy(float) - pred)})
    return float(e.groupby('fips').e.mean().mean())


def history_model(fit, alpha=GRID['history_ridge_alpha']):
    return Pipeline([('prep', prep()), ('m', Ridge(alpha=alpha))]).fit(fit[HIST], fit.yield_kg_ha.to_numpy(float))


def baselines(frame, hmodel, fallback):
    out = {'history_ridge': np.maximum(0, hmodel.predict(frame[HIST]))}
    for name, col in [('last', 'hist_last'), ('mean3', 'hist_mean3'), ('trend', 'hist_trend')]:
        out[name] = np.maximum(0, frame[col].fillna(fallback).to_numpy(float))
    return out


def _network(width, hidden, device, seed):
    import torch
    torch.manual_seed(seed)
    layers = []
    for size in hidden:
        layers += [torch.nn.Linear(width, size), torch.nn.ReLU(), torch.nn.Dropout(GRID['mlp_dropout'])]
        width = size
    layers.append(torch.nn.Linear(width, 1))
    return torch.nn.Sequential(*layers).to(device)


def fit_mlp(fit, features, hmodel, hidden, device, seeds, epochs=None, stop=None):
    """stop: inner early-stopping rows (latest fit year), never the scored validation year."""
    import torch
    p = prep().fit(fit[features])
    x = torch.as_tensor(p.transform(fit[features]), dtype=torch.float32, device=device)
    resid = fit.yield_kg_ha.to_numpy(float) - hmodel.predict(fit[HIST])
    scale = float(np.std(resid)) or 1.0
    y = torch.as_tensor(resid / scale, dtype=torch.float32, device=device)
    if stop is not None:
        sx = torch.as_tensor(p.transform(stop[features]), dtype=torch.float32, device=device)
        sb = hmodel.predict(stop[HIST])
    states, log = [], []
    for seed in seeds:
        model = _network(x.shape[1], hidden, device, seed)
        opt = torch.optim.AdamW(model.parameters(), lr=GRID['mlp_learning_rate'], weight_decay=GRID['mlp_weight_decay'])
        gen = torch.Generator(device=device).manual_seed(seed)
        best, best_state, best_epoch = float('inf'), None, 1
        t0 = time.perf_counter()
        for epoch in range(1, (epochs or GRID['mlp_max_epochs']) + 1):
            model.train()
            order = torch.randperm(len(x), generator=gen, device=device)
            for i in range(0, len(x), GRID['mlp_batch_size']):
                b = order[i:i + GRID['mlp_batch_size']]
                opt.zero_grad(set_to_none=True)
                torch.nn.functional.mse_loss(model(x[b]).squeeze(1), y[b]).backward()
                opt.step()
            if stop is not None:
                model.eval()
                with torch.no_grad():
                    s = county_mae(stop, np.maximum(0, sb + model(sx).squeeze(1).cpu().numpy() * scale))
                if s < best:
                    best, best_epoch = s, epoch
                    best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                elif epoch - best_epoch >= GRID['mlp_patience']:
                    break
        if stop is not None:
            model.load_state_dict(best_state)
        log.append({'seed': seed, 'epochs': int(best_epoch if stop is not None else epochs), 'elapsed_s': round(time.perf_counter() - t0, 2), 'device': str(device)})
        states.append({k: v.detach().cpu().numpy().copy() for k, v in model.state_dict().items()})
    return {'family': 'mlp', 'features': features, 'prep': p, 'history': hmodel, 'scale': scale, 'states': states, 'hidden': list(hidden),
            'epochs': int(np.median([l['epochs'] for l in log])), 'seed_log': log}


def predict(bundle, frame):
    fam = bundle['family']
    if fam == 'ridge':
        return np.maximum(0, bundle['model'].predict(frame[bundle['features']]))
    x = bundle['prep'].transform(frame[bundle['features']]).astype(np.float32)
    base = bundle['history'].predict(frame[HIST])
    if fam == 'hgb':
        return np.maximum(0, base + bundle['model'].predict(x))
    vals = []
    for st in bundle['states']:
        h = x
        for i in range(len(bundle['hidden']) + 1):
            h = h @ st[f'{i * 3}.weight'].T + st[f'{i * 3}.bias']
            if i < len(bundle['hidden']):
                h = np.maximum(h, 0)
        vals.append(h[:, 0])
    return np.maximum(0, base + np.mean(vals, axis=0) * bundle['scale'])


def fit_candidate(kind, param, fit, features, hmodel, device, epochs=None, stop=None, seeds=None):
    if kind == 'ridge':
        return {'family': 'ridge', 'features': features, 'model': Pipeline([('prep', prep()), ('m', Ridge(alpha=param))]).fit(fit[features], fit.yield_kg_ha.to_numpy(float))}
    if kind == 'hgb':
        p = prep().fit(fit[features])
        r = fit.yield_kg_ha.to_numpy(float) - hmodel.predict(fit[HIST])
        m = HistGradientBoostingRegressor(max_iter=GRID['hgb_iterations'], max_leaf_nodes=param, min_samples_leaf=GRID['hgb_min_samples_leaf'],
                                          learning_rate=GRID['hgb_learning_rate'], early_stopping=False, random_state=42).fit(p.transform(fit[features]), r)
        return {'family': 'hgb', 'features': features, 'prep': p, 'model': m, 'history': hmodel}
    return fit_mlp(fit, features, hmodel, param, device, seeds or [GRID['mlp_selection_seed']], epochs=epochs, stop=stop)


def candidate_list():
    return ([('ridge', a) for a in GRID['ridge_alphas']] + [('hgb', l) for l in GRID['hgb_leaves']] + [('mlp', tuple(h)) for h in GRID['mlp_hidden']])


def cid(kind, param):
    return f'{kind}:{param}' if kind != 'mlp' else f"mlp:{'x'.join(map(str, param))}"


# ---------------------------------------------------------------- development
def _labelled(frame):
    return frame[frame.yield_kg_ha.notna()]


def fit_rows(frame_all, V, kind, cut):
    """Training rows for validation year V: earlier target years, label published by the validation issue, never holdout."""
    m = (frame_all.issue_kind == kind) & (frame_all.year < V) & ~frame_all.spatial_holdout & \
        frame_all.label_available_at.notna() & (frame_all.label_available_at <= cut)
    if 'role' in frame_all:
        m &= frame_all.role != 'eval'
    return _labelled(frame_all[m])


def fold(frame_all, V, kinds, features, device, policy='A', history_only=False):
    """One validation year. Returns long predictions for seen and holdout validation rows."""
    preds = []
    groups = [[k] for k in kinds] if policy == 'A' else [list(kinds)]
    for ks in groups:
        val = frame_all[(frame_all.year == V) & frame_all.issue_kind.isin(ks)]
        if 'role' in val:
            val = val[val.role != 'fit']
        if val.empty:
            continue
        issued = {k: val[val.issue_kind == k].issued_at.min() for k in ks}
        fit = pd.concat([fit_rows(frame_all, V, k, issued[k]) for k in ks])
        if len(fit) < 50:
            continue
        hm = history_model(fit)
        fb = float(fit.yield_kg_ha.median())
        out = {f'base:{n}': v for n, v in baselines(val, hm, fb).items()}
        if not history_only:
            last_year = fit.year.max()
            inner_fit, stop = fit[fit.year < last_year], fit[fit.year == last_year]
            hm_inner = history_model(inner_fit)
            for kind, param in candidate_list():
                if kind == 'mlp':
                    probe = fit_candidate(kind, param, inner_fit, features, hm_inner, device, stop=stop)
                    b = fit_candidate(kind, param, fit, features, hm, device, epochs=probe['epochs'])
                    b['epochs'] = probe['epochs']
                else:
                    b = fit_candidate(kind, param, fit, features, hm, device)
                out[cid(kind, param)] = predict(b, val)
        keep = val[['fips', 'state', 'crop', 'year', 'issue_kind', 'group', 'spatial_holdout', 'in_v1_population', 'yield_kg_ha', 'hist_age']].copy()
        for name, p in out.items():
            preds.append(keep.assign(model=name, pred=p))
    return pd.concat(preds) if preds else pd.DataFrame()


def score_dev(long):
    lab = long[long.yield_kg_ha.notna()]
    rows = []
    for (kind, model, holdout), g in lab.groupby(['issue_kind', 'model', 'spatial_holdout']):
        per_year = [county_mae(gy, gy.pred.to_numpy()) for _, gy in g.groupby('year')]
        rows.append({'issue_kind': kind, 'model': model, 'spatial_holdout': holdout, 'mae': float(np.mean(per_year)), 'pairs': len(g), 'years': len(per_year)})
    return pd.DataFrame(rows)


def device():
    import torch
    return torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


def run_development(name, builder, kinds, mode, fips_filter, extra=None, policies=('A',), history_only=False, log=print):
    """Rolling-origin development for every scope. Writes dev_<name>_predictions.csv.gz and scores."""
    dev = device()
    allp = []
    for scope, (state, crop) in SCOPES.items():
        if extra is not None and scope not in extra['scopes']:
            continue
        if mode == 'mismatch_diag':
            # v1-style: fit rows at a consistent 2-year lag; evaluation rows with a ceiling fixed 2..5 years back
            fit_f = builder.rows(state, crop, range(2001, 2025), kinds, mode='fixed_lag2', fips_filter=fips_filter).assign(role='fit')
            def ceiling(year):
                return ('lag2_ceiling', year - 2 - (year - VAL_YEARS[0]) % 4)
            eval_f = builder.rows(state, crop, VAL_YEARS, kinds, mode=ceiling, fips_filter=fips_filter).assign(role='eval')
            frame = pd.concat([fit_f, eval_f], ignore_index=True)
        else:
            frame = builder.rows(state, crop, range(2001, 2025), kinds, mode=mode, fips_filter=fips_filter)
        if extra is not None:
            frame = extra['fn'](frame)
        if extra is not None and extra.get('replace_weather'):
            features = HIST + SPACE + extra['columns']
        else:
            features = HIST + SPACE + F.WEATHER_COLUMNS + (extra['columns'] if extra else [])
        for policy in policies:
            feats = features + (POLICY_B_EXTRA if policy == 'B' else [])
            t0 = time.perf_counter()
            for V in VAL_YEARS:
                p = fold(frame, V, kinds, feats, dev, policy=policy, history_only=history_only)
                if len(p):
                    allp.append(p.assign(scope=scope, policy=policy))
            log(json.dumps({'pipeline': name, 'scope': scope, 'policy': policy, 'seconds': round(time.perf_counter() - t0, 1)}))
    long = pd.concat(allp)
    path = OUT / 'dev' / f'dev_{name}_predictions.csv.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    long.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
    scores = pd.concat([score_dev(g).assign(scope=s, policy=p) for (s, p), g in long.groupby(['scope', 'policy'])])
    scores.to_csv(OUT / 'dev' / f'dev_{name}_scores.csv', index=False)
    return long, scores


PIPELINES = {
    'main': dict(kinds=F.ISSUE_KINDS, mode='availability_replay', population='U2', policies=('A', 'B')),
    'v1pop_avail': dict(kinds=('in_season',), mode='availability_replay', population='V1', policies=('A',)),
    'v1pop_lag2': dict(kinds=('in_season',), mode='fixed_lag2', population='V1', policies=('A',)),
}


def population(builder, which):
    if which == 'U2':
        return None
    return {f for f, c in builder.counties.items() if c['in_v1_population']}


def extra_spec(name):
    from data.ingest import us_v2_extras as X
    from data.ingest.us_v2_data import load_development
    if name == 'e3_ne_irrigation':
        table = X.irrigation_table(load_development())
        return {'scopes': ['NE_soy', 'NE_maize'], 'fn': lambda f: X.add_irrigation(f, table), 'columns': X.IRR_COLUMNS}
    if name == 'e4_ks_calendar':
        prog = pd.read_json(ROOT / 'data/raw/us_training_v2/progress.jsonl.gz', lines=True, dtype={'asd_code': str})
        return {'scopes': ['KS_wheat_winter'], 'fn': lambda f: X.add_wheat_calendar(f, prog), 'columns': X.E4_COLUMNS, 'replace_weather': True}
    raise ValueError(name)


EXTRA_PIPELINES = ('e3_ne_irrigation', 'e4_ks_calendar')
WEATHER_PIPELINES = {'e2a_gridmet_point': 'weather_features_gridmet_point.csv.gz', 'e2b_gridmet_cdl': 'weather_features_gridmet_cdl.csv.gz'}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('pipeline', choices=list(PIPELINES) + ['mismatch_diag', *EXTRA_PIPELINES, *WEATHER_PIPELINES])
    a = ap.parse_args(argv)
    if a.pipeline in WEATHER_PIPELINES:
        b = RowBuilder(weather=pd.read_csv(OUT / WEATHER_PIPELINES[a.pipeline], dtype={'fips': str}))
        run_development(a.pipeline, b, ('in_season',), 'availability_replay', None)
        return
    b = RowBuilder()
    if a.pipeline in EXTRA_PIPELINES:
        run_development(a.pipeline, b, ('in_season',), 'availability_replay', None, extra=extra_spec(a.pipeline))
        return
    if a.pipeline == 'mismatch_diag':
        run_development('mismatch_diag', b, ('in_season',), 'mismatch_diag', population(b, 'V1'))
        return
    spec = PIPELINES[a.pipeline]
    run_development(a.pipeline, b, spec['kinds'], spec['mode'], population(b, spec['population']), policies=spec['policies'])


if __name__ == '__main__':
    main()
