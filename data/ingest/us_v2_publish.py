"""Publish US v2 product runs in the APP layout (future-simulation-publications.v1 / -capabilities.v1 / -run.v1).

One run per county x crop x harvest year that has per-member daily water balance. Cases without it
(post-harvest 2026, partial in-season KS 2027 whose observed prefix lacks radiation) are listed in the catalog with a reason.
Runs are immutable (write-once, identical bytes on republish).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from data.ingest import us_v2_product as P
from data.ingest import us_v2_weather as wx
from data.models import water_balance as WB

PUBLISH = P.T.OUT / 'publish'
RUN_CONTRACT, INDEX_CONTRACT, CATALOG_CONTRACT = 'future-simulation-run.v1', 'future-simulation-publications.v1', 'future-simulation-capabilities.v1'
PROTOCOL_ID = 'us_training_v2'
ENGINE_VERSION = 'water_balance.simulate (FAO-56 bucket) + us_v2 yield models'
LABEL_RELEASE_RULE = {'maize': 'NASS county estimates: corn/soy crop year Y released in May of Y+1 (2024-2025 confirmed/planned; earlier estimated)',
                      'soy': 'NASS county estimates: corn/soy crop year Y released in May of Y+1 (2024-2025 confirmed/planned; earlier estimated)',
                      'wheat_winter': 'NASS county estimates: winter wheat crop year Y released in December of Y (2024 not published; 2025 released 2026-03-05)'}
SENSITIVITY = (0.25, 0.75)


def _dump(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=float).encode('utf-8')


def _sha(obj) -> str:
    return hashlib.sha256(_dump(obj)).hexdigest()


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise RuntimeError(f'immutable artifact differs: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def _q(values) -> dict:
    v = np.asarray([x for x in values if x is not None and not (isinstance(x, float) and math.isnan(x))], float)
    return {'p10': float(np.quantile(v, .1)), 'p50': float(np.quantile(v, .5)), 'p90': float(np.quantile(v, .9)), 'n': int(len(v))}


def _place_name(row) -> str:
    name = str(row['county_name'])
    return name if name.endswith('County') else f'{name} County'


def yield_object(row: dict) -> dict:
    """County yield result in the APP model variant. Observed-weather results (post-harvest) carry no scenario range."""
    crop, state = row['crop'], row['state']
    is_ref = str(row['model']).startswith('base:')
    scenario = int(row['members']) > 1
    over = 'over climatological members' if scenario else 'with observed season weather to the feature cutoff'
    return {'status': 'reference' if is_ref else 'research', 'method': f"us_v2 {row['model_kind']} {row['model']} {over}",
            'model_version': f"research_us_v2/product/{row['scope']}_{row['model_kind']}", 'value_kg_ha': float(row['yield_p50']),
            'scenario_range': {'p10': float(row['yield_p10']), 'p50': float(row['yield_p50']), 'p90': float(row['yield_p90']),
                               'method': 'ensemble_spread_uncalibrated'} if scenario else None,
            'predictive_range': None if pd.isna(row.get('predictive_p10')) else {'p10': float(row['predictive_p10']), 'p50': float(row['yield_p50']),
                                                                                  'p90': float(row['predictive_p90']),
                                                                                  'interval_method': 'p50_plus_development_residual_quantiles'},
            'position_vs_season': row['position_vs_season'], 'last_label_year': None if pd.isna(row['last_label_year']) else int(row['last_label_year']),
            'history_horizon_years': None if pd.isna(row['hist_age']) else int(row['hist_age']), 'label_release_rule': LABEL_RELEASE_RULE[crop],
            'research_status': row['scientific_status'], 'exposure_flags': ['IA_2025_partial_snippet_exposure'] if state == 'IA' else [],
            'conditional_on_scenario': scenario, 'scale': 'county'}


def build_run(row: dict, series_payload: dict, manifest: dict, issued_at: str, as_of: str) -> tuple[dict, dict]:
    fips, crop, year, state = row['fips'], row['crop'], int(row['year']), row['state']
    meta = series_payload.pop('_manifest')
    analog = series_payload.pop('_analog_years')
    yields = series_payload.pop('_yield')
    ids = sorted(series_payload)
    first = series_payload[ids[0]]
    dates = first['day_utc']
    cfg = WB.CROPS[P.WB_CROP[crop]]
    cap = cfg['cad_mm'][P.WB_CAPACITY_CLASS]
    start, cutoff = wx.window(state, crop, year)
    cols = {'precipitation_mm': 'rain_mm', 'et0_mm': 'et0_mm', 'etc_mm': 'etc_mm', 'eta_mm': 'eta_mm', 'et_deficit_mm': 'et_deficit_mm',
            'storage_mm': 'storage_end_mm', 'drainage_mm': 'drainage_mm', 'stress_coefficient': 'ks', 'mass_balance_residual_mm': 'mass_residual_mm'}
    members = [{'member_id': m, 'analog_year': analog.get(m), 'crop_id': [crop] * len(dates), 'phase_code': series_payload[m]['phase'],
                **{k: series_payload[m][v] for k, v in cols.items()}} for m in ids]
    bands = {v: {q: [float(np.quantile([mm[v][i] for mm in members], p)) for i in range(len(dates))] for q, p in (('p10', .1), ('p50', .5), ('p90', .9))}
             for v in ('storage_mm', 'precipitation_mm', 'et_deficit_mm')}
    day_kind = ['climatological_scenario'] * len(dates)
    series = {'dates': dates, 'capacity_mm': cap, 'members': members, 'bands': bands, 'day_kind': day_kind}
    per = {}
    for m in ids:
        d = series_payload[m]
        rep = [i for i, ph in enumerate(d['phase']) if ph == 'reproductive']
        per[m] = {'crop_deficit_mm': float(sum(d['et_deficit_mm'])), 'crop_eta_mm': float(sum(d['eta_mm'])), 'rain_mm': float(sum(d['rain_mm'])),
                  'reproductive_stress_days': float(sum(1 for i in rep if d['ks'][i] < 1)), 'yield_kg_ha': float(yields[m])}
    metrics = list(next(iter(per.values())).keys())
    whole = {m: (per[m]['rain_mm'], per[m]['crop_deficit_mm'], float(np.mean(series_payload[m]['storage_end_mm']))) for m in ids}
    med = [float(np.median([whole[m][k] for m in ids])) for k in range(3)]
    sd = [float(np.std([whole[m][k] for m in ids])) or 1.0 for k in range(3)]
    central = min(ids, key=lambda m: (sum(((whole[m][k] - med[k]) / sd[k]) ** 2 for k in range(3)), m))
    dry = min(ids, key=lambda m: (-whole[m][1], m))
    wet = min(ids, key=lambda m: (whole[m][1], m))
    sens = {}
    c = series_payload[central]
    base_def = per[central]['crop_deficit_mm']
    for f in SENSITIVITY:
        sim = WB.simulate([{'day_utc': d, 'rain_mm': r, 'et0_mm': e} for d, r, e in zip(c['day_utc'], c['rain_mm'], c['et0_mm'])], P.WB_CROP[crop], cap, f)
        sens[str(f)] = {f'{crop}:{year}': float(sum(x['et_deficit_mm'] for x in sim) - base_def)}
    is_ref = str(row['model']).startswith('base:')
    yld = yield_object(row)
    cycle_days = sum(cfg['phase_days'])
    season = {'crop_id': crop, 'harvest_year': year, 'season_label': f'{start.year}/{year}' if start.year != year else str(year),
              'sowing_date': start.isoformat(), 'expected_harvest_date': dates[-1], 'cycle_days': cycle_days,
              'calendar_source': f'{crop}: sowing {start.isoformat()} fixed regional hypothesis (us_training_v1/v2 calendars); yield features to {cutoff.isoformat()}; FAO-56 *_us stage lengths',
              'quantiles': {k: _q([per[m][k] for m in ids]) for k in metrics},
              'events': [{'event_id': 'reproductive_stress_ge_20d', 'share_of_members': sum(1 for m in ids if per[m]['reproductive_stress_days'] >= P.STRESS_EVENT_DAYS) / len(ids), 'members': len(ids)}],
              'interval_method': 'climate_ensemble_spread_only', 'members': per, 'yield': yld}
    identity = {'protocol': PROTOCOL_ID, 'contract': RUN_CONTRACT, 'location_id': f'US:fips:{fips}', 'plan': f'us_{crop}', 'harvest_years': [year],
                'as_of': as_of, 'ensemble_sha256': meta.get('ensemble_sha256'), 'engine': ENGINE_VERSION, 'parameter_set': P.WB_CROP[crop],
                'soil': {'capacity_mm': cap, 'profile_id': f'{P.WB_CAPACITY_CLASS}-hypothesis'}, 'seed': meta.get('seed'),
                'product_manifest_sha256': manifest['_sha256'], 'model': row['model'], 'storage_fraction': P.WB_INITIAL_FRACTION}
    run_id = 'sim_' + _sha(identity)[:24]
    cm = meta.get('calendar_mapping') or {'method': 'day_offset_from_aligned_start', 'max_month_day_drift_days': 0, 'note': ''}
    run = {'run_id': run_id, 'contract': RUN_CONTRACT, 'protocol_id': PROTOCOL_ID, 'execution_status': 'succeeded',
           'output_mode': 'simulation_conditional_on_historical_weather',
           'scientific_status': {'weather': 'climatological_scenario', 'water_balance': 'physical_experimental', 'phenology': 'physical_experimental',
                                 'yield': 'reference' if is_ref else 'research'},
           'location_id': f'US:fips:{fips}', 'place_name': _place_name(row), 'state_code': state, 'plan_id': f'us_{crop}',
           'harvest_years': [year], 'as_of': as_of, 'issued_at': issued_at, 'simulation_start': dates[0], 'simulation_end': dates[-1],
           'weather_segments': [{'kind': 'climatological_scenario', 'start': dates[0], 'end': dates[-1], 'origin': 'historical_analog_blocks_v1 over NASA POWER'}],
           'ensemble': {'method': 'historical_analog_blocks_v1', 'members': len(ids), 'effective_source_years': len({analog[m] for m in ids}),
                        'analog_years': meta.get('analog_years'), 'eligible_years': meta.get('analog_years'), 'excluded_years': meta.get('excluded_years') or {},
                        'coverage_class': meta.get('coverage_class') or 'standard', 'seed': meta.get('seed'), 'calendar_mapping': cm, 'analog_years_requested': meta.get('analog_years')},
           'sources': {'history': {'provider': 'NASA POWER', 'product': 'POWER daily point AG', 'availability': 'retrospective snapshot'}, 'observed': None,
                       'et0': 'FAO-56 Penman-Monteith daily (water_balance.et0_daily) with POWER T2MDEW/PS', 'yield_labels': 'USDA NASS county estimates (qs.crops_20261114)'},
           'engine': {'version': ENGINE_VERSION, 'parameter_set_id': P.WB_CROP[crop], 'daily_order': ['rain', 'infiltration', 'drainage', 'eta', 'storage']},
           'soil': {'capacity_mm': cap, 'profile_id': f'{P.WB_CAPACITY_CLASS}-hypothesis', 'is_hypothesis': True},
           'calendar_rules': [],
           'seasons': [season],
           'representative': {'central': central, 'dry': dry, 'wet': wet, 'rule': 'nearest whole member to the multivariate median (rain, deficit, mean storage); dry/wet = max/min crop deficit'},
           'initial_condition': {'storage_fraction': P.WB_INITIAL_FRACTION, 'is_hypothesis': True, 'warm_up': None, 'sensitivity': sens,
                                 'sensitivity_metric': 'season crop deficit difference (mm) on the central member'},
           'limitations': P.LIMITATIONS, 'identity': identity}
    run['checksums'] = {'series_sha256': _sha(series), 'identity_sha256': _sha(identity)}
    return run, series


def publish(tag: str, out: Path = PUBLISH, only: set[str] | None = None) -> dict:
    summ = pd.read_csv(P.PRODUCT / f'us_scenarios_summary_{tag}.csv.gz', dtype={'fips': str})
    if 'error' in summ:
        summ = summ[summ.error.isna()]
    if only:
        summ = summ[summ.fips.isin(only)]
    out = Path(out)
    man_path = P.PRODUCT / f'us_scenarios_manifest_{tag}.json'
    manifest = json.loads(man_path.read_text())
    manifest['_sha256'] = P.sha(man_path)
    pubs, places = [], {}
    as_of = f"{manifest['as_of']}T23:59:00+00:00"
    yield_only = 0
    for row in summ.to_dict('records'):
        fips, crop, year = row['fips'], row['crop'], int(row['year'])
        place = places.setdefault(fips, {'location_id': f'US:fips:{fips}', 'name': _place_name(row), 'state_code': row['state'],
                                         'history_years': 25, 'observed_until': manifest['as_of'], 'capabilities': []})
        sp = P.PRODUCT / 'series' / f'{fips}_{crop}_{year}_{tag}.json.gz'
        if not sp.exists():
            # A yield result exists but no daily water-balance series: never a fabricated run id.
            reason = 'post_harvest_no_daily_series' if row['position_vs_season'] in ('post_harvest_pre_release', 'post_cutoff') else 'source_unavailable'
            yld = yield_object(row)
            place['capabilities'].append({'crop_id': crop, 'harvest_year': year, 'status': 'missing_data', 'reason_code': reason, 'run_ids': [],
                                          'yield': yld['status'], 'yield_result': yld})
            yield_only += 1
            continue
        payload = json.loads(gzip.decompress(sp.read_bytes()))
        run, series = build_run(row, payload, manifest, manifest['issued_at'], as_of)
        raw = _dump(series)
        rb, gz = _dump(run), gzip.compress(raw, compresslevel=9, mtime=0)
        base = f"runs/{run['run_id']}"
        _write_once(out / base / 'run.json', rb)
        _write_once(out / base / 'series.json.gz', gz)
        pubs.append({'run_id': run['run_id'], 'location_id': run['location_id'], 'place_name': run['place_name'], 'plan_id': run['plan_id'], 'crops': [crop],
                     'harvest_years': [year], 'as_of': run['as_of'], 'issued_at': run['issued_at'], 'visibility': 'public_regional',
                     'publication_version': run['run_id'], 'current': True,
                     'artifacts': [{'kind': 'run', 'path': f'{base}/run.json', 'sha256': hashlib.sha256(rb).hexdigest(), 'bytes': len(rb), 'compression': None},
                                   {'kind': 'series', 'path': f'{base}/series.json.gz', 'sha256': hashlib.sha256(gz).hexdigest(), 'bytes': len(gz), 'compression': 'gzip'}],
                     'measured': {'seconds': None, 'peak_python_mib': None, 'series_bytes_raw': len(raw), 'members': run['ensemble']['members'], 'days': len(series['dates']), 'series_bytes_gzip': len(gz),
                                  'note': 'product runtime and peak RSS in the product manifest'}})
        place['capabilities'].append({'crop_id': crop, 'harvest_year': year, 'status': 'ready', 'reason_code': None, 'run_ids': [run['run_id']],
                                      'water_balance': 'physical_experimental', 'yield': run['seasons'][0]['yield']['status']})
    pubs.sort(key=lambda p: (p['location_id'], p['plan_id'], p['harvest_years']))
    index = {'contract': INDEX_CONTRACT, 'as_of': as_of, 'publications': pubs}
    years = sorted({c['harvest_year'] for pl in places.values() for c in pl['capabilities']})
    catalog = {'contract': CATALOG_CONTRACT, 'as_of': as_of, 'harvest_years': years, 'places': sorted(places.values(), key=lambda p: p['location_id']),
               'inputs': {'product_manifest': man_path.relative_to(P.T.ROOT).as_posix(), 'product_manifest_sha256': manifest['_sha256'], 'seed': manifest['seed'], 'publication_revision': 2,
                          'revision_note': 'revision 2 adds yield-only capabilities (2026 post-harvest, KS 2027 in-season) with yield_result; run and series bytes are unchanged from revision 1', 'yield_only_capabilities': yield_only, 'subset': sorted(only) if only else None}}
    for name, obj in (('index.json', index), ('catalog.json', catalog)):
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, default=float).encode('utf-8'))
    return {'runs': len(pubs), 'places': len(places), 'yield_only': yield_only}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--out', default=str(PUBLISH))
    ap.add_argument('--only', nargs='*', help='county FIPS subset (fixtures)')
    a = ap.parse_args()
    print(json.dumps(publish(a.tag, Path(a.out), set(a.only) if a.only else None)))
