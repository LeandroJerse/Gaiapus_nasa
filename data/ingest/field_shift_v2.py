"""Field Shift v2: three historical management strategies in Sorriso (MT), one verifiable package.

Strategies (same soy, same fixed calendar, same soil hypothesis, continuous soil water between years):
- reference_fallow  A: soy -> second-crop maize -> bare fallow (reference of the demonstration);
- brachiaria        B: soy -> maize intercropped with Brachiaria -> cover until desiccation;
- soy_long_fallow   C: soy -> bare fallow until the next soy. Fewer crops, not a diversified rotation.

SIMULATED water and soil-presence indicators only, with retrospective NASA POWER weather. Nothing here is a yield,
carbon, nitrogen, infiltration or pest effect. `soil_covered` is the binary "a crop or cover is present" of the
engine (land != bare), not a measured ground-cover percentage.

Windows: the own off-season of each strategy has a different length (C starts 120 days earlier), so strategies are
compared on two windows that are identical for all of them: the agricultural year (soy sowing to the next soy
sowing) and the period after the soy harvest. Differences are paired year by year on the common valid years; their
range is the range across the simulated years, not a calibrated predictive interval.

Output: data/processed/field_shift_v2/<package_id>/ with summary.json, series.json, CSV tables and manifest.json.
The package id is derived from inputs, code and parameters, never from the clock. v1 files are never written.
"""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from data.ingest import field_shift_case as fsc
from data.ingest.field_shift_case import CENTRAL, SCENARIOS, SENSITIVITY, YEARS
from data.ingest.water_balance_run import ROOT, save_json, write_csv
from data.models.field_shift_forcing import RAIN_MULTIPLIERS, transform_rain
from data.models.water_balance import CROPS

SCHEMA_VERSION = '2.0.0'
OUT = ROOT / 'data/processed/field_shift_v2'
V1 = ROOT / 'data/processed/field_shift_v1'
REFERENCE = 'reference_fallow'
CODE_FILES = ('data/ingest/field_shift_case.py', 'data/ingest/field_shift_v2.py', 'data/models/field_shift_forcing.py', 'data/models/water_balance.py')
INITIAL_STORAGE_FRACTION = .7
FALLOW_KC = .30
STRATEGY = {
    'reference_fallow': {'label': 'A', 'crops': ['soy', 'maize2'], 'kind': 'demonstration_reference',
                         'sequence': 'soy -> second-crop maize -> bare fallow'},
    'brachiaria': {'label': 'B', 'crops': ['soy', 'maize2', 'brachiaria'], 'kind': 'cover_crop_alternative',
                   'sequence': 'soy -> maize intercropped with Brachiaria -> cover until desiccation'},
    'soy_long_fallow': {'label': 'C', 'crops': ['soy'], 'kind': 'fewer_crops_management_strategy',
                        'sequence': 'soy -> bare fallow until the next soy (no maize)'},
}
V1_KEYS = ('covered_days', 'off_season_water_use_mm', 'off_season_drainage_mm', 'storage_at_next_sowing_mm', 'soy_deficit_mm',
           'maize_deficit_mm', 'maize_stress_days', 'next_soy_establishment_deficit_mm')
V2_KEYS = ('bare_days', 'off_season_days', 'soy_stress_days', 'year_days', 'year_rain_mm', 'year_water_use_mm', 'year_drainage_mm',
           'post_soy_days', 'post_soy_water_use_mm', 'post_soy_drainage_mm')
KEYS = V1_KEYS + V2_KEYS
DEFINITIONS = {
    'covered_days': 'days of the agricultural year with a crop or cover present (binary engine flag, not ground-cover percentage)',
    'bare_days': 'days of the agricultural year without crop or cover',
    'off_season_days': 'length of the own off-season of the strategy (differs between strategies)',
    'off_season_water_use_mm': 'actual evapotranspiration in the own off-season of the strategy',
    'off_season_drainage_mm': 'water leaving the bucket below the root zone in the own off-season of the strategy',
    'storage_at_next_sowing_mm': 'available soil water on the day before the next soy sowing',
    'soy_deficit_mm': 'crop need minus water use (ETc - ETa) in the soy cycle',
    'soy_stress_days': 'soy days with water stress coefficient below 1',
    'maize_deficit_mm': 'ETc - ETa in the maize cycle; null where maize is not grown',
    'maize_stress_days': 'maize days with water stress coefficient below 1; null where maize is not grown',
    'next_soy_establishment_deficit_mm': 'ETc - ETa in the first 15 days of the next soy; null when the next season is not simulated',
    'year_days': 'days from soy sowing to the next soy sowing (same for every strategy)',
    'year_rain_mm': 'rain in the agricultural year (same for every strategy)',
    'year_water_use_mm': 'actual evapotranspiration in the agricultural year',
    'year_drainage_mm': 'drainage below the bucket in the agricultural year; it does not measure erosion, leaching or infiltration',
    'post_soy_days': 'days from the soy harvest to the next soy sowing (same for every strategy)',
    'post_soy_water_use_mm': 'actual evapotranspiration from the soy harvest to the next soy sowing',
    'post_soy_drainage_mm': 'drainage from the soy harvest to the next soy sowing',
}


def indicators(rows, dates, next_rows, trace):
    """v1 indicators, unchanged, plus explicit absences and the windows common to every strategy."""
    base = fsc.indicators(rows, dates, next_rows)
    soy = [r for r in rows if r['land_cover'] == 'soy']
    off = [r for r in rows if r['stage'].startswith('off_season')]
    post = [item for r, item in zip(rows, trace) if r['land_cover'] != 'soy']
    return {**base,
            'maize_status': 'grown' if base['maize_sowing'] else 'not_grown',
            'next_soy_establishment_status': 'ok' if next_rows else 'next_season_not_simulated',
            'off_season_days': len(off),
            'soy_stress_days': sum(r['ks'] < 1 - 1e-9 for r in soy),
            'year_days': len(rows),
            'year_rain_mm': round(sum(i['rain_mm'] for i in trace), 1),
            'year_water_use_mm': round(sum(i['eta_mm'] for i in trace), 1),
            'year_drainage_mm': round(sum(i['drainage_mm'] for i in trace), 1),
            'post_soy_days': len(post),
            'post_soy_water_use_mm': round(sum(i['eta_mm'] for i in post), 1),
            'post_soy_drainage_mm': round(sum(i['drainage_mm'] for i in post), 1)}


def run(weather, scenario, params):
    """Continuous simulation of the valid agricultural years; returns (indicators by year, daily rows by year)."""
    capacity = CROPS['soy']['cad_mm'][params['cad_class']]
    storage, simulated = INITIAL_STORAGE_FRACTION * capacity, {}
    for year in [*YEARS, YEARS[-1] + 1]:
        if fsc.calendar(year, params['soy_shift_days'])['next_soy_sowing'].isoformat() not in weather:
            break  # the next sowing is outside the weather series: this year is not simulated, never truncated
        trace = []
        rows, storage, dates = fsc.simulate_year(weather, year, scenario, params, storage, trace=trace)
        simulated[year] = (rows, dates, trace)
    years, series = [], {}
    for year in YEARS:
        if year not in simulated:
            break
        rows, dates, trace = simulated[year]
        next_rows = simulated.get(year + 1, (None,))[0]
        label = f'{year}/{year + 1}'
        years.append({'agricultural_year': label, **indicators(rows, dates, next_rows, trace)})
        series[label] = rows
    return years, series


def summary(years):
    """Mean of each indicator over the years where it exists; None when it exists in no year."""
    return {k: fsc.mean(years, k) for k in KEYS}


def paired(alternative, reference, keys):
    """Year-by-year differences alternative - reference on the years where both have the indicator."""
    ref = {y['agricultural_year']: y for y in reference}
    out = {}
    for key in keys:
        pairs = [(y['agricultural_year'], y[key] - ref[y['agricultural_year']][key]) for y in alternative
                 if y['agricultural_year'] in ref and y.get(key) is not None and ref[y['agricultural_year']].get(key) is not None]
        values = [v for _, v in pairs]
        out[key] = {'n': len(values), 'mean': round(sum(values) / len(values), 2) if values else None,
                    'min': round(min(values), 2) if values else None, 'max': round(max(values), 2) if values else None,
                    'years': [y for y, _ in pairs], 'status': 'ok' if values else 'not_applicable'}
    return out


def _all(weather, params):
    return {s: run(weather, s, params) for s in SCENARIOS}


def _comparisons(runs):
    return {f'{s}_minus_{REFERENCE}': paired(runs[s][0], runs[REFERENCE][0], KEYS) for s in SCENARIOS if s != REFERENCE}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def weather_digest(weather):
    days = sorted(weather)
    body = json.dumps([[d, weather[d]['rain_mm'], weather[d]['et0_mm']] for d in days], separators=(',', ':'), allow_nan=False)
    return {'sha256': _sha(body.encode('utf-8')), 'days': len(days), 'first_day': days[0], 'last_day': days[-1]}


def code_digest():
    return {name: _sha((ROOT / name).read_bytes().replace(b'\r\n', b'\n')) for name in CODE_FILES}


def build(weather, weather_source):
    """(package_id, summary, series) for a daily forcing {day: {'rain_mm', 'et0_mm'}}."""
    identity = {'schema_version': SCHEMA_VERSION, 'weather': weather_digest(weather), 'code': code_digest(),
                'parameters': {'central': CENTRAL, 'ranges_tested': SENSITIVITY, 'rain_multipliers': RAIN_MULTIPLIERS,
                               'fallow_kc': FALLOW_KC, 'initial_storage_fraction': INITIAL_STORAGE_FRACTION}}
    package_id = 'field-shift-v2-' + _sha(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8'))[:12]
    base = _all(weather, CENTRAL)
    labels = [y['agricultural_year'] for y in base[REFERENCE][0]]
    rain_points = []
    for multiplier in RAIN_MULTIPLIERS:
        runs = base if multiplier == 1.0 else _all(transform_rain(weather, multiplier), CENTRAL)
        rain_points.append({'multiplier': multiplier, 'rain_change_percent': round((multiplier - 1) * 100),
                            'scenarios': {s: {'mean': summary(runs[s][0]), 'by_year': runs[s][0], 'valid_years': len(runs[s][0])} for s in SCENARIOS},
                            'comparisons': _comparisons(runs)})
    parameters = []
    for key, values in SENSITIVITY.items():
        for value in values:
            runs = _all(weather, {**CENTRAL, key: value})
            parameters.append({'parameter': key, 'value': value,
                               'scenarios': {s: {'mean': summary(runs[s][0]), 'valid_years': len(runs[s][0])} for s in SCENARIOS},
                               'comparisons': _comparisons(runs)})
    result = {
        'schema_version': SCHEMA_VERSION, 'product': 'field_shift_strategies', 'package_id': package_id,
        'municipality_code': fsc.CODE, 'municipality': 'Sorriso - MT', 'simulated': True, 'scale': 'municipal point forcing, not a field',
        'reference_scenario': REFERENCE,
        'reference_note': 'A is the reference of the demonstration; it is the current rotation of a grower only if the grower says so.',
        'sowing': {'soy': {'date_pattern': '10-05', 'origin': 'hypothetical_fixed_calendar_per_state (MT, 5 October)'},
                   'maize': {'rule': f'day after the soy cycle ({fsc.SOY_DAYS} days)', 'origin': 'derived_from_assumed_soy_cycle'},
                   'brachiaria': {'rule': 'sown with the maize; desiccated 15 days before the next soy', 'origin': 'assumed_management'},
                   'long_fallow': {'rule': 'bare fallow from the soy harvest to the next soy sowing', 'origin': 'assumed_management'}},
        'weather': {'source': weather_source, 'agricultural_years': labels, **identity['weather']},
        'soil': {'cad_class': CENTRAL['cad_class'], 'capacity_mm': CROPS['soy']['cad_mm'][CENTRAL['cad_class']],
                 'initial_storage_fraction': INITIAL_STORAGE_FRACTION, 'basis': 'fixed hypothesis, not a measured profile'},
        'hypotheses': {'central': CENTRAL, 'ranges_tested': SENSITIVITY, 'fallow_kc': FALLOW_KC,
                       'sources': {'cover_kc': 'FAO-56 grazing pasture Kc 0.75-0.85', 'straw_factor': 'assumed reduction of soil evaporation by straw',
                                   'fallow_kc': 'bare soil 0.30'}},
        'windows': {'agricultural_year': 'soy sowing to the day before the next soy sowing; identical for every strategy',
                    'post_soy': 'day after the soy cycle to the day before the next soy sowing; identical for every strategy',
                    'off_season': 'own off-season of each strategy; lengths differ, so it is reported but is not the comparison window'},
        'indicator_definitions': DEFINITIONS,
        'scenarios': {s: {**STRATEGY[s], 'mean': summary(base[s][0]), 'by_year': base[s][0], 'valid_years': len(base[s][0])} for s in SCENARIOS},
        'comparisons': _comparisons(base),
        'comparisons_note': 'Paired differences by agricultural year on the common valid years. min and max are the range across the simulated years, not a calibrated predictive interval.',
        'sensitivity': {
            'rain': {'label': 'sensitivity test on historical weather, not a climate forecast',
                     'method': 'every daily rain multiplied by the factor on the same day sequence; ET0 and all other inputs unchanged; soil water carried continuously',
                     'points': rain_points},
            'parameters': parameters,
            'temperature': {'status': 'not_calculated',
                            'reason': 'a temperature shift needs FAO-56 ET0 recomputed with a declared humidity policy; not part of this package'}},
        'not_simulated': ['yield', 'soil carbon', 'infiltration', 'weeds', 'nitrogen', 'pests and nematodes', 'costs and margin'],
        'yield_effect': None,
        'evidence': 'Simulation under stated hypotheses; no field measurement of these effects in Sorriso.'}
    columns = list(base[REFERENCE][1][labels[0]][0]) if labels else []
    series = {'schema_version': SCHEMA_VERSION, 'product': 'field_shift_strategies_daily', 'package_id': package_id,
              'municipality_code': fsc.CODE, 'capacity_mm': result['soil']['capacity_mm'], 'rain_multiplier': 1.0,
              'scenarios': {s: {'agricultural_year': [y for y, rows in base[s][1].items() for _ in rows],
                                **{c: [r[c] for rows in base[s][1].values() for r in rows] for c in columns}} for s in SCENARIOS}}
    return package_id, result, series, base, identity


def reproduce_v1(base):
    """A and B of this run against the published v1 tables, value for value. None where v1 is not in the checkout."""
    import csv
    if not V1.exists():
        return None
    out = {}
    for scenario in ('reference_fallow', 'brachiaria'):
        years, series = base[scenario]
        v1_keys = None
        with (V1 / f'{scenario}_by_year.csv').open(encoding='utf-8', newline='') as stream:
            published = list(csv.DictReader(stream))
        v1_keys = list(published[0])
        mine = [{k: '' if y[k] is None else str(y[k]) for k in v1_keys} for y in years]
        with (V1 / f'{scenario}_daily.csv').open(encoding='utf-8', newline='') as stream:
            daily = list(csv.DictReader(stream))
        flat = [{k: str(v) for k, v in {'agricultural_year': y, **r}.items()} for y, rows in series.items() for r in rows]
        out[scenario] = {'by_year_identical': mine == published, 'daily_identical': flat == daily, 'years': len(published), 'days': len(daily),
                         'v1_by_year_sha256': _sha((V1 / f'{scenario}_by_year.csv').read_bytes()),
                         'v1_daily_sha256': _sha((V1 / f'{scenario}_daily.csv').read_bytes())}
    return out


def write_package(weather, out, weather_source):
    """Write one immutable package directory and return its manifest. Refuses an existing directory."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f'Package directory already exists and is immutable: {out}')
    package_id, result, series, base, identity = build(weather, weather_source)
    out.mkdir(parents=True)
    save_json(out / 'summary.json', result)
    (out / 'series.json').write_text(json.dumps(series, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf-8')
    for s in SCENARIOS:
        write_csv(out / f'{s}_by_year.csv', base[s][0])
        write_csv(out / f'{s}_daily.csv', [{'agricultural_year': y, **r} for y, rows in base[s][1].items() for r in rows])
    write_csv(out / 'paired_differences.csv', [{'comparison': name, 'indicator': key, **{k: (';'.join(v) if k == 'years' else v) for k, v in stats.items()}}
                                               for name, block in result['comparisons'].items() for key, stats in block.items()])
    write_csv(out / 'rain_sensitivity.csv', [{'rain_multiplier': p['multiplier'], 'scenario': s, 'valid_years': p['scenarios'][s]['valid_years'], **p['scenarios'][s]['mean']}
                                             for p in result['sensitivity']['rain']['points'] for s in SCENARIOS])
    try:
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    manifest = {'package_id': package_id, 'schema_version': SCHEMA_VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'identity': identity, 'weather_source': weather_source, 'base_git_revision': revision,
                'reproduction_v1': reproduce_v1(base),
                'files': {p.name: {'sha256': _sha(p.read_bytes()), 'bytes': p.stat().st_size} for p in sorted(out.iterdir())}}
    save_json(out / 'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    from data.ingest import cohort_v5 as v5
    from data.ingest.cohort_v6 import RAW
    weather, _ = v5.daily_weather(fsc.CODE, root=RAW)
    source = 'NASA POWER daily point (IMERG rain), retrospective'
    package_id = build(weather, source)[0]
    manifest = write_package(weather, OUT / package_id, source)
    print(package_id)
    print('reproduction_v1', manifest['reproduction_v1'])
    result = json.loads((OUT / package_id / 'summary.json').read_text(encoding='utf-8'))
    for s in SCENARIOS:
        print(s, result['scenarios'][s]['mean'])


if __name__ == '__main__':
    from data.ingest.field_shift_v2 import main as entry
    entry()
