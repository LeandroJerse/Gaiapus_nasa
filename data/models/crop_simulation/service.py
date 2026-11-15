"""JSON service of the management comparison: a validated request in, a serialisable result out.

This is the boundary the application calls. It adds nothing scientific: it parses the request strictly (unknown
fields are refused), runs `comparison.run_comparison` and `harvest.compare_harvests`, and returns summaries with the
identity of what produced them. The same request on the same forcing always gives the same `comparison_id`; no clock
enters the identity. Daily trajectories are not part of the result.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta

from . import comparison as cmp
from . import harvest as hv
from .calendar import resolve_month_day
from .engine import ENGINE_VERSION
from .ensemble import RecurringCrop
from .types import ForcingDay, SimulationInputError, SoilProfile, SoilState

REQUEST_CONTRACT = 'management-comparison-request.v1'
RESULT_CONTRACT = 'management-comparison-result.v1'
_REQUEST_KEYS = {'contract', 'location_id', 'parameter_set_id', 'soil', 'initial_storage_fraction', 'reference_id', 'alternatives', 'area_ha',
                 'yield_sources', 'budgets', 'report_currency', 'conversions', 'leap_policy'}
_ALT_KEYS = {'alternative_id', 'name', 'crops', 'irrigation', 'irrigation_efficiency', 'runoff_fraction', 'max_net_mm_per_day', 'max_gross_mm_per_season', 'hypotheses', 'parameter_origin'}
_SOURCE_KEYS = {'origin', 'kg_ha', 'reference', 'validation_status', 'per_member', 'responds_to_management', 'scale', 'period', 'information_cutoff', 'unusable_reason', 'estimated_for_config_sha256'}
_BUDGET_KEYS = {'currency', 'price_per_unit', 'price_unit', 'unit_kg', 'price_reference', 'price_date', 'cost_per_ha', 'cost_basis', 'irrigation_cost_per_m3', 'cost_per_kg', 'costs_complete'}


def _bad(message: str):
    raise SimulationInputError('INVALID_PARAMETER', message)


def _keys(value, allowed: set, where: str) -> dict:
    if not isinstance(value, dict):
        _bad(f'{where} must be an object')
    extra = sorted(set(value) - allowed)
    if extra:
        _bad(f'{where}: unknown field(s) {", ".join(extra)}')
    return value


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def request_digest(request: dict) -> str:
    """Digest of the request content; independent of key order."""
    return _sha(request)


def members_from_daily(weather: dict, years, *, start_month_day: str = '09-01', days: int = 365) -> dict[int, list[ForcingDay]]:
    """One member per season year: `days` consecutive days of a daily series {iso date: {'rain_mm', 'et0_mm'}} from `start_month_day`.

    A season that runs past the series is not a member. A missing value stays missing (None): the engine excludes that
    member with its reason; nothing is filled.
    """
    out = {}
    for year in years:
        start = resolve_month_day(start_month_day, year)
        rows = []
        for i in range(days):
            day = start + timedelta(days=i)
            w = weather.get(day.isoformat())
            if w is None:
                rows = None
                break
            rows.append(ForcingDay(day, w.get('rain_mm'), w.get('et0_mm')))
        if rows:
            out[year] = rows
    return out


def _alternative(raw) -> cmp.ManagementAlternative:
    a = _keys(raw, _ALT_KEYS, 'alternative')
    try:
        crops = tuple(RecurringCrop(c['crop_id'], c['sowing_rule'], int(c.get('year_offset', 0))) for c in a['crops'])
        irrigation = tuple(cmp.IrrigationEvent(e['crop_id'], e['days_after_sowing'], e['net_mm']) for e in a.get('irrigation', ()))
        return cmp.ManagementAlternative(alternative_id=a['alternative_id'], name=str(a.get('name') or a['alternative_id']), crops=crops, irrigation=irrigation,
                                         irrigation_efficiency=a.get('irrigation_efficiency', 1.0), runoff_fraction=a.get('runoff_fraction', 0.0),
                                         max_net_mm_per_day=a.get('max_net_mm_per_day'), max_gross_mm_per_season=a.get('max_gross_mm_per_season'),
                                         hypotheses=tuple(a.get('hypotheses', ())), parameter_origin=a.get('parameter_origin', 'user'))
    except (KeyError, TypeError) as error:
        _bad(f'alternative is missing or has a malformed field: {error}')


def _inputs(block, keys: set, build, crop_field: bool, where: str):
    block = _keys(block or {}, {'common', 'by_alternative'}, where)

    def per_crop(items):
        out = {}
        for crop, raw in (items or {}).items():
            raw = dict(_keys(raw, keys, f'{where}.{crop}'))
            if crop_field:
                raw['crop_id'] = crop
            if 'per_member' in raw and raw['per_member'] is not None:
                raw['per_member'] = {int(k): v for k, v in raw['per_member'].items()}
            try:
                out[crop] = build(**raw)
            except (ValueError, TypeError) as error:
                _bad(f'{where}.{crop}: {error}')
        return out
    return per_crop(block.get('common')), {name: per_crop(items) for name, items in (block.get('by_alternative') or {}).items()}


def _plain(value):
    """Drop internal fields and make everything JSON-safe."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items() if k not in ('exact', 'member_margin_per_ha')}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise SimulationInputError('INVALID_PARAMETER', 'a non-finite number reached the result')
    if isinstance(value, date):
        return value.isoformat()
    return value


def run_request(request: dict, members: dict[int, list[ForcingDay]], *, forcing_source: str) -> dict:
    r = _keys(request, _REQUEST_KEYS, 'request')
    if r.get('contract') != REQUEST_CONTRACT:
        _bad(f'contract must be {REQUEST_CONTRACT}')
    soil = _keys(r.get('soil'), {'profile_id', 'capacity_mm'}, 'soil')
    capacity, fraction = soil.get('capacity_mm'), r.get('initial_storage_fraction')
    for name, v, ok in (('soil.capacity_mm', capacity, lambda x: x > 0), ('initial_storage_fraction', fraction, lambda x: 0 <= x <= 1)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not ok(v):
            _bad(f'{name} is missing or outside its domain')
    if not isinstance(r.get('alternatives'), list) or not isinstance(r.get('location_id'), str) or not isinstance(soil.get('profile_id'), str):
        _bad('location_id, soil.profile_id and alternatives are required')
    alternatives = tuple(_alternative(a) for a in r['alternatives'])
    sources, sources_by = _inputs(r.get('yield_sources'), _SOURCE_KEYS, hv.YieldSource, True, 'yield_sources')
    budgets, budgets_by = _inputs(r.get('budgets'), _BUDGET_KEYS, hv.HarvestBudget, False, 'budgets')
    try:
        conversions = tuple(hv.CurrencyConversion(**c) for c in r.get('conversions', ()))
    except (ValueError, TypeError) as error:
        _bad(f'conversions: {error}')
    result = cmp.run_comparison(cmp.ComparisonRequest(reference_id=r.get('reference_id'), alternatives=alternatives, parameter_set_id=r.get('parameter_set_id'),
                                                      soil=SoilProfile(float(capacity), soil['profile_id']), initial_state=SoilState(float(capacity) * float(fraction)),
                                                      leap_policy=r.get('leap_policy', 'reject')), members)
    try:
        money = hv.compare_harvests(result, sources, budgets, area_ha=r.get('area_ha'), sources_by_alternative=sources_by, budgets_by_alternative=budgets_by,
                                    report_currency=r.get('report_currency'), conversions=conversions)
    except ValueError as error:
        _bad(str(error))
    by_id = {a.alternative_id: a for a in alternatives}
    identity = {'request_sha256': request_digest(request), 'forcing_sha256': result.forcing_sha256, 'engine_version': ENGINE_VERSION, 'parameter_set_id': r['parameter_set_id']}
    identity['sha256'] = _sha(identity)
    out = {
        'contract': RESULT_CONTRACT, 'comparison_id': 'mc_' + identity['sha256'][:24], 'identity': identity, 'location_id': r['location_id'],
        'reference_id': result.reference_id, 'member_ids': result.member_ids, 'excluded': {str(k): v for k, v in result.excluded.items()}, 'coverage': result.coverage,
        'forcing': {'source': forcing_source, 'sha256': result.forcing_sha256, 'kind': 'historical_seasons_replayed'},
        'soil': {'profile_id': soil['profile_id'], 'capacity_mm': float(capacity), 'basis': 'hypothesis_not_measured'},
        'hypotheses': {'initial_storage': {'fraction': fraction, 'basis': 'hypothesis_not_measured'}},
        'area_ha': r.get('area_ha'),
        'alternatives': {name: {'alternative_id': name, 'name': run.name, 'config_sha256': run.config_sha256, 'config': cmp._config(by_id[name]), 'hypotheses': list(by_id[name].hypotheses),
                                'water': run.summary, 'harvest_calendar': run.harvests, 'harvests': money['alternatives'][name]['harvests'],
                                'rotation': money['alternatives'][name]['rotation'], 'coverage': money['alternatives'][name]['coverage'], 'warnings': run.warnings}
                         for name, run in result.runs.items()},
        'differences': {name: {'water': result.differences[name], 'money': money['differences'][name], 'not_comparable': {'water': result.not_comparable[name], 'harvests': money['not_comparable'][name]}}
                        for name in result.differences},
        'differences_kind': result.differences_kind, 'yield_message': money['yield_message'], 'ranking': None,
        'evidence_status': result.evidence_status, 'yield_response': result.yield_response, 'frequency_kind': result.frequency_kind}
    return _plain(out)


def handle(request, members: dict[int, list[ForcingDay]], *, forcing_source: str) -> dict:
    """`run_request` with input errors returned as {'error': {'code', 'message'}} instead of raised."""
    try:
        return run_request(request, members, forcing_source=forcing_source)
    except SimulationInputError as error:
        return {'error': {'code': error.code, 'message': error.message}}
