"""US v2 product: post-confirmation refit and calculated scenarios for harvest years 2026-2028 (research status).

Issued at the real UTC run time. Labels published by the issue date only (2025 via the C1-gated accessor).
Outputs are climatological scenarios conditioned on history, not weather forecasts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from data.forecasting.weather_ensemble import build_ensemble
from data.ingest import us_v2_chain as C
from data.ingest import us_v2_features as F
from data.ingest import us_v2_freeze as Z
from data.ingest import us_v2_scenarios as S
from data.ingest import us_v2_train as T
from data.ingest import us_v2_weather as wx
from data.ingest.us_v2_confirm import released_labels_after_opening
from data.models import water_balance as WB

PRODUCT = T.OUT / 'product'
MODELS = T.ROOT / 'data/models/research_us_v2/product'
WB_CROP = {'maize': 'maize1_us', 'soy': 'soy_us', 'wheat_winter': 'wheat_winter_us'}
WB_CAPACITY_CLASS, WB_INITIAL_FRACTION = 'AD4', 0.5
STRESS_EVENT_DAYS = 20  # event: >= 20 days with Ks < 1 in the reproductive phase
CONTRACT_VERSION = 'us_yield_scenarios_v1'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def product_yields() -> pd.DataFrame:
    dev = T.load_yields()[['fips', 'state', 'crop', 'year', 'yield_kg_ha']]
    c25 = released_labels_after_opening()[['fips', 'crop', 'year', 'yield_kg_ha']]
    c25['state'] = c25.fips.str[:2].map({'19': 'IA', '31': 'NE', '20': 'KS'})
    return pd.concat([dev, c25], ignore_index=True)


def model_kind(policy: str, window_start: date, window_end: date, issued: date, hist_age: float | None) -> str:
    if issued > window_end:
        return 'in_season'
    if policy == 'B':
        return 'shared'
    return 'pre_6m' if (hist_age is not None and hist_age <= 2) else 'pre_18m'


def fit_product(builder, scope, sel, device):
    """Refit with every label published by the product issue date (crop years <= 2025), all eligible counties."""
    state, crop = T.SCOPES[scope]
    frame = builder.rows(state, crop, range(2001, 2026), F.ISSUE_KINDS, max_label_year=2025)
    frame = frame.assign(spatial_holdout=False)
    policy = sel['policy']
    features = T.HIST + T.SPACE + F.WEATHER_COLUMNS + (T.POLICY_B_EXTRA if policy == 'B' else [])
    models = {}
    for kind in F.ISSUE_KINDS:
        group = [kind] if policy == 'A' else list(F.ISSUE_KINDS)
        fit = pd.concat([frame[(frame.issue_kind == k) & frame.yield_kg_ha.notna()] for k in group])
        name = sel['F1_in_season'] if kind == 'in_season' else sel[f'{kind}_weather_model']
        ref = sel[f'{kind}_history_only']
        models[kind] = {'weather_model': Z.fit_final(fit, features, name, device), 'model_name': name,
                        'history_only': Z.fit_final(fit, features, ref, device), 'history_only_name': ref,
                        'features': features, 'fit_rows': int(len(fit)), 'fit_years': [int(fit.year.min()), int(fit.year.max())]}
    if policy == 'B':
        models['shared'] = models['pre_6m']
    return models


def water_balance_members(daily: dict, crop: str, sowing: date, latitude: float, keep: dict | None = None) -> pd.DataFrame:
    cfg = WB.CROPS[WB_CROP[crop]]
    n = sum(cfg['phase_days'])
    cap = cfg['cad_mm'][WB_CAPACITY_CLASS]
    rows = []
    keep = {} if keep is None else keep
    for member_id, mf in daily.items():
        mf = mf[mf.date >= sowing.isoformat()].head(n)
        if len(mf) < n or mf.et0_mm.isna().any():
            rows.append({'member_id': member_id, 'wb_status': 'unavailable_incomplete_cycle'})
            continue
        sim = WB.simulate([{'day_utc': d, 'rain_mm': float(r), 'et0_mm': float(e)} for d, r, e in zip(mf.date, mf.PRECTOTCORR, mf.et0_mm)],
                          WB_CROP[crop], cap, WB_INITIAL_FRACTION)
        s = pd.DataFrame(sim)
        if (s.storage_end_mm < -1e-9).any() or s.mass_residual_mm.abs().max() > 1e-6:
            raise ValueError('water balance did not close')
        keep[member_id] = s[['day_utc', 'phase', 'rain_mm', 'et0_mm', 'etc_mm', 'eta_mm', 'et_deficit_mm', 'storage_end_mm', 'drainage_mm', 'ks', 'mass_residual_mm']]
        rep = s[s.phase == 'reproductive']
        rows.append({'member_id': member_id, 'wb_status': 'ok', 'cycle_rain_mm': float(s.rain_mm.sum()), 'cycle_et0_mm': float(s.et0_mm.sum()),
                     'cycle_eta_mm': float(s.eta_mm.sum()), 'cycle_et_deficit_mm': float(s.et_deficit_mm.sum()),
                     'reproductive_stress_days': int((rep.ks < 1).sum()), 'reproductive_et_deficit_mm': float(rep.et_deficit_mm.sum()),
                     'max_mass_residual_mm': float(s.mass_residual_mm.abs().max())})
    return pd.DataFrame(rows)


def scenario_case(fips, state, crop, year, issued, models, policy, history_obj, observed, hist, residual_q, county):
    start, end = wx.window(state, crop, year)
    h = F.history_at_issue(hist, crop, year, issued)
    cfg = WB.CROPS[WB_CROP[crop]]
    cycle_end = start + timedelta(days=sum(cfg['phase_days']) - 1)
    valid = observed.dropna(subset=F.FEATURE_VARS)
    data_through = date.fromisoformat(valid.date.max())
    as_of = min(issued - timedelta(days=1), data_through)  # weather information limit (POWER latency)
    weather_issue = as_of + timedelta(days=1)
    horizon = 'in_season' if as_of >= end else ('pre_18m' if (start - issued).days > 365 else 'pre_6m')
    kind = 'in_season' if horizon == 'in_season' else ('shared' if policy == 'B' else horizon)
    pack = models[kind]
    base = {'fips': fips, 'state': state, 'crop': crop, 'year': year, 'issued_at': issued.isoformat(), 'lead_days': (end - issued).days,
            'latitude': county['latitude'], 'longitude': county['longitude'], **h}
    daily = {}
    if as_of >= end:
        released = F.label_available_at(crop, year)[0]
        position = 'post_harvest_pre_release' if released is None or released > issued else 'post_cutoff'
        mf, man = S.member_features(history_obj, observed, state, crop, year, weather_issue)
    elif as_of >= start:
        position = 'in_season_partial'
        mf, man = S.member_features(history_obj, observed, state, crop, year, weather_issue)
        man['wb_error'] = 'observed prefix lacks radiation (POWER latency) for FAO-56 ET0'
    else:
        position = 'pre_season'
        # one ensemble over the whole water-balance cycle; yield features use the same members up to the feature cutoff
        ens = build_ensemble(history_obj, target_start=start, target_end=cycle_end,
                             as_of=datetime(as_of.year, as_of.month, as_of.day, 23, 59, tzinfo=timezone.utc), seed=S.SEED)
        rows = []
        for m in ens.members:
            frame = S._member_frame(m)
            daily[m.member_id] = frame
            rows.append({'member_id': m.member_id, 'analog_year': m.analog_year,
                         **F.weather_features(frame[frame.date <= end.isoformat()][['date'] + F.FEATURE_VARS], start, end)})
        mf = pd.DataFrame(rows)
        man = {'observed_days': 0, 'analog_years': ens.manifest['analog_years'], 'coverage_class': ens.manifest['coverage_class'],
               'ensemble_sha256': ens.manifest['content_sha256'], 'calendar_mapping': ens.manifest['calendar_mapping'],
               'excluded_years': ens.manifest['excluded_years'], 'seed': S.SEED, 'scenario_start': start.isoformat(), 'wb_cycle_end': cycle_end.isoformat()}
    X = pd.concat([pd.DataFrame([base] * len(mf)).reset_index(drop=True), mf.reset_index(drop=True)], axis=1)
    X['issue_kind'] = kind
    yields = Z.predict_any(pack['weather_model'], X)
    ref = float(Z.predict_any(pack['history_only'], X.iloc[[0]])[0])
    kept: dict = {}
    wb = water_balance_members(daily, crop, start, county['latitude'], kept) if daily else pd.DataFrame()
    if kept:
        import gzip
        sdir = PRODUCT / 'series'
        sdir.mkdir(parents=True, exist_ok=True)
        payload = {m: {c: df[c].tolist() for c in df.columns} for m, df in kept.items()}
        payload['_analog_years'] = {k: (None if pd.isna(v) else int(v)) for k, v in zip(mf.member_id, mf.analog_year)}
        payload['_manifest'] = {k: man.get(k) for k in ('analog_years', 'coverage_class', 'calendar_mapping', 'excluded_years', 'ensemble_sha256',
                                                        'observed_days', 'scenario_start', 'seed', 'wb_cycle_end')}
        payload['_yield'] = {k: v for k, v in zip(mf.member_id, map(float, yields))}
        (sdir / f'{fips}_{crop}_{year}_{issued.isoformat()}.json.gz').write_bytes(gzip.compress(json.dumps(payload, default=float).encode(), mtime=0))
    members = mf[['member_id', 'analog_year']].assign(yield_kg_ha=yields)
    if len(wb):
        members = members.merge(wb, on='member_id', how='left')
    q = residual_q.get(horizon)
    p50 = float(np.median(yields))
    summary = {'crop': crop, 'state': state, 'model_kind': kind, 'horizon': horizon, 'model': pack['model_name'], 'position_vs_season': position,
               'members': int(len(mf)), 'analog_years': json.dumps(man.get('analog_years', [])), 'observed_days': man['observed_days'],
               'weather_as_of': as_of.isoformat(), 'hist_age': h['hist_age'], 'last_label_year': h['last_label_year'], 'hist_count': h['hist_count'],
               'hist_suppressed_in_window': h['hist_suppressed_in_window'], 'availability_status': h['availability_status'],
               'yield_p10': float(np.quantile(yields, .1)), 'yield_p50': p50, 'yield_p90': float(np.quantile(yields, .9)),
               'predictive_p10': p50 + q[0] if q else None, 'predictive_p90': p50 + q[1] if q else None,
               'reference_kg_ha': ref, 'reference_model': pack['history_only_name'], 'ensemble_sha256': man.get('ensemble_sha256'),
               'wb_error': man.get('wb_error')}
    if len(wb) and (wb.wb_status == 'ok').any():
        ok = wb[wb.wb_status == 'ok']
        summary |= {'p_reproductive_stress_ge_20d': float((ok.reproductive_stress_days >= STRESS_EVENT_DAYS).mean()),
                    'cycle_et_deficit_p50_mm': float(ok.cycle_et_deficit_mm.median())}
    return summary, members


def residual_quantiles(sel_by_scope):
    chain = pd.read_csv(C.DEV / 'dev_chain_predictions.csv.gz', dtype={'fips': str})
    main = pd.read_csv(C.DEV / 'dev_main_predictions.csv.gz', dtype={'fips': str})
    out = {}
    for scope, sel in sel_by_scope.items():
        q = {}
        for kind in C.PRE_KINDS:
            r = chain[(chain.scope == scope) & (chain.issue_kind == kind) & chain.yield_kg_ha.notna() & ~chain.spatial_holdout]
            if len(r):
                res = r.yield_kg_ha - r.p50
                q[kind] = (float(np.quantile(res, .1)), float(np.quantile(res, .9)))
        m = main[(main.scope == scope) & (main.issue_kind == 'in_season') & (main.policy == sel['policy']) & (main.model == sel['F1_in_season'])
                 & main.yield_kg_ha.notna() & ~main.spatial_holdout]
        if len(m):
            res = m.yield_kg_ha - m.pred
            q['in_season'] = (float(np.quantile(res, .1)), float(np.quantile(res, .9)))
        out[scope] = q
    return out


def run(years=(2026, 2027, 2028), fips_filter=None, log=print):
    import psutil
    proc = psutil.Process(os.getpid())
    issued_dt = datetime.now(timezone.utc)
    issued = issued_dt.date()
    sel_all = json.loads((C.DEV / 'selection_main.json').read_text())
    c1 = json.loads((T.OUT / 'c1_score.json').read_text())
    corr_path = T.OUT / 'c1_score_correction.json'
    corrected = {r['id']: r['status_corrected'] for r in json.loads(corr_path.read_text())['results']} if corr_path.exists() else {}
    by_key: dict = {}
    for r in c1['results']:
        by_key.setdefault((r['scope'], r['family']), []).append(corrected.get(r['id'], r['status']))
    # a scope/family is confirmed only if every registered comparison in it is confirmed; otherwise the weakest status
    order = ['not_executed', 'underpowered', 'reference_selected', 'gain_not_confirmed', 'research_confirmed']
    status = {k: ('research_confirmed' if all(s == 'research_confirmed' for s in v) else min((s for s in v if s != 'research_confirmed'), key=order.index))
              for k, v in by_key.items()}
    resq = residual_quantiles(sel_all)
    b = T.RowBuilder(yields=product_yields())
    dev = T.device()
    PRODUCT.mkdir(parents=True, exist_ok=True)
    summaries, member_tables, peak = [], [], 0
    t0 = time.perf_counter()
    for scope, (state, crop) in T.SCOPES.items():
        sel = sel_all[scope]
        models = fit_product(b, scope, sel, dev)
        MODELS.mkdir(parents=True, exist_ok=True)
        for kind, pack in models.items():
            joblib.dump(pack, MODELS / f'{scope}_{kind}.joblib')
        counties = [c for f, c in b.counties.items() if c['state'] == state and (fips_filter is None or f in fips_filter)]
        for county in counties:
            fips = county['fips']
            hobj, obs = S.weather_history(fips), wx.read_county(fips)
            hist = b.hist.get((fips, crop), pd.DataFrame({'year': [], 'yield_kg_ha': []}))
            gc = next(c for c in wx.counties() if c['fips'] == fips)
            for year in years:
                try:
                    summ, members = scenario_case(fips, state, crop, year, issued, models, sel['policy'], hobj, obs, hist, resq[scope], gc)
                except Exception as exc:  # noqa: BLE001
                    summaries.append({'scope': scope, 'fips': fips, 'year': year, 'error': type(exc).__name__ + ': ' + str(exc)[:200]})
                    continue
                fam = {'in_season': 'F1', 'pre_6m': 'F8', 'pre_18m': 'F9'}[summ['horizon']]
                summ |= {'scope': scope, 'fips': fips, 'location_id': f'US:fips:{fips}', 'year': year, 'county_name': gc['county_name'],
                         'scientific_status': status.get((scope, fam), 'not_evaluated'), 'status_family': fam, 'spatial_holdout_in_v2': county['spatial_holdout']}
                summaries.append(summ)
                member_tables.append(members.assign(scope=scope, fips=fips, year=year))
            peak = max(peak, proc.memory_info().rss)
        log(json.dumps({'product_scope': scope, 'cases': sum(1 for s in summaries if s.get('scope') == scope), 'seconds': round(time.perf_counter() - t0, 1)}))
    tag = issued.isoformat()
    sp, mp = PRODUCT / f'us_scenarios_summary_{tag}.csv.gz', PRODUCT / f'us_scenarios_members_{tag}.csv.gz'
    pd.DataFrame(summaries).to_csv(sp, index=False, compression={'method': 'gzip', 'mtime': 0})
    pd.concat(member_tables).to_csv(mp, index=False, compression={'method': 'gzip', 'mtime': 0})
    manifest = {'contract_version': CONTRACT_VERSION, 'issued_at': issued_dt.isoformat(), 'as_of': (issued - timedelta(days=1)).isoformat(),
                'seed': S.SEED, 'files': {sp.name: sha(sp), mp.name: sha(mp)},
                'models': {p.name: sha(p) for p in sorted(MODELS.glob('*.joblib'))},
                'inputs': {'weather': 'data/raw/us_training_v2/power*', 'labels': 'USDA qs.crops_20261114 (dev + 2025 released after C1 opening)',
                           'availability': 'data/frozen/us_v2_availability.json', 'selection': 'data/processed/us_training_v2/dev/selection_main.json',
                           'c1_score_sha256': sha(T.OUT / 'c1_score.json')},
                'water_balance': {'engine': 'data/models/water_balance.simulate (FAO-56 bucket)', 'soil_capacity_class': WB_CAPACITY_CLASS,
                                  'initial_fraction': WB_INITIAL_FRACTION, 'irrigation': 'rainfed only (NE irrigated area not represented)',
                                  'event': f'reproductive phase days with Ks<1 >= {STRESS_EVENT_DAYS}'},
                'uncertainty_included': ['weather scenario spread (analog years)', 'development residual quantiles (predictive band, labelled separately)'],
                'uncertainty_excluded': ['label revision', 'management/technology change', 'climate trend', 'model structure beyond development residuals', 'soil capacity and initial storage (hypotheses)'],
                'runtime_s': round(time.perf_counter() - t0, 1), 'peak_rss_mb': round(peak / 2**20, 1)}
    (PRODUCT / f'us_scenarios_manifest_{tag}.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return manifest


EXAMPLES = {'IA': '19169', 'NE': '31185', 'KS': '20191'}
LIMITATIONS = [
    'County-scale estimate (USDA NASS county yield), not a field or farm prediction',
    'Weather after as_of is a climatological scenario from historical analog years, not a forecast',
    'Scenario band is not a calibrated predictive interval; predictive band adds development residuals only',
    'Sowing date is a fixed regional hypothesis; Crop Progress is state-level',
    'Water balance: rainfed bucket; soil capacity and initial storage are hypotheses; irrigation not represented',
    'Label availability before 2023 uses conservative estimated release dates',
    'Research status only; no automatic promotion',
]


def _none(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


def export_examples(tag: str) -> list[Path]:
    summ = pd.read_csv(PRODUCT / f'us_scenarios_summary_{tag}.csv.gz', dtype={'fips': str})
    mem = pd.read_csv(PRODUCT / f'us_scenarios_members_{tag}.csv.gz', dtype={'fips': str})
    man_path = PRODUCT / f'us_scenarios_manifest_{tag}.json'
    man = json.loads(man_path.read_text())
    if 'error' in summ:
        summ = summ[summ.error.isna()]
    out = []
    for state, fips in EXAMPLES.items():
        for r in summ[summ.fips == fips].to_dict('records'):
            start, end = wx.window(state, r['crop'], int(r['year']))
            obs_end = date.fromisoformat(man['as_of'])
            segs = []
            if r['observed_days'] > 0:
                segs.append({'kind': 'observed', 'start': start.isoformat(), 'end': min(obs_end, end).isoformat(), 'origin': 'NASA POWER daily point (retrospective snapshot)'})
            if obs_end < end:
                segs.append({'kind': 'climatological_scenario', 'start': max(start, obs_end + timedelta(days=1)).isoformat(), 'end': end.isoformat(),
                             'origin': 'historical_analog_blocks_v1 over NASA POWER (data/forecasting/weather_ensemble.py)'})
            elig = {'pre_season': 'prospective_pre_season', 'in_season_partial': 'prospective_in_season',
                    'post_harvest_pre_release': 'post_harvest_estimate_not_skill_evidence', 'post_cutoff': 'retrospective_only'}[r['position_vs_season']]
            m = mem[(mem.fips == fips) & (mem.year == r['year']) & (mem.scope == r['scope'])]
            analog = r['analog_years']
            analog = json.loads(analog) if isinstance(analog, str) else []
            doc = {'contract_version': CONTRACT_VERSION, 'location_id': f'US:fips:{fips}', 'county_name': r['county_name'], 'state': state, 'crop_id': r['crop'],
                   'harvest_year': int(r['year']), 'season_label': f"{int(r['year']) - 1}/{int(r['year'])}" if r['crop'] == 'wheat_winter' else str(int(r['year'])),
                   'issued_at': man['issued_at'], 'as_of': man['as_of'], 'sowing_date': start.isoformat(), 'feature_cutoff': end.isoformat(),
                   'position_vs_season': r['position_vs_season'], 'operational_eligibility': elig,
                   'history': {'last_label_year': None if _none(r['last_label_year']) is None else int(r['last_label_year']),
                               'hist_age': None if _none(r['hist_age']) is None else int(r['hist_age']), 'hist_count': int(r['hist_count']),
                               'hist_suppressed_in_window': int(r['hist_suppressed_in_window']), 'availability_status': _none(r['availability_status'])},
                   'weather': {'segments': segs, 'members': int(r['members']), 'analog_years': analog, 'seed': man['seed'], 'ensemble_sha256': _none(r.get('ensemble_sha256'))},
                   'yield': {'unit': 'kg/ha', 'model': r['model'], 'model_kind': r['model_kind'],
                             'scenario_band': {'p10': r['yield_p10'], 'p50': r['yield_p50'], 'p90': r['yield_p90']},
                             'predictive_band': None if _none(r.get('predictive_p10')) is None else {'p10': r['predictive_p10'], 'p90': r['predictive_p90']},
                             'reference': {'model': r['reference_model'], 'value': r['reference_kg_ha']}, 'unavailable_reason': None},
                   'water_balance': {'engine': 'data/models/water_balance.simulate', 'soil_capacity_class': WB_CAPACITY_CLASS, 'initial_fraction': WB_INITIAL_FRACTION,
                                     'irrigation': 'rainfed only', 'p_reproductive_stress_ge_20d': _none(r.get('p_reproductive_stress_ge_20d')),
                                     'cycle_et_deficit_p50_mm': _none(r.get('cycle_et_deficit_p50_mm')), 'unavailable_reason': _none(r.get('wb_error'))},
                   'scientific_status': r['scientific_status'], 'status_family': r['status_family'],
                   'domain': {'in_v2_universe': True, 'spatial_holdout_in_v2': bool(r['spatial_holdout_in_v2'])},
                   'limitations': LIMITATIONS,
                   'provenance': {'manifest': man_path.relative_to(T.ROOT).as_posix(), 'manifest_sha256': sha(man_path)},
                   'members': [{'member_id': x['member_id'], 'analog_year': None if _none(x['analog_year']) is None else int(x['analog_year']),
                                'yield_kg_ha': float(x['yield_kg_ha']),
                                'reproductive_stress_days': None if _none(x.get('reproductive_stress_days')) is None else int(x['reproductive_stress_days']),
                                'cycle_et_deficit_mm': _none(x.get('cycle_et_deficit_mm'))} for x in m.to_dict('records')]}
            path = PRODUCT / 'examples' / f"US_fips_{fips}_{r['crop']}_{int(r['year'])}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, indent=1, default=float), encoding='utf-8')
            out.append(path)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['run', 'examples'])
    ap.add_argument('--tag')
    a = ap.parse_args()
    if a.action == 'run':
        print(json.dumps(run(), indent=1)[:2000])
    else:
        print([str(p) for p in export_examples(a.tag)])
