"""v9 prospective registry: predictions recorded on their decision date, scored once after the PAM release.

Protocol (data/frozen/prospective_v9_protocol.json, written once by `init`, amended only by
`add_model`, never edited): which models are registered, the target, eligibility and the scoring and
approval rules. Every day the live job calls `register` with the ready estimates of that day:

- one record per crop x municipality x harvest x cutoff, written the first day the cutoff is reached
  (decision_at = that day); later days never rewrite it;
- eligible only if the PAM of that harvest was not published on the decision date;
- predictions of every registered model applicable to the key, computed from the same feature row;
- provenance of every input (product, version/granule, retrieval time, hash) from the live provider;
- each record hashed and chained to the previous one; a daily manifest holds the file hash.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from data.ingest.audit_v8 import release_date
from data.ingest.water_balance_run import ROOT, save_json, sha

REGISTRY = ROOT / 'data/prospective_registry'
PROTOCOL = ROOT / 'data/frozen/prospective_v9_protocol.json'
V7 = ROOT / 'dist/gaiapus-models-v7'
V8 = ROOT / 'dist/gaiapus-models-v8'
REFS = ROOT / 'dist/prospective_v9/references'
COFFEE_REFS = ('ref_last_available_kg_ha', 'ref_same_phase_kg_ha', 'ref_biennial_mean_kg_ha', 'ref_cycle_mean_kg_ha')
V8_SCOPES = {'soy': [('soy_mgmt', ('31', '51'))], 'maize2': [('maize2_mgmt', ('31', '51')), ('maize2_national', None)],
             'coffee_arabica': [('coffee_arabica', ('31',))], 'coffee_total_mg': [('coffee_total_mg', ('31',))],
             'coffee_conilon': [('coffee_conilon', ('32',))]}


VERSIONS = ROOT / 'data/frozen/prospective_v9_protocol_versions.jsonl'
# Decision dates are calendar days in Brasilia time (UTC-03:00; Brazil has no daylight saving since 2019).
BRT = timezone(timedelta(hours=-3), 'America/Sao_Paulo')


def now():
    return datetime.now(timezone.utc).isoformat()


def local_date(timestamp):
    """Brasilia calendar day of an ISO timestamp with timezone."""
    return datetime.fromisoformat(timestamp).astimezone(BRT).date()


def log_version(change, impact):
    """Append the hash of the protocol after a change (append-only history used to validate records)."""
    VERSIONS.parent.mkdir(parents=True, exist_ok=True)
    with VERSIONS.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'sha256': sha(PROTOCOL), 'at': now(), 'change': change, 'eligibility_impact': impact}) + '\n')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


# ------------------------------------------------------------------ protocol

def model_entries():
    """Registered models: served v7 per crop, frozen history reference per crop, simple references, v8 experimental scopes."""
    catalog = json.loads((V7 / 'catalog.json').read_text(encoding='utf-8'))
    models = []
    for crop, item in catalog['crops'].items():
        for scope in item['models']:
            models.append({'model_id': f'v7:{crop}:{scope["scope"]}', 'crop': crop, 'role': 'v7_catalog', 'scope': scope['scope'],
                           'scope_states': scope['states'], 'artifact': f'dist/gaiapus-models-v7/models/{scope["artifact"]}.json',
                           'sha256': next(v for k, v in scope['files'].items() if k.endswith('.json')), 'family': scope['type']})
        ref = REFS / f'history_{crop}.json'
        models.append({'model_id': f'history_ref:{crop}', 'crop': crop, 'role': 'simple_reference', 'scope_states': None,
                       'artifact': ref.relative_to(ROOT).as_posix(), 'sha256': sha(ref), 'family': 'ridge (trend + published lags)'})
        columns = COFFEE_REFS if crop.startswith('coffee') else ('ref_lag_mean_kg_ha',)
        for column in columns:
            models.append({'model_id': f'{column[:-6]}:{crop}', 'crop': crop, 'role': 'simple_reference', 'scope_states': None,
                           'column': column, 'family': 'published reference'})
    for crop, scopes in V8_SCOPES.items():
        for scope, states in scopes:
            path = V8 / 'models' / f'{scope}.json'
            spec = json.loads(path.read_text(encoding='utf-8'))
            family = f'{spec["base"]["kind"]} + {spec["residual"]["learner"]}' if spec['residual'] else spec['base']['kind']
            models.append({'model_id': f'v8:{scope}', 'crop': crop, 'role': 'candidate', 'scope_states': states,
                           'artifact': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'family': family})
    return models


def init():
    if PROTOCOL.exists():
        raise SystemExit('Protocol already registered; use add_model for amendments')
    protocol = {
        'version': 'prospective_v9', 'registered_at': now(),
        'plan': 'docs/superpowers/plans/gaiapus-v9-plano-avaliacao-operacional.md',
        'target': 'municipal yield (kg/ha) of the first IBGE PAM release of the harvest; label_valid rule of cohort_v5',
        'unit': 'crop x municipality x harvest_year x cutoff (or coffee milestone)',
        'decision_at': 'the day the live job first reaches the cutoff with operational inputs (the record issue day)',
        'eligibility': ['record written on decision_at (issued_at date == decision_at)',
                        'every input retrieved before issued_at (provenance recorded)',
                        'the PAM of the harvest not released on decision_at (IBGE calendar; unknown future dates estimated as 31 Oct of the following year)',
                        'model registered (protocol or amendment) before issued_at'],
        'inputs': 'operational sources of the live job: NASA POWER meteorology, IMERG Late rain, CERES radiation from POWER where '
                  'available and ERA5 for the recent days without it, PAM lags published before the decision',
        'models': model_entries(),
        'no_rewrite': 'a key is recorded once; models added later never get predictions for keys already recorded',
        'scoring': {'when': 'once, after the first PAM release of the harvest',
                    'pairing': 'keys with predictions from every compared model; cutoffs averaged per harvest before averaging harvests',
                    'weighting': 'MG+MT scopes 50% per state; other scopes harvest-weighted',
                    'uncertainty': 'bootstrap of municipalities stratified by state, 2,000 draws; one season shares weather shocks, so the CI '
                                   'is conditional on the season'},
        'approval_for_operational_use': {
            'requires_all': ['relative MAE gain >= 5% against EVERY registered simple reference of the crop (history_ref and the '
                             'published references), CI95 upper bound of the MAE difference < 0 for each, Holm-adjusted across the '
                             'candidates of the same crop x scope',
                             'no priority state (MG, MT for soy/maize; MG for coffee; ES for conilon) more than 5% worse than the best simple reference',
                             'at least 30 paired municipality-harvests and >= 80% of the eligible keys with a prediction',
                             'operational inputs only, every input with available_at <= decision_at in the provenance'],
            'scope': 'the crop x scope scored, municipal yield product only; not field yield',
            'decision': 'approval allows a human decision to change the served model; nothing is promoted automatically',
            'renewal': 'scores continue every season; a later failure withdraws the approval'},
        'earliest_scoring': 'harvest 2027 keys after the PAM 2027 release (expected ~September 2028)',
    }
    save_json(PROTOCOL, protocol)
    return protocol


def add_model(entry, reason):
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    if any(m['model_id'] == entry['model_id'] for m in protocol['models']):
        raise ValueError(f'{entry["model_id"]} already registered')
    protocol.setdefault('amendments', []).append({'added_at': now(), 'model_id': entry['model_id'], 'reason': reason})
    protocol['models'].append({**entry, 'registered_at': protocol['amendments'][-1]['added_at']})
    save_json(PROTOCOL, protocol)
    log_version(f'add model {entry["model_id"]}', 'the model is scored only on keys first issued after its registered_at')


# ------------------------------------------------------------------ registration

def released(harvest_year, on):
    """PAM of `harvest_year` released on or before `on` (IBGE calendar; unknown future dates estimated)."""
    return release_date(harvest_year) <= on


def predict_all(protocol, crop, code, row, served, issued_at, cache):
    """Every registered model applicable to the key; `served` = (scope name, estimate) of the live answer."""
    from data.serving.yield_model import YieldModel
    out = {}
    for m in protocol['models']:
        if m['crop'] != crop or (m.get('registered_at') and m['registered_at'] > issued_at):
            continue
        if m['scope_states'] and code[:2] not in m['scope_states']:
            continue
        if 'column' in m:
            value = row.get(m['column'])
            out[m['model_id']] = {'estimate_kg_ha': None if value is None or value != value else round(float(value), 1)}
            continue
        try:
            if m['model_id'] not in cache:
                path = ROOT / m['artifact']
                if sha(path) != m['sha256']:
                    raise ValueError('artifact changed since registration')
                cache[m['model_id']] = YieldModel(path.with_suffix(''))
            model = cache[m['model_id']]
            if m['role'] == 'candidate' and code not in model.spec.get('coverage', [code]):
                continue
            features = {**row, 'municipality_code': code}
            out[m['model_id']] = {'estimate_kg_ha': round(max(model.predict(features), 0.0), 1)}
            if m['role'] == 'v7_catalog' and m['scope'] == served[0]:
                out[m['model_id']]['served'] = True
                out[m['model_id']]['matches_served_estimate'] = abs(out[m['model_id']]['estimate_kg_ha'] - served[1]) <= 0.1
        except Exception as error:  # recorded, never retried later for this key
            out[m['model_id']] = {'estimate_kg_ha': None, 'error': f'{type(error).__name__}: {error}'[:200]}
    return out


def recorded_keys():
    keys = set()
    for path in sorted((REGISTRY / 'records').glob('*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            keys.add(json.loads(line)['key'])
    return keys


def chain_head():
    path = REGISTRY / 'chain_head.json'
    return json.loads(path.read_text(encoding='utf-8'))['record_sha256'] if path.exists() else '0' * 64


def register(as_of, items, provider, source_mode):
    """`items`: list of (response, trace) of the day. Returns the daily manifest."""
    if source_mode != 'operational':
        raise ValueError('Only operational runs feed the prospective registry')
    today = datetime.now(BRT).date()
    if as_of != today:
        raise ValueError(f'decision_at must be the issue day in Brasilia time ({today}); got {as_of}')
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    protocol_sha = sha(PROTOCOL)
    seen = recorded_keys()
    previous = chain_head()
    records, skipped, cache = [], {'already_recorded': 0, 'pam_released': 0, 'not_ready': 0}, {}
    for response, trace in items:
        if response.get('status') != 'ready' or not trace.get('row'):
            skipped['not_ready'] += 1
            continue
        crop, code, year = response['crop'], response['municipality_code'], response['harvest_year']
        key = f'{crop}:{code}:{year}:{response["cutoff_days_used"]}'
        if key in seen:
            skipped['already_recorded'] += 1
            continue
        if released(year, as_of):
            skipped['pam_released'] += 1
            continue
        issued_at = now()
        first, last = trace['window']
        record = {'key': key, 'crop': crop, 'municipality_code': code, 'harvest_year': year,
                  'cutoff_days': response['cutoff_days_used'], 'milestone': response.get('milestone'),
                  'decision_at': as_of.isoformat(), 'issued_at': issued_at, 'observed_through': response['observed_through'],
                  'sowing_date': response['sowing_date'], 'sowing_basis': response['sowing_basis'],
                  'target': protocol['target'], 'protocol_sha256': protocol_sha,
                  'served_model': response['model'], 'coverage': response['coverage'],
                  'predictions': predict_all(protocol, crop, code, trace['row'], (response['model']['scope'], response['estimate_kg_ha']),
                                             issued_at, cache),
                  'inputs': provider.provenance(crop, code, first, last, trace['history_years']),
                  'warnings': response['warnings'], 'previous_record_sha256': previous}
        record['record_sha256'] = hashlib.sha256(canonical(record).encode()).hexdigest()
        previous = record['record_sha256']
        records.append(record)
        seen.add(key)
    day_file = REGISTRY / 'records' / f'{as_of.isoformat()}.jsonl'
    if records:
        day_file.parent.mkdir(parents=True, exist_ok=True)
        with day_file.open('a', encoding='utf-8') as handle:
            for record in records:
                handle.write(canonical(record) + '\n')
        save_json(REGISTRY / 'chain_head.json', {'record_sha256': previous, 'updated_at': now()})
    manifest = {'as_of': as_of.isoformat(), 'written_at': now(), 'records_added': len(records), 'skipped': skipped,
                'file': f'records/{day_file.name}' if day_file.exists() else None,
                'file_sha256': sha(day_file) if day_file.exists() else None, 'chain_head': previous, 'protocol_sha256': protocol_sha,
                'by_crop': {c: sum(1 for r in records if r['crop'] == c) for c in sorted({r['crop'] for r in records})}}
    save_json(REGISTRY / 'manifests' / f'{as_of.isoformat()}.json', manifest)
    return manifest


def seal(as_of):
    """Reproducible gzip copy of the day's records for the repository; its hash goes into the daily manifest."""
    import gzip
    source = REGISTRY / 'records' / f'{as_of.isoformat()}.jsonl'
    manifest_path = REGISTRY / 'manifests' / f'{as_of.isoformat()}.json'
    if not source.exists():
        return None
    target = REGISTRY / 'sealed' / f'{as_of.isoformat()}.jsonl.gz'
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=9) as handle:
        handle.write(source.read_bytes())
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.update(file_sha256=sha(source), sealed_file=f'sealed/{target.name}', sealed_sha256=sha(target), sealed_at=now())
    save_json(manifest_path, manifest)
    return manifest


def add_note(text, amendment_id=None, impact=None):
    """Versioned protocol amendment without a model (rules), timestamped before any scoring; never rewrites records."""
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    protocol.setdefault('amendments', []).append({'added_at': now(), 'amendment_id': amendment_id, 'note': text,
                                                  'eligibility_impact': impact})
    save_json(PROTOCOL, protocol)
    log_version(amendment_id or 'note', impact)


def verify():
    """Recompute every record hash and the chain; raise on any break."""
    previous = '0' * 64
    count = 0
    for path in sorted((REGISTRY / 'records').glob('*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            stored = record.pop('record_sha256')
            if record['previous_record_sha256'] != previous:
                raise ValueError(f'chain broken at {record["key"]}')
            if hashlib.sha256(canonical(record).encode()).hexdigest() != stored:
                raise ValueError(f'record altered: {record["key"]}')
            previous = stored
            count += 1
    if count and previous != chain_head():
        raise ValueError('chain head does not match the last record')
    return count


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('init', 'verify'))
    args = parser.parse_args()
    from data.serving import prospective_registry as module
    print(module.init()['registered_at'] if args.step == 'init' else f'{module.verify()} records verified')
