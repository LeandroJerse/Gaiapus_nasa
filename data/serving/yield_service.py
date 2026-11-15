"""Yield estimate for one crop and municipality at a date, following contracts/yield-estimate.schema.json (1.1.0).

Inputs come through two callables so the same code runs offline (cached files) and live:
  get_weather(code, first, last) -> (weather_by_day, elevation, location)
  get_history(crop, code, years) -> {harvest_year: published yield}
Request errors (invalid crop, invalid code, municipality not covered) follow yield-error.schema.json.
"""
from datetime import date, datetime, timedelta, timezone

from data.ingest.cohort_v7 import COFFEE
from data.ingest.crops import REGISTRY, cycle_days, sowing_date as calendar_sowing
from data.serving.live_features import feature_row
from data.serving.yield_model import YieldModel
from data.serving.yield_request import check_yield_request

SCHEMA_VERSION = '1.2.0'
_MODELS = {}


def model_for(catalog, crop, municipality_code):
    """The scope that applies to a municipality: a regional rule first, otherwise national."""
    scopes = catalog['crops'][crop]['models']
    regional = [s for s in scopes if s['states'] and municipality_code[:2] in s['states']]
    return regional[0] if regional else next(s for s in scopes if s['scope'] == 'national')


def load_model(models_dir, artifact):
    key = (str(models_dir), artifact)
    if key not in _MODELS:
        _MODELS[key] = YieldModel(models_dir / artifact)
    return _MODELS[key]


def season(crop, code, as_of, data_end, harvest_year=None, sowing=None):
    """(harvest_year, start, days_with_data, sowing_basis) of the season active at `as_of`, or None."""
    if crop in COFFEE:
        years = [harvest_year] if harvest_year else [as_of.year + 1, as_of.year]
        for year in years:
            start = date(year - 1, 6, 1)
            if start <= as_of and (harvest_year or as_of < date(year, 9, 1)):
                return year, start, (data_end - start).days + 1, 'coffee_production_year_start'
        return None
    years = [harvest_year] if harvest_year else [as_of.year + 1, as_of.year]
    for year in years:
        start = sowing or calendar_sowing(crop, code, year)
        if start <= as_of and (harvest_year or (as_of - start).days < cycle_days(crop) + 60):
            return year, start, (data_end - start).days + 1, 'reported_by_user' if sowing else 'hypothetical_fixed_calendar_per_state'
    return None


TEST_STATUS = {'confirmed': 'confirmed_on_2022_2024_test', 'not_confirmed': 'not_confirmed_on_2022_2024_test',
               'worse_than_history': 'worse_than_history_on_2022_2024_test'}


def scientific_fields(scope, item):
    """Display fields: the served model's single 2022-2024 test result and the history reference (no model change)."""
    test = scope.get('test') or item.get('test_2022_2024')
    return {'scientific_status': TEST_STATUS[test['outcome']] if test else 'not_tested',
            'historical_reference': {'name': 'history', 'description': 'Ridge on trend and published IBGE PAM yields (lags), refitted per cutoff',
                                     'test_2022_2024_mae_kg_ha': test['history_mae_kg_ha'] if test else None,
                                     'served_model_test_2022_2024_mae_kg_ha': test['mae_kg_ha'] if test else None}}


def base_response(crop, code, as_of, mode):
    return {'schema_version': SCHEMA_VERSION, 'product': 'yield_estimate', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'as_of': as_of.isoformat(), 'municipality_code': code, 'crop': crop, 'operational_release': 'not_released',
            'availability': 'operational_sources_validated_retrospectively' if mode == 'operational' else 'retrospective_inputs_vintages_unverified',
            'warnings': ['Retrospective validation 2019-2021; not released for operational use',
                         'Municipal yield, not field yield']}


def estimate_yield(crop, code, as_of, *, catalog, models_dir, get_weather, get_history, mode='retrospective', data_end=None,
                   harvest_year=None, sowing_date=None, trace=None):
    eligible = {name: {m['code'] for m in item['municipalities']} for name, item in catalog['crops'].items()}
    error = check_yield_request(crop, code, eligible)
    if error:
        return error
    item = catalog['crops'][crop]
    scope = model_for(catalog, crop, code)
    model = load_model(models_dir, scope['artifact'])
    data_end = data_end or (as_of - timedelta(days=catalog['operational_data_lag_days'] if mode == 'operational' else 0))
    response = base_response(crop, code, as_of, mode)
    found = season(crop, code, as_of, data_end, harvest_year, sowing_date)
    sha = next(v for k, v in scope['files'].items() if k.endswith('.json'))
    response['model'] = {'version': 'research_v7', 'type': scope['type'], 'name': scope['model'], 'scope': scope['scope'],
                         'sources': scope['sources'], 'artifact_sha256': sha, 'trained_through_harvest': 2019,
                         **scientific_fields(scope, item)}
    response['supported_cutoffs_days'] = item['cutoffs_days']
    empty = {'status': None, 'harvest_year': as_of.year, 'sowing_date': as_of.isoformat(), 'sowing_basis': 'hypothetical_fixed_calendar_per_state',
             'days_after_sowing_requested': 0, 'cutoff_days_used': None, 'estimate_kg_ha': None, 'observed_through': None, 'validation': None,
             'milestone': None, 'coverage': {'forcing_days_expected': 0, 'forcing_days_present': 0, 'sources': [], 'source_mode': mode}}
    if found is None:
        return {**response, **empty, 'status': 'outside_season',
                'warnings': [*response['warnings'], 'No season of this crop is active at this date']}
    year, start, days, basis = found
    cutoff = max((c for c in item['cutoffs_days'] if c <= days), default=None)
    common = {'harvest_year': year, 'sowing_date': start.isoformat(), 'sowing_basis': basis, 'days_after_sowing_requested': max(days, 0)}
    if cutoff is None:
        return {**response, **empty, **common, 'status': 'before_first_cutoff',
                'warnings': [*response['warnings'], f'First validated cutoff is day {min(item["cutoffs_days"])}; data reach day {max(days, 0)}']}
    sources = ['NASA POWER daily (temperature, humidity, wind, dew point, pressure)',
               'IMERG Late V07 rain + CERES/ERA5 radiation' if mode == 'operational' else 'IMERG Final rain + CERES radiation (via POWER)',
               'IBGE PAM published yields']
    first, last, history_years = start - timedelta(days=400 if crop in COFFEE else 0), min(start + timedelta(days=cutoff), data_end), list(range(year - 6, year - 1))
    try:
        weather, elevation, location = get_weather(code, first, last)
        history = get_history(crop, code, history_years)
    except Exception as error:  # an external source failed today; the next run retries from the cache
        coverage = {'forcing_days_expected': cutoff, 'forcing_days_present': 0, 'sources': sources, 'source_mode': mode}
        return {**response, **empty, **common, 'coverage': coverage, 'status': 'inputs_incomplete',
                'warnings': [*response['warnings'], f'Source unavailable today: {type(error).__name__}']}
    row = feature_row(crop, code, year, cutoff, weather, history, location, elevation,
                      sowing=start if basis == 'reported_by_user' else None)
    coverage = {'forcing_days_expected': cutoff, 'forcing_days_present': cutoff if row else 0, 'sources': sources, 'source_mode': mode}
    if row is None:
        return {**response, **empty, **common, 'cutoff_days_used': None, 'coverage': coverage, 'status': 'inputs_incomplete',
                'warnings': [*response['warnings'], 'Weather inputs missing inside the window; no estimate']}
    row['horizon_days'] = cutoff
    missing = [f for f in model.required_features(cutoff) if f not in row]
    if missing:
        return {**response, **empty, **common, 'coverage': coverage, 'status': 'inputs_incomplete',
                'warnings': [*response['warnings'], f'Missing model inputs: {missing}']}
    metrics = scope['validation_by_cutoff'][str(cutoff)]
    inside = metrics['in_training']
    validation = {'years': item['validation_years'], 'cutoff_days': cutoff, 'mae_kg_ha': inside['mae_kg_ha'], 'bias_kg_ha': inside['bias_kg_ha'],
                  'label_families': inside['label_families'], 'municipalities': inside['municipalities'],
                  'scope': f'walk-forward 2019-2021, {scope["scope"]}, municipalities used in training',
                  'validation_status': item['validation_status']}
    if 'out_of_training' in metrics:
        validation['out_of_training_municipalities'] = {k: metrics['out_of_training'][k] for k in ('mae_kg_ha', 'label_families')}
    warnings = list(response['warnings'])
    ranges = scope.get('training_ranges', {}).get(str(cutoff), {})
    # harvest_year is extrapolated on purpose (linear trend), so it is not flagged.
    outside = sorted(f for f, (low, high) in ranges.items() if f != 'harvest_year'
                     and row.get(f) is not None and row.get(f) == row.get(f) and not low <= float(row[f]) <= high)
    if outside:
        warnings.append(f'Inputs outside the 0.5-99.5% training range: {", ".join(outside)}; treat the estimate with caution')
    test = scope.get('test') or item.get('test_2022_2024')
    if test:
        validation['test_2022_2024'] = {'mae_kg_ha': test['mae_kg_ha'], 'history_mae_kg_ha': test['history_mae_kg_ha'],
                                        'ci95': test['ci95'], 'outcome': test['outcome']}
        if test['outcome'] != 'confirmed':
            warnings.append(f'Not confirmed on the 2022-2024 test (MAE {test["mae_kg_ha"]} vs history {test["history_mae_kg_ha"]} kg/ha)')
    if basis == 'reported_by_user':
        warnings.append('Sowing date reported by the user; the model was validated on the state calendar')
    if item['validation_status'] == 'reference_only':
        warnings.append('No model beat the simple reference for this crop; the estimate is the reference')
    if trace is not None:  # prospective registry: same inputs for the registered candidates and references
        trace.update(row=dict(row), window=(first, last), history_years=history_years)
    return {**response, **common, 'status': 'ready', 'milestone': row.get('milestone') or f'day_{cutoff}', 'cutoff_days_used': cutoff,
            'estimate_kg_ha': round(max(model.predict(row), 0.0), 1), 'observed_through': row['feature_cutoff'], 'validation': validation,
            'coverage': coverage, 'warnings': warnings}
