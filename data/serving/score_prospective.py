"""Score the v9 prospective registry once the PAM of a harvest is released (data/frozen/prospective_v9_protocol.json, amendments A1-A5).

Dates (all calendar days in Brasilia time, UTC-03:00):
  cutoff_date     = sowing (or coffee production-year start) + cutoff_days - 1
  planned_issue   = cutoff_date + 6 days (operational data lag of the live job)
  deadline        = planned_issue + 7 days (tolerance, amendment A1)
  issued_date     = Brasilia day of issued_at; must equal decision_at
A record is on time if issued_date <= deadline.

Approval uses only records that are on time, belong to an intact hash chain of sealed batches, cite a protocol version that existed
before their issue, and whose inputs were all retrieved before issue with availability proven (retrieval time recorded, or the sealed
batch committed no later than the day after the decision). Coverage is measured against the keys the candidate was expected to have
since its registration; a priority state without pairs blocks the conclusion for that scope.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from data.ingest.water_balance_run import ROOT, save_json, sha
from data.serving.prospective_registry import BRT, PROTOCOL, REGISTRY, VERSIONS, canonical, local_date

OPERATIONAL_LAG_DAYS = 6
LATE_AFTER_DAYS = 7
SEALED_GRACE_DAYS = 1
PRIORITY = {'soy': ('31', '51'), 'maize2': ('31', '51'), 'coffee_arabica': ('31',), 'coffee_total_mg': ('31',), 'coffee_conilon': ('32',)}
REQUIRED_SOURCES = ('NASA POWER', 'GPM IMERG Late', 'IBGE PAM')


# ------------------------------------------------------------------ records, chain and dates

def load_sealed():
    """All sealed records in order, with the hash chain recomputed; raises on any break."""
    import gzip
    records, previous = [], '0' * 64
    for path in sorted((REGISTRY / 'sealed').glob('*.jsonl.gz')):
        for line in gzip.decompress(path.read_bytes()).decode('utf-8').splitlines():
            record = json.loads(line)
            body = {k: v for k, v in record.items() if k != 'record_sha256'}
            if record['previous_record_sha256'] != previous or hashlib.sha256(canonical(body).encode()).hexdigest() != record['record_sha256']:
                raise ValueError(f'sealed chain broken at {record["key"]} ({path.name})')
            previous = record['record_sha256']
            records.append({**record, '_sealed_file': path.name})
    return records


def key_dates(crop, code, harvest_year, cutoff_days, sowing=None):
    from data.ingest.crops import sowing_date
    start = date.fromisoformat(sowing) if sowing else (date(harvest_year - 1, 6, 1) if crop.startswith('coffee') else sowing_date(crop, code, harvest_year))
    cutoff = start + timedelta(days=int(cutoff_days) - 1)
    planned = cutoff + timedelta(days=OPERATIONAL_LAG_DAYS)
    return {'cutoff_date': cutoff, 'planned_issue': planned, 'deadline': planned + timedelta(days=LATE_AFTER_DAYS)}


def dates(record):
    d = key_dates(record['crop'], record['municipality_code'], record['harvest_year'], record['cutoff_days'], record['sowing_date'])
    d['issued_date'] = local_date(record['issued_at'])
    d['decision_matches_issue'] = d['issued_date'].isoformat() == record['decision_at']
    return d


def on_time(record):
    return dates(record)['issued_date'] <= dates(record)['deadline']


# ------------------------------------------------------------------ provenance

def protocol_versions():
    if not VERSIONS.exists():
        return {}
    return {v['sha256']: v['at'] for v in map(json.loads, VERSIONS.read_text(encoding='utf-8').splitlines())}


def sealed_commit_times():
    """First commit time of each sealed batch in the repository (git log), as Brasilia dates."""
    import subprocess
    out = {}
    for path in sorted((REGISTRY / 'sealed').glob('*.jsonl.gz')):
        rel = path.relative_to(ROOT).as_posix()
        log = subprocess.run(['git', 'log', '--diff-filter=A', '--format=%cI', '--', rel], cwd=ROOT, capture_output=True, text=True).stdout.split()
        if log:
            out[path.name] = datetime.fromisoformat(log[-1]).astimezone(BRT).date()
    return out


def provenance_problems(record, versions, sealed_times):
    """Empty list if the record may enter an approval decision."""
    problems = []
    issued = datetime.fromisoformat(record['issued_at'])
    if not dates(record)['decision_matches_issue']:
        problems.append('decision_at differs from the Brasilia day of issued_at')
    version_at = versions.get(record.get('protocol_sha256'))
    if version_at is None:
        problems.append('protocol version unknown')
    elif datetime.fromisoformat(version_at) > issued:
        problems.append('protocol version registered after issue')
    inputs = record.get('inputs') or []
    for source in REQUIRED_SOURCES:
        if not any(i.get('source', '').startswith(source) for i in inputs):
            problems.append(f'missing provenance: {source}')
    sealed_day = sealed_times.get(record.get('_sealed_file'))
    sealed_ok = sealed_day is not None and sealed_day <= date.fromisoformat(record['decision_at']) + timedelta(days=SEALED_GRACE_DAYS)
    for item in inputs:
        if item.get('days') == 0:
            continue  # source not used for this key
        stamp = item.get('retrieved_at') or item.get('retrieved_at_max')
        if not stamp:
            problems.append(f'no retrieval time: {item.get("source")}')
            continue
        if datetime.fromisoformat(stamp) > issued:
            problems.append(f'retrieved after issue: {item.get("source")}')
        if item.get('retrieved_at_basis') != 'recorded' and not sealed_ok:
            problems.append(f'availability not proven: {item.get("source")}')
    return problems


# ------------------------------------------------------------------ expected keys

def expected_keys(model, harvest_year, catalog, protocol_registered_at):
    """Keys the model was expected to receive: covered municipality x cutoff whose planned issue is on/after its registration day."""
    from data.serving.yield_model import YieldModel
    crop = model['crop']
    item = catalog['crops'][crop]
    codes = [m['code'] for m in item['municipalities']]
    if model.get('scope_states'):
        codes = [c for c in codes if c[:2] in model['scope_states']]
    if model.get('artifact') and model['role'] == 'candidate':
        coverage = set(YieldModel((ROOT / model['artifact']).with_suffix('')).spec.get('coverage', codes))
        codes = [c for c in codes if c in coverage]
    registered = local_date(model.get('registered_at') or protocol_registered_at)
    keys = {}
    for code in codes:
        for cutoff in item['cutoffs_days']:
            d = key_dates(crop, code, harvest_year, cutoff)
            if d['planned_issue'] >= registered:
                keys[f'{crop}:{code}:{harvest_year}:{cutoff}'] = d
    return keys


# ------------------------------------------------------------------ outcomes and statistics

def fetch_outcomes(crop, harvest_year, states, today=None):
    from data.ingest import cohort_v5 as v5
    from data.ingest.audit_v8 import release_date
    from data.ingest.cohort_v7 import COFFEE, parse_labels
    from data.ingest.crops import REGISTRY as CROPS
    today = today or datetime.now(BRT).date()
    if release_date(harvest_year) > today:
        raise SystemExit(f'PAM {harvest_year} not released yet (expected around {release_date(harvest_year)}, subject to the IBGE calendar)')
    if crop in COFFEE:
        table, classification, category = 1613, 'c82', COFFEE[crop]['category']
    else:
        table, classification, category = CROPS[crop]['table'], CROPS[crop]['classification'], CROPS[crop]['category']
    folder = REGISTRY / 'outcomes' / str(harvest_year)
    labels = {}
    for state in states:
        url = f'https://apisidra.ibge.gov.br/values/t/{table}/n6/in%20n3%20{state}/v/216,214,112/p/{harvest_year}/{classification}/{category}'
        payload = v5.fetch(f'pam_{crop}_{state}_{harvest_year}.json', url, root=folder)
        for entry in parse_labels(payload, crop, category, years=(harvest_year, harvest_year)):
            if entry['label_valid']:
                labels[entry['municipality_code']] = entry['yield_kg_ha']
    return labels


def harvest_errors(records, labels, models):
    import pandas as pd
    rows = []
    for r in records:
        truth = labels.get(r['municipality_code'])
        estimates = {m: r['predictions'].get(m, {}).get('estimate_kg_ha') for m in models}
        if truth is None or any(v is None for v in estimates.values()):
            continue
        for m, value in estimates.items():
            rows.append({'model': m, 'sample': f'{r["municipality_code"]}:{r["harvest_year"]}', 'state': r['municipality_code'][:2],
                         'abs_error': abs(value - truth), 'error': value - truth})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.groupby(['model', 'sample', 'state'], as_index=False)[['abs_error', 'error']].mean()


def mae(h, weighting):
    by_state = h.groupby('state').abs_error.mean()
    return float(by_state.mean()) if weighting == 'state50' else float(h.abs_error.mean())


def bootstrap(h, a, b, weighting, draws=2000, seed=42):
    import numpy as np
    pa = h[h.model == a].set_index('sample')
    pb = h[h.model == b].set_index('sample')
    paired = pa[['state', 'abs_error']].join(pb[['abs_error']], rsuffix='_b')
    paired['delta'] = paired.abs_error - paired.abs_error_b
    groups = {s: g.delta.to_numpy() for s, g in paired.groupby('state')}
    rng = np.random.default_rng(seed)

    def stat(chosen):
        return float(np.mean([v.mean() for v in chosen.values()])) if weighting == 'state50' else float(np.concatenate(list(chosen.values())).mean())
    point = stat(groups)
    samples = np.array([stat({s: v[rng.integers(len(v), size=len(v))] for s, v in groups.items()}) for _ in range(draws)])
    return point, float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5)), float((samples >= 0).mean()), len(paired)


def holm(pvalues):
    order = sorted(pvalues, key=pvalues.get)
    adjusted, running = {}, 0.0
    for i, key in enumerate(order):
        running = max(running, min(1.0, (len(order) - i) * pvalues[key]))
        adjusted[key] = running
    return adjusted


# ------------------------------------------------------------------ scoring

def score_scope(records, labels, crop, states, candidates, references, expected=None, versions=None, sealed_times=None):
    """`expected`: {candidate: set of expected keys}; `versions`/`sealed_times` for provenance (None skips only in unit tests)."""
    weighting = 'state50' if states and len(states) > 1 else 'harvest'
    results, pvalues = {}, {}
    scoped = [r for r in records if not states or r['municipality_code'][:2] in states]
    for candidate in candidates:
        applicable = [r for r in scoped if candidate in r['predictions']]
        timely = [r for r in applicable if on_time(r)]
        rejected = {}
        if versions is not None:
            kept = []
            for r in timely:
                problems = provenance_problems(r, versions, sealed_times or {})
                if problems:
                    rejected[r['key']] = problems
                else:
                    kept.append(r)
            timely = kept
        usable = {r['key'] for r in timely if r['predictions'][candidate].get('estimate_kg_ha') is not None}
        denominator = expected.get(candidate) if expected else {r['key'] for r in applicable}
        coverage = len(usable & set(denominator)) / len(denominator) if denominator else 0.0
        entry = {'expected_keys': len(denominator), 'usable_keys': len(usable), 'coverage': coverage,
                 'late_keys': len(applicable) - len([r for r in applicable if on_time(r)]), 'provenance_rejected': len(rejected),
                 'provenance_reasons': sorted({p for v in rejected.values() for p in v})}
        h = harvest_errors(timely, labels, [candidate, *references])
        if h.empty:
            results[candidate] = {**entry, 'status': 'no_paired_outcomes', 'approved_for_operational_use': False}
            continue
        maes = {m: mae(h[h.model == m], weighting) for m in [candidate, *references]}
        versus = {}
        for ref in references:
            point, low, high, p, n = bootstrap(h, candidate, ref, weighting)
            versus[ref] = {'mae_reference': maes[ref], 'gain': (maes[ref] - maes[candidate]) / maes[ref], 'delta_kg_ha': point,
                           'ci95': [low, high], 'p_one_sided': p, 'paired_harvests': n}
        best_ref = min(references, key=maes.get)
        priority = [s for s in PRIORITY.get(crop, ()) if not states or s in states]
        state_change, missing_states = {}, []
        for state in priority:
            part = h[h.state == state]
            if part.empty:
                missing_states.append(state)
                continue
            ref_mae = part[part.model == best_ref].abs_error.mean()
            state_change[state] = float((part[part.model == candidate].abs_error.mean() - ref_mae) / ref_mae)
        pvalues[candidate] = max(v['p_one_sided'] for v in versus.values())
        results[candidate] = {**entry, 'mae': maes, 'versus': versus, 'best_simple_reference': best_ref,
                              'priority_state_change': state_change, 'priority_states_without_pairs': missing_states,
                              'paired_harvests': int(h[h.model == candidate].shape[0])}
    adjusted = holm(pvalues) if pvalues else {}
    for candidate, result in results.items():
        if 'versus' not in result:
            continue
        result['holm_adjusted_p'] = adjusted[candidate]
        blocked = bool(result['priority_states_without_pairs'])
        result['status'] = 'inconclusive_priority_state_without_pairs' if blocked else 'scored'
        result['approved_for_operational_use'] = bool(
            not blocked
            and all(v['gain'] >= 0.05 and v['ci95'][1] < 0 for v in result['versus'].values())
            and adjusted[candidate] < 0.05
            and all(c <= 0.05 for c in result['priority_state_change'].values())
            and result['paired_harvests'] >= 30 and result['coverage'] >= 0.8)
    return {'weighting': weighting, 'candidates': results}


def score(harvest_year, labels_by_crop):
    out_path = REGISTRY / 'scores' / f'{harvest_year}.json'
    if out_path.exists():
        raise SystemExit(f'{out_path.name} exists; the original score is kept')
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    catalog = json.loads((ROOT / 'dist/gaiapus-models-v7/catalog.json').read_text(encoding='utf-8'))
    records = [r for r in load_sealed() if r['harvest_year'] == harvest_year]
    versions, sealed_times = protocol_versions(), sealed_commit_times()
    report = {}
    for crop, labels in labels_by_crop.items():
        models = [m for m in protocol['models'] if m['crop'] == crop]
        references = [m['model_id'] for m in models if m['role'] == 'simple_reference']
        scopes = {}
        for m in models:
            if m['role'] in ('candidate', 'v7_catalog'):
                scopes.setdefault(tuple(m['scope_states']) if m['scope_states'] else None, []).append(m)
        crop_records = [r for r in records if r['crop'] == crop]
        report[crop] = {}
        for states, entries in scopes.items():
            expected = {m['model_id']: set(expected_keys(m, harvest_year, catalog, protocol['registered_at'])) for m in entries}
            report[crop][str(states)] = score_scope(crop_records, labels, crop, states, [m['model_id'] for m in entries], references,
                                                    expected, versions, sealed_times)
    save_json(out_path, {'harvest_year': harvest_year, 'scored_at': datetime.now(timezone.utc).isoformat(),
                         'protocol_sha256': sha(PROTOCOL), 'records': len(records), 'crops': report})
    return report
