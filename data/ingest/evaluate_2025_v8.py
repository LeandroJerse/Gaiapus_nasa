"""Evaluation of v8 on harvest 2025 (protocol 'independent_evaluation'), staged and resumable.

freeze   (development data only) for every crop x region scope: the v8 pool with the lower
         development MAE, its configuration reselected on all development years (training <= 2021,
         internal validation 2020-2021), the feature columns, sources, preprocessing, exclusions,
         coverage limits, out-of-distribution rule, baselines and decision rule are written to
         data/frozen/v8_frozen_choices.json, which is committed before any 2025 label is read.
predict  (no 2025 label read) builds 2025 features with the training sources, refits the frozen
         choices and every baseline on harvests <= 2023 and writes one predictions checkpoint per
         scope with its sha256 in the run log. Interrupted downloads/fits resume from checkpoints.
labels   fetches PAM 2025 into a sealed folder (immutable cache with receipts).
score    joins labels to the logged predictions (checksums verified) and writes one score file per
         scope; a score file is never overwritten, so the original result is preserved.
The frozen file hash is recorded at the first stage and checked at every later stage.
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from data.ingest import cohort_v5 as v5
from data.ingest import gpu
from data.ingest import train_v7 as v7
from data.ingest.cohort_v8 import ContaminationError, load_development, validate
from data.ingest.evaluate_v8 import SIMPLE, bootstrap, harvest_table, summary
from data.ingest.train_v8 import OUT, PROTOCOL, SCOPES, feature_sets, fit_predict, fixed_baselines, select
from data.ingest.water_balance_run import ROOT, save_json, sha

FROZEN = ROOT / 'data/frozen/v8_frozen_choices.json'
SEALED = ROOT / 'data/raw/v8_eval_2025'
RESULT = OUT / 'evaluation_2025'
YEAR = 2025
CATEGORIES = {'soy': (5457, 'c782', '40124'), 'maize2': (839, 'c81', '114254'), 'coffee_arabica': (1613, 'c82', '31619'),
              'coffee_total_mg': (1613, 'c82', '2723'), 'coffee_conilon': (1613, 'c82', '31620')}
CODE = ['data/ingest/evaluate_2025_v8.py', 'data/ingest/train_v8.py', 'data/ingest/train_v7.py', 'data/ingest/train_v6.py',
        'data/ingest/cohort_v5.py', 'data/ingest/cohort_v7.py', 'data/ingest/cohort_v8.py', 'data/serving/live_features.py',
        'data/ingest/hydro_training_features.py', 'data/models/water_balance.py', 'data/ingest/crops.py']


def scope_mask(scope):
    states = SCOPES[scope]['states']
    return (lambda f: f.state_code.isin(states)) if states else (lambda f: f.state_code == f.state_code)


def kind_of(crop):
    return 'coffee' if crop.startswith('coffee') else 'annual'


# ---------------------------------------------------------------- freeze

def frozen_rules():
    """Everything fixed before 2025 labels, common to all scopes."""
    return {
        'sources': {'rain': 'NASA POWER IMERG_PRECTOT (IMERG Final V07), daily UTC', 'radiation': 'NASA POWER ALLSKY_SFC_SW_DWN (CERES)',
                    'meteorology': 'NASA POWER T2M_MIN, T2M_MAX, RH2M, WS2M, T2MDEW, PS',
                    'et0': 'FAO-56 Penman-Monteith daily (data/models/water_balance.et0_daily) from the POWER inputs',
                    'operational_sources_used': False, 'rain_correction': 'none', 'source_selection_on_2025': 'none'},
        'preprocessing': {'imputation': 'median, fitted on the training rows of each model only',
                          'scaling': 'standardization fitted on the training rows only; MLP target = residual / training residual std',
                          'history_base': 'Ridge alpha 10 per cutoff on harvest_year and published lags; a cutoff without varying features predicts its training mean',
                          'reference_base': 'published reference; missing -> mean yield of the same state over the training rows of the fit '
                                            '(state absent from training -> mean of all training rows)',
                          'feature_order': 'columns of the frozen feature set in the listed order, minus columns constant in training '
                                           '(train_v6.usable), plus horizon_days for the MLP; the exported artifact stores the final order',
                          'training_window': 'all harvests of the pool up to the cutoff year (no window selection; learning curve is exploratory)',
                          'seeds': 'MLP selection seed 11, final ensemble seeds from the protocol; HGB random_state 42',
                          'determinism': 'CUDA deterministic algorithms, CUBLAS_WORKSPACE_CONFIG=:4096:8, fixed data order and torch generators',
                          'statistics_from_2025': 'none'},
        'exclusions': {'municipalities': 'development cohort municipalities of the scope (same selection as v6/v7 cohorts)',
                       'rows': 'a cutoff is dropped when any daily input of its window is missing in POWER (no gap filling)',
                       'labels': 'harvest scored only when PAM 2025 area, production and yield are present and consistent (label_valid)',
                       'spatial_holdout': 'scored separately; never in training'},
        'coverage_limits': {'harvest_year': YEAR, 'annual_cutoffs': '30-105 days after the state sowing calendar (hypothesis)',
                            'coffee_cutoffs': 'pre_flowering, post_flowering, grain_filling milestones'},
        'out_of_distribution': 'no truncation or clipping of inputs or predictions; rows with any required feature outside the '
                               'training [min, max] are counted and reported, never removed',
        'training_for_evaluation': 'pool harvests <= 2023 outside the spatial holdout; 2022-2023 rows (already opened test) used for fitting only',
        'baselines': 'all simple references of the protocol available for the crop, fitted history, v7 configurations, on the same keys',
        'decision_rule': {'primary_reference': 'strongest simple reference of the development validation (per scope)',
                          'confirmed': 'IC95 upper bound of MAE(v8) - MAE(reference) < 0 on municipalities used in training',
                          'promotion': PROTOCOL['promotion_criteria']},
        'classification': 'retrospective with documented prior exposure (availability.json, pam2025_exposure.json)',
        'chronology': {'development': 'harvests <= 2021 (selection and external folds 2019-2021)',
                       'evaluation_fit': 'harvests <= 2023', 'lag_history_loaded': 'PAM <= 2024',
                       'lag_rule': 'a PAM year enters a cutoff only if released before the decision (cutoff + 1 day); '
                                   'PAM 2024 (released 2025-09-11) enters no 2025 cutoff'},
        'prospective_confirmation': {
            'eligible_unit': 'crop x municipality x harvest x cutoff whose decision date is later than the commit of the frozen file '
                             '(prediction issued before the outcome can exist), or, as a weaker level, a harvest whose PAM is still '
                             'unpublished when predictions are issued and hashed',
            'issue_rule': 'both conditions: (a) prediction written and hashed on or before the decision date; (b) every input '
                          'available at that date (operational sources, available_at <= decision_at); never recomputed after the PAM release',
            'provenance': 'store with each prediction the product and version of every source (e.g. IMERG Late granule name and '
                          'version, POWER request time, ERA5 retrieval time, PAM release used for lags) and its retrieval timestamp',
            'outcome_rule': 'scored once against the first PAM release of that harvest, with the same references and criteria',
            'selection_of_harvests': 'chosen by these dates at issue time, not fixed in advance',
        },
    }


def freeze():
    device, _ = gpu.require_cuda()
    choices = {}
    for name, scope in SCOPES.items():
        decision = json.loads((OUT / name / 'decision.json').read_text(encoding='utf-8'))
        mae = decision['mae_in_training']
        pools = [p for p in scope['pools'] if f'v8_{p}' in mae]
        pool = min(pools, key=lambda p: mae[f'v8_{p}'])
        frame = load_development(scope['crop'])
        mask = scope_mask(name)
        pool_frame = frame if pool == 'national' else frame[mask(frame)]
        train = pool_frame[~pool_frame.spatial_holdout]
        kind = kind_of(scope['crop'])
        weighting = 'state50' if scope['states'] and len(scope['states']) > 1 else 'harvest'
        choice, info = select(train, mask, kind, weighting, device)
        base_cols, sets = feature_sets(kind)
        dev = next(d for d in decision['decisions'] if d['candidate'] == f'v8_{pool}')
        scoped = frame[mask(frame)]
        choices[name] = {'crop': scope['crop'], 'states': scope['states'], 'pool': pool,
                         'choice': {k: v for k, v in choice.items() if k != 'score'}, 'inner_score': choice['score'], 'selection': info,
                         'feature_columns': {'base': base_cols, 'correction': sets.get(choice['features'], [])},
                         'mlp_seeds': list(PROTOCOL['candidates']['learners']['mlp']['final_seeds']) if choice['learner'] == 'mlp' else None,
                         'municipalities': sorted(scoped.municipality_code.unique().tolist()),
                         'inputs': input_allowlist(scope['crop'], sorted(scoped.municipality_code.unique().tolist())),
                         'cutoffs': sorted(int(h) for h in scoped.horizon_days.unique()),
                         'development_status': dev['status'], 'development_reference': dev['strongest_reference'],
                         'development_mae': {k: round(v, 1) for k, v in mae.items()},
                         'baselines': [*[c for c in SIMPLE if c in mae], *[c for c in mae if c.startswith('v7_')]]}
        print(name, pool, choice['base'], choice['features'], choice['learner'], choice['shrink'], flush=True)
    save_json(FROZEN, {'frozen_at': datetime.now(timezone.utc).isoformat(), 'protocol': 'data/frozen/training_v8_protocol.json',
                       'protocol_sha256': sha(ROOT / 'data/frozen/training_v8_protocol.json'),
                       'code_sha256': {p: sha(ROOT / p) for p in CODE}, 'rules': frozen_rules(), 'choices': choices,
                       'evaluation': {'harvest_year': YEAR, 'pam_release': '2026-09-17'}})


# ---------------------------------------------------------------- data

def fetch_labels(crop, states):
    from data.ingest.cohort_v7 import parse_labels
    table, classification, category = CATEGORIES[crop]
    labels = {}
    for state in states:
        url = f'https://apisidra.ibge.gov.br/values/t/{table}/n6/in%20n3%20{state}/v/216,214,112/p/{YEAR}/{classification}/{category}'
        payload = v5.fetch(f'pam_{crop}_{state}_{YEAR}.json', url, root=SEALED)
        items = v5.parse_labels(payload, crop, years=(YEAR, YEAR)) if crop in v5.CROPS else parse_labels(payload, crop, category, years=(YEAR, YEAR))
        for item in items:
            if item['label_valid']:
                labels[item['municipality_code']] = item['yield_kg_ha']
    return labels


def label_files(crop):
    """The only label files the predict stage may read for `crop` (PAM <= 2024)."""
    if crop in ('soy', 'maize2'):
        return [(p, (2001, 2021) if p.parent.name == 'training_v6' else (2022, 2024))
                for folder in (ROOT / 'data/raw/training_v6', ROOT / 'data/raw/training_v6/sealed_test')
                for p in sorted(folder.glob(f'pam_{crop}_*.json')) if not p.name.endswith('.manifest.json')]
    return [(ROOT / 'data/raw/training_v7/labels_all.csv', None), (ROOT / 'data/raw/training_v7/sealed_test/labels_test.csv', None)]


def frame_files(crop):
    if crop in ('soy', 'maize2'):
        return [ROOT / 'data/processed/training_v6/samples.parquet', ROOT / 'data/processed/training_v6/test_samples.parquet']
    return [ROOT / 'data/processed/training_v7/samples.parquet', ROOT / 'data/processed/training_v7/test_samples.parquet']


def static_files(crop):
    return [ROOT / 'data/raw/training_v6/locations.json', ROOT / 'data/raw/training_v7/locations.json',
            v7.OUT / f'validation/{crop}/cohort.json', ROOT / 'data/processed/training_v8/audit.json']


def input_allowlist(crop, codes):
    """Relative path -> sha256 of every file the predict stage reads for one crop."""
    paths = [p for p, _ in label_files(crop)] + frame_files(crop) + static_files(crop) + [SEALED / 'power' / f'{c}.json' for c in codes]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}


def reject_2025_yields(path):
    """Raise if a file holds any yield of harvest >= 2025 (SIDRA payload, label table or sample table)."""
    import pandas as pd
    if path.suffix == '.json':
        payload = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(payload, list):
            late = [r for r in payload[1:] if isinstance(r, dict) and str(r.get('D3C', '')).isdigit() and int(r['D3C']) >= YEAR]
            if late:
                raise ContaminationError(f'{path.name}: {len(late)} PAM rows of {YEAR} or later')
        elif 'properties' in payload and 'yield' in json.dumps(payload.get('properties', {})).lower():
            raise ContaminationError(f'{path.name}: weather payload with a yield field')
        return
    frame = pd.read_parquet(path) if path.suffix == '.parquet' else pd.read_csv(path, low_memory=False)
    if 'harvest_year' in frame and 'yield_kg_ha' in frame and (frame.harvest_year[frame.yield_kg_ha.notna()] >= YEAR).any():
        raise ContaminationError(f'{path.name}: yields of harvest {YEAR} or later')


def verify_inputs(crop, frozen_inputs):
    """Every predict input must be allowlisted at freeze, unchanged, and free of 2025 yields."""
    for relative, digest in frozen_inputs.items():
        path = ROOT / relative
        if not path.exists() or sha(path) != digest:
            raise ContaminationError(f'{relative} missing or changed since the freeze')
        if not relative.startswith('data/raw/v8_eval_2025/power/') and path.suffix in ('.json', '.csv', '.parquet') \
                and 'locations' not in path.name and 'cohort' not in path.name and 'audit' not in path.name:
            reject_2025_yields(path)
    return True


def label_history(crop, allowed=None):
    """Published yields up to 2024 (development labels plus the already-opened 2022-2024 files); never 2025."""
    import pandas as pd
    history = {}
    for path, years in label_files(crop):
        if allowed is not None and path.relative_to(ROOT).as_posix() not in allowed:
            raise ContaminationError(f'{path} is not in the predict allowlist')
        if years:
            for item in v5.parse_labels(json.loads(path.read_bytes()), crop, years=years):
                if item['label_valid']:
                    history.setdefault(item['municipality_code'], {})[item['harvest_year']] = item['yield_kg_ha']
        else:
            frame = pd.read_csv(path, dtype={'municipality_code': str})
            for row in frame[(frame.crop == crop) & frame.label_valid].itertuples():
                history.setdefault(row.municipality_code, {})[int(row.harvest_year)] = float(row.yield_kg_ha)
    return guard_history(history)


def guard_history(history, year=YEAR):
    """Lags of harvest `year` may only come from earlier harvests; its own label must never be there."""
    late = sorted({y for h in history.values() for y in h if y >= year})
    if late:
        raise ContaminationError(f'label history contains harvests {late}; lags of {year} must come from earlier harvests')
    return history


def weather(code, locations, fetch=True):
    """POWER 2023-2025 for one municipality, cached under the sealed folder (resumable, retried)."""
    import time
    from data.serving.live_features import fetch_power
    path = SEALED / 'power' / f'{code}.json'
    if not path.exists():
        if not fetch:
            return None
        loc = locations[code]
        for attempt in range(6):
            try:
                payload = fetch_power(loc['latitude'], loc['longitude'], date(2023, 1, 1), date(2025, 12, 31))
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(10 * 2 ** attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.part')
        tmp.write_text(json.dumps(payload), encoding='utf-8')
        tmp.replace(path)
    return v5.parse_power(json.loads(path.read_text(encoding='utf-8')), code, date(2023, 1, 1), date(2025, 12, 31))


def features_2025(crop, codes, locations, history, workers=3):
    import pandas as pd
    from data.ingest.cohort_v7 import COFFEE
    from data.ingest.crops import horizons
    from data.serving.live_features import feature_row

    def rows(code):
        cached = weather(code, locations, fetch=False)
        if cached is None:
            raise ContaminationError(f'weather of {code} is not in the frozen allowlist; no download inside predict')
        w, elevation = cached
        cutoffs = sorted({(date(YEAR + o, m, d) - date(YEAR - 1, 6, 1)).days + 1 for m, d, o in COFFEE[crop]['milestones'].values()}) \
            if crop in COFFEE else horizons(crop)
        out = []
        for horizon in cutoffs:
            row = feature_row(crop, code, YEAR, horizon, w, history.get(code, {}), locations[code], elevation)
            if row is not None:
                out.append({**row, 'sample_id': f'{code}:{crop}:{YEAR}', 'state_code': code[:2], 'spatial_holdout': locations[code]['spatial_holdout']})
        return out
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(rows, codes))
    frame = pd.DataFrame([r for group in results for r in group])
    if crop not in COFFEE:
        frame['ref_lag_mean_kg_ha'] = frame.yield_lag2to4_mean_kg_ha
        frame['milestone'] = 'day_' + frame.horizon_days.astype(str)
    return check_target(frame, crop)


def check_target(frame, crop, year=YEAR):
    """Rows to score: only harvest `year`, lags published before each decision, and no label column."""
    if set(frame.harvest_year.unique()) != {year}:
        raise ContaminationError(f'target must hold harvest {year} only, got {sorted(frame.harvest_year.unique())}')
    if 'yield_kg_ha' in frame:
        raise ContaminationError('2025 features must be built without the 2025 label')
    return validate(frame, crop, last_year=year)


def locations_with_holdout():
    out = {}
    for path in (ROOT / 'data/raw/training_v6/locations.json', ROOT / 'data/raw/training_v7/locations.json'):
        for item in json.loads(path.read_text(encoding='utf-8')):
            out[item['municipality_code']] = {'latitude': item['latitude'], 'longitude': item['longitude'], 'spatial_holdout': item['spatial_holdout']}
    return out


def training_frame(crop):
    import pandas as pd
    dev = load_development(crop)
    path = frame_files(crop)[1]
    opened = pd.read_parquet(path, filters=[('crop', '==', crop)])
    opened = opened[opened.harvest_year <= YEAR - 2].copy()
    if 'milestone' not in opened:
        opened['milestone'] = 'day_' + opened.horizon_days.astype(str)
    if crop in ('soy', 'maize2'):
        opened['ref_lag_mean_kg_ha'] = opened.yield_lag2to4_mean_kg_ha
    return validate(pd.concat([dev, opened], ignore_index=True), crop, last_year=YEAR - 2)


# ---------------------------------------------------------------- run log and checkpoints

def log(event, **fields):
    RESULT.mkdir(parents=True, exist_ok=True)
    with (RESULT / 'run_log.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}) + '\n')


def logged(event):
    path = RESULT / 'run_log.jsonl'
    if not path.exists():
        return []
    return [r for r in map(json.loads, path.read_text(encoding='utf-8').splitlines()) if r['event'] == event]


def frozen_checked():
    """Load the frozen choices; the hash must match the one recorded when the evaluation started."""
    digest = sha(FROZEN)
    started = logged('started')
    if not started:
        log('started', frozen_sha256=digest)
    elif started[0]['frozen_sha256'] != digest:
        raise ContaminationError('frozen choices changed after the evaluation started')
    return json.loads(FROZEN.read_text(encoding='utf-8'))


def predict(scopes=None):
    """Stage 1: predictions for every 2025 row, before any 2025 label exists locally for this run."""
    import pandas as pd
    frozen = frozen_checked()
    device, _ = gpu.require_cuda()
    locations = locations_with_holdout()
    done = {r['scope'] for r in logged('predictions_written')}
    for name, entry in frozen['choices'].items():
        if (scopes and name not in scopes) or name in done:
            continue
        scope, crop = SCOPES[name], entry['crop']
        kind, mask = kind_of(crop), scope_mask(name)
        verify_inputs(crop, entry['inputs'])
        log('inputs_verified', scope=name, files=len(entry['inputs']))
        history = label_history(crop, allowed=entry['inputs'])
        target = features_2025(crop, entry['municipalities'], locations, history)
        train_all = training_frame(crop)
        pool_frame = train_all if entry['pool'] == 'national' else train_all[mask(train_all)]
        train = pool_frame[~pool_frame.spatial_holdout]
        choice = dict(entry['choice'])
        if choice['learner'] == 'mlp':
            choice['params'] = {**choice['params'], 'hidden': tuple(choice['params']['hidden'])}
        prediction, mlp = fit_predict(choice, train, target, kind, device)
        keep = ['sample_id', 'municipality_code', 'state_code', 'harvest_year', 'horizon_days', 'spatial_holdout']
        outputs = [target[keep].assign(candidate=f'v8_{entry["pool"]}', predicted_kg_ha=prediction)]
        if mlp:
            outputs += [target[keep].assign(candidate=f'v8_{entry["pool"]}_seed{i}', predicted_kg_ha=p) for i, p in enumerate(mlp['per_seed'])]
        first = scope['pools'][0]  # baselines use the same training pool as in development
        base_frame = train_all if first == 'national' else train_all[mask(train_all)]
        v7_info = json.loads((v7.OUT / f'validation/{crop}/cohort.json').read_text(encoding='utf-8'))
        for baseline, pred in fixed_baselines(base_frame[~base_frame.spatial_holdout], target, kind, scope, v7_info, device).items():
            outputs.append(target[keep].assign(candidate=baseline, predicted_kg_ha=pred))
        table = pd.concat(outputs, ignore_index=True)
        ranges = {}
        needed = [c for c in {*entry['feature_columns']['base'], *entry['feature_columns']['correction']} if c in train and c in target]
        outside = pd.Series(False, index=target.index)
        for column in needed:
            low, high = train[column].min(), train[column].max()
            flag = target[column].notna() & ((target[column] < low) | (target[column] > high))
            outside |= flag
            if flag.any():
                ranges[column] = int(flag.sum())
        path = RESULT / f'predictions_{name}.parquet'
        table.to_parquet(path)
        log('predictions_written', scope=name, file=path.name, sha256=sha(path), rows=len(target),
            municipalities_with_features=int(target.municipality_code.nunique()), municipalities_in_scope=len(entry['municipalities']),
            train_rows=len(train), train_last_year=int(train.harvest_year.max()), rows_outside_training_range=int(outside.sum()),
            outside_by_feature=ranges)
        print('predicted', name, len(target), 'rows', flush=True)


def labels(scopes=None):
    """Stage 2: PAM 2025 into the sealed folder, only after every prediction checkpoint exists."""
    frozen = frozen_checked()
    written = {r['scope'] for r in logged('predictions_written')}
    wanted = [n for n in frozen['choices'] if not scopes or n in scopes]
    missing = [n for n in wanted if n not in written]
    if missing:
        raise SystemExit(f'predictions missing for {missing}; run the predict stage first')
    for name in wanted:
        entry = frozen['choices'][name]
        out = SEALED / f'labels_{name}.json'
        if out.exists():
            continue
        found = fetch_labels(entry['crop'], sorted({c[:2] for c in entry['municipalities']}))
        save_json(out, {c: found[c] for c in entry['municipalities'] if c in found})
        log('labels_fetched', scope=name, file=out.name, sha256=sha(out))


def score(scopes=None):
    """Stage 3: write-once scores per scope; existing score files are kept as the original result."""
    import pandas as pd
    frozen = frozen_checked()
    rules = frozen['rules']['decision_rule']
    for name, entry in frozen['choices'].items():
        if scopes and name not in scopes:
            continue
        out = RESULT / f'score_{name}.json'
        if out.exists():
            print(name, 'already scored; original result kept', flush=True)
            continue
        written = [r for r in logged('predictions_written') if r['scope'] == name][-1]
        path = RESULT / written['file']
        if sha(path) != written['sha256']:
            raise ContaminationError(f'{path.name} changed after it was logged')
        label_entry = [r for r in logged('labels_fetched') if r['scope'] == name][-1]
        label_path = SEALED / label_entry['file']
        if sha(label_path) != label_entry['sha256']:
            raise ContaminationError(f'{label_path.name} changed after it was logged')
        found = json.loads(label_path.read_text(encoding='utf-8'))
        table = pd.read_parquet(path)
        table['yield_kg_ha'] = table.municipality_code.map(found)
        scored = table[table.yield_kg_ha.notna()]
        scope = SCOPES[name]
        weighting = 'state50' if scope['states'] and len(scope['states']) > 1 else 'harvest'
        h = harvest_table(scored[~scored.candidate.str.contains('_seed')])
        inside, held = h[~h.spatial_holdout], h[h.spatial_holdout]
        candidate = f'v8_{entry["pool"]}'
        mae = {c: summary(inside[inside.candidate == c], weighting) for c in sorted(inside.candidate.unique())}
        comparisons = {}
        for ref in [c for c in mae if c != candidate]:
            p, lo, hi, n = bootstrap(inside[inside.candidate == candidate], inside[inside.candidate == ref], weighting)
            states = {s: float((sub[sub.candidate == candidate].abs_error.mean() - sub[sub.candidate == ref].abs_error.mean())
                               / sub[sub.candidate == ref].abs_error.mean()) for s, sub in inside.groupby('state_code')}
            comparisons[ref] = {'reference_mae': mae[ref]['mae_kg_ha'], 'relative_reduction': (mae[ref]['mae_kg_ha'] - mae[candidate]['mae_kg_ha']) / mae[ref]['mae_kg_ha'],
                                'delta_kg_ha': p, 'ci95': [lo, hi], 'paired_harvests': n, 'relative_change_by_state': states}
        reference = entry['development_reference']
        primary = comparisons[reference]
        criteria = rules['promotion']
        promoted = (primary['relative_reduction'] >= criteria['relative_mae_reduction_min'] and primary['ci95'][1] < 0
                    and all(v <= criteria['no_priority_state_worse_than'] for v in primary['relative_change_by_state'].values()))
        held_delta = bootstrap(held[held.candidate == candidate], held[held.candidate == reference], weighting)[:3] if len(held) else None
        seeds = sorted(c for c in scored.candidate.unique() if c.startswith(candidate + '_seed'))
        seed_mae = [summary(harvest_table(scored[(scored.candidate == s) & ~scored.spatial_holdout]), weighting)['mae_kg_ha'] for s in seeds]
        coverage = {'municipalities_in_scope': len(entry['municipalities']),
                    'municipalities_with_features': int(table.municipality_code.nunique()),
                    'municipalities_with_valid_2025_label': len(found),
                    'harvests_scored_in_training_municipalities': int(inside[inside.candidate == candidate].sample_id.nunique()),
                    'harvests_scored_held_out': int(held[held.candidate == candidate].sample_id.nunique()),
                    'rows_outside_training_range': written['rows_outside_training_range']}
        result = {'scope': name, 'crop': entry['crop'], 'states': entry['states'], 'candidate': candidate, 'classification': 'retrospective',
                  'coverage': coverage, 'mae_in_training': mae, 'primary_reference': reference, 'vs_primary': primary,
                  'vs_each_reference': comparisons, 'held_out_delta_ci95': held_delta,
                  'seed_mae_range': [min(seed_mae), max(seed_mae)] if seed_mae else None,
                  'confirmed': primary['ci95'][1] < 0, 'promoted': promoted, 'development_status': entry['development_status'],
                  'predictions_sha256': written['sha256'], 'labels_sha256': label_entry['sha256'], 'frozen_sha256': sha(FROZEN)}
        save_json(out, result)
        log('scored', scope=name, file=out.name, sha256=sha(out))
        print(name, f"{candidate} vs {reference}: {100 * primary['relative_reduction']:.1f}% delta {primary['delta_kg_ha']:.1f} "
                    f"[{primary['ci95'][0]:.1f}; {primary['ci95'][1]:.1f}] confirmed={result['confirmed']} promoted={promoted}", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('freeze', 'predict', 'labels', 'score'))
    parser.add_argument('--scopes', default=None)
    args = parser.parse_args()
    from data.ingest import evaluate_2025_v8 as module
    scopes = args.scopes.split(',') if args.scopes else None
    module.freeze() if args.step == 'freeze' else getattr(module, args.step)(scopes)
