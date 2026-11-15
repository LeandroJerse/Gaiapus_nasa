"""Comparison of 2-4 management alternatives of the same place on the same climate members.

Every alternative is simulated by the existing daily engine with the same forcing days, soil and initial state; only
the management changes (sowing rules, crops of the plan, irrigation). Differences are formed per member against the
reference alternative first and summarised afterwards, on the members every alternative could simulate.

Irrigation is declared relative to the sowing of a crop (days after sowing), so the same decision lands on the right
day of every member year. Daily capacity and available water limit what is applied; the shortfall is reported.
An automatic irrigation policy (deciding on day t from the state up to t) is not implemented here.

Status: physical_experimental. The water engine does not estimate yield: nothing here says a management raises or
lowers kg/ha. Frequencies over members are historical frequencies, not calibrated probabilities.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .calendar import load_parameter_set, resolve_month_day
from .engine import simulate_daily
from .ensemble import MAX_MEMBERS, MIN_PAIRED_MEMBERS, RecurringCrop, Scenario, _apply, percentile
from .types import CropPlanItem, ForcingDay, ManagementSpec, SimulationInputError, SimulationTrajectory, SoilProfile, SoilState

MIN_ALTERNATIVES, MAX_ALTERNATIVES = 2, 4
MAX_IRRIGATION_EVENTS = 60
_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
LIMITED = 'irrigation_limited_by_capacity_or_available_water'


@dataclass(frozen=True)
class IrrigationEvent:
    """Net water applied `days_after_sowing` of one crop of the plan (0 = sowing day)."""

    crop_id: str
    days_after_sowing: int
    net_mm: float


@dataclass(frozen=True)
class ManagementAlternative:
    alternative_id: str
    name: str
    crops: tuple[RecurringCrop, ...]
    irrigation: tuple[IrrigationEvent, ...] = ()
    irrigation_efficiency: float = 1.0
    runoff_fraction: float = 0.0
    max_net_mm_per_day: float | None = None       # capacity of the system
    max_gross_mm_per_season: float | None = None  # water available for the whole simulated period
    hypotheses: tuple[str, ...] = ()
    parameter_origin: str = 'user'


@dataclass(frozen=True)
class ComparisonRequest:
    reference_id: str
    alternatives: tuple[ManagementAlternative, ...]
    parameter_set_id: str
    soil: SoilProfile
    initial_state: SoilState
    scenario: Scenario = field(default_factory=Scenario)
    leap_policy: str = 'reject'


@dataclass
class AlternativeRun:
    alternative_id: str
    name: str
    config_sha256: str
    metrics: dict[str, dict[int, float]]
    summary: dict[str, dict]
    harvests: dict[str, dict]
    warnings: list[str]
    trajectories: dict[int, SimulationTrajectory]


@dataclass
class ComparisonResult:
    reference_id: str
    member_ids: list[int]
    excluded: dict[int, dict[str, str]]
    coverage: dict
    runs: dict[str, AlternativeRun]
    differences: dict[str, dict[str, dict]]
    not_comparable: dict[str, list[str]]
    forcing_sha256: str
    differences_kind: str = 'paired_per_member_then_summarised'
    evidence_status: str = 'physical_experimental'
    yield_response: str = 'not_estimated_by_the_water_engine'
    frequency_kind: str = 'historical_frequency_not_probability'


def _number(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _fail(message: str, code: str = 'INVALID_PARAMETER'):
    raise SimulationInputError(code, message)


def summarise(values: list[float]) -> dict:
    """Mean (equal weights), median and spread over the members. The mean is not the median."""
    return {'n': len(values), 'mean': sum(values) / len(values), 'p10': percentile(values, .1), 'p50': percentile(values, .5),
            'p90': percentile(values, .9), 'min': min(values), 'max': max(values), 'weights': 'equal'}


def _validate(request: ComparisonRequest) -> dict:
    alts = request.alternatives
    if not MIN_ALTERNATIVES <= len(alts) <= MAX_ALTERNATIVES:
        _fail(f'a comparison takes {MIN_ALTERNATIVES} to {MAX_ALTERNATIVES} alternatives', 'RESOURCE_LIMIT' if len(alts) > MAX_ALTERNATIVES else 'INVALID_PARAMETER')
    ids = [a.alternative_id for a in alts]
    for i in ids:
        if not isinstance(i, str) or not _ID.match(i):
            _fail('alternative ids are lower-case letters, digits, "-" or "_" (1-40 characters)')
    if len(set(ids)) != len(ids):
        _fail('alternative ids must be unique')
    if request.reference_id not in ids:
        _fail('the reference alternative is not among the alternatives')
    params = load_parameter_set(request.parameter_set_id)
    aliases = params.get('aliases', {})
    for a in alts:
        if not a.crops:
            _fail(f'{a.alternative_id}: a management needs at least one crop')
        if not _number(a.irrigation_efficiency) or not 0 < a.irrigation_efficiency <= 1:
            _fail(f'{a.alternative_id}: irrigation efficiency must be in (0, 1]')
        if not _number(a.runoff_fraction) or not 0 <= a.runoff_fraction <= 1:
            _fail(f'{a.alternative_id}: runoff fraction must be in [0, 1]')
        for name in ('max_net_mm_per_day', 'max_gross_mm_per_season'):
            v = getattr(a, name)
            if v is not None and (not _number(v) or v <= 0):
                _fail(f'{a.alternative_id}: {name} must be a finite, positive number or absent')
        if len(a.irrigation) > MAX_IRRIGATION_EVENTS:
            _fail(f'{a.alternative_id}: at most {MAX_IRRIGATION_EVENTS} irrigation events', 'RESOURCE_LIMIT')
        crops = {aliases.get(c.crop_id, c.crop_id) for c in a.crops}
        for e in a.irrigation:
            crop = aliases.get(e.crop_id, e.crop_id)
            if crop not in crops or crop not in params['crops']:
                _fail(f'{a.alternative_id}: irrigation for {e.crop_id}, which is not a crop of this management')
            cycle = sum(params['crops'][crop]['phase_days'])
            if isinstance(e.days_after_sowing, bool) or not isinstance(e.days_after_sowing, int) or not 0 <= e.days_after_sowing < cycle:
                _fail(f'{a.alternative_id}: irrigation day {e.days_after_sowing} is outside the {cycle}-day cycle of {crop}')
            if not _number(e.net_mm) or e.net_mm < 0:
                _fail(f'{a.alternative_id}: irrigation depth must be a finite, non-negative number of mm')
    return params


def _plan(alt: ManagementAlternative, year: int, parameter_set_id: str, leap_policy: str) -> list[CropPlanItem]:
    items = []
    for c in alt.crops:
        sow = resolve_month_day(c.sowing_rule, year + c.year_offset, leap_policy=leap_policy)
        if sow is None:
            raise SimulationInputError('INVALID_DATE', f'{c.sowing_rule} does not exist in {year + c.year_offset}')
        items.append(CropPlanItem(c.crop_id, parameter_set_id, sow, c.reference_yield_kg_ha))
    return items


def resolve_irrigation(alt: ManagementAlternative, year: int, parameter_set_id: str, leap_policy: str) -> dict[date, float]:
    """Requested net irrigation by calendar date for the member `year` (before capacity and water limits)."""
    aliases = load_parameter_set(parameter_set_id).get('aliases', {})
    sowing = {aliases.get(i.crop_id, i.crop_id): i.sowing_date for i in _plan(alt, year, parameter_set_id, leap_policy)}
    out: dict[date, float] = {}
    for e in alt.irrigation:
        day = sowing[aliases.get(e.crop_id, e.crop_id)] + timedelta(days=e.days_after_sowing)
        out[day] = out.get(day, 0.0) + float(e.net_mm)
    return out


def _limit(requested: dict[date, float], alt: ManagementAlternative) -> tuple[dict[date, float], float]:
    """Apply the daily capacity and the available water in date order. Returns (applied net, unmet net)."""
    applied, unmet = {}, 0.0
    left = alt.max_gross_mm_per_season
    for day in sorted(requested):
        want = requested[day]
        give = want if alt.max_net_mm_per_day is None else min(want, alt.max_net_mm_per_day)
        if left is not None:
            give = min(give, left * alt.irrigation_efficiency)
            left -= give / alt.irrigation_efficiency
        unmet += want - give
        if give > 0:
            applied[day] = give
    return applied, unmet


def _metrics(t: SimulationTrajectory, unmet: float) -> dict[str, float]:
    rows = t.rows
    out = {'drainage_mm': sum(r['drainage_mm'] for r in rows), 'eta_mm': sum(r['eta_mm'] for r in rows), 'runoff_mm': sum(r['runoff_mm'] for r in rows),
           'precipitation_mm': sum(r['precipitation_mm'] for r in rows), 'irrigation_net_mm': sum(r['irrigation_net_mm'] for r in rows),
           'irrigation_gross_mm': sum(r['irrigation_gross_mm'] for r in rows), 'irrigation_unmet_net_mm': unmet,
           'storage_end_mm': rows[-1]['storage_mm'], 'crop_days': sum(1 for r in rows if r['crop_id'] is not None)}
    for crop in dict.fromkeys(r['crop_id'] for r in rows if r['crop_id'] is not None):
        cr = [r for r in rows if r['crop_id'] == crop]
        eta, etc = sum(r['eta_mm'] for r in cr), sum(r['etc_mm'] for r in cr)
        out.update({f'{crop}.deficit_mm': sum(r['et_deficit_mm'] for r in cr), f'{crop}.eta_mm': eta, f'{crop}.etc_mm': etc,
                    f'{crop}.stress_days': sum(1 for r in cr if r['stress_coefficient'] < 1),
                    f'{crop}.reproductive_stress_days': sum(1 for r in cr if r['phase_code'] == 'reproductive' and r['stress_coefficient'] < 1),
                    f'{crop}.irrigation_gross_mm': sum(r['irrigation_gross_mm'] for r in cr)})
        if etc > 0:
            out[f'{crop}.water_satisfaction'] = eta / etc
    return out


def _config(alt: ManagementAlternative) -> dict:
    return {'crops': [[c.crop_id, c.sowing_rule, c.year_offset] for c in alt.crops],
            'irrigation': sorted([e.crop_id, e.days_after_sowing, e.net_mm] for e in alt.irrigation),
            'irrigation_efficiency': alt.irrigation_efficiency, 'runoff_fraction': alt.runoff_fraction,
            'max_net_mm_per_day': alt.max_net_mm_per_day, 'max_gross_mm_per_season': alt.max_gross_mm_per_season}


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def forcing_digest(members: dict[int, list[ForcingDay]]) -> str:
    return _sha({str(y): [[d.date.isoformat(), d.precipitation_mm, d.et0_mm] for d in members[y]] for y in sorted(members)})


def run_comparison(request: ComparisonRequest, members: dict[int, list[ForcingDay]]) -> ComparisonResult:
    params = _validate(request)
    if len(members) > MAX_MEMBERS:
        _fail(f'at most {MAX_MEMBERS} members', 'RESOURCE_LIMIT')
    aliases = params.get('aliases', {})
    alts = sorted(request.alternatives, key=lambda a: a.alternative_id)  # results never depend on the order received
    raw: dict[str, dict[int, tuple[SimulationTrajectory, dict[str, float]]]] = {a.alternative_id: {} for a in alts}
    excluded: dict[int, dict[str, str]] = {}
    for year in sorted(members):
        forcing = _apply(members[year], request.scenario)  # one forcing object per member, shared by every alternative
        for a in alts:
            try:
                plan = _plan(a, year, request.parameter_set_id, request.leap_policy)
                applied, unmet = _limit(resolve_irrigation(a, year, request.parameter_set_id, request.leap_policy), a)
                t = simulate_daily(plan, forcing, request.soil, request.initial_state, ManagementSpec(applied, a.irrigation_efficiency, a.runoff_fraction))
            except SimulationInputError as error:
                if error.code in ('FORCING_INCOMPLETE', 'INVALID_DATE'):
                    excluded.setdefault(year, {})[a.alternative_id] = error.code
                    continue
                raise
            raw[a.alternative_id][year] = (t, _metrics(t, unmet))
    common = sorted(y for y in members if all(y in raw[a.alternative_id] for a in alts))
    if len(common) < MIN_PAIRED_MEMBERS:
        _fail(f'{len(common)} members common to every alternative; at least {MIN_PAIRED_MEMBERS} needed', 'INSUFFICIENT_COVERAGE')
    runs: dict[str, AlternativeRun] = {}
    for a in alts:
        keys = sorted(set.intersection(*(set(raw[a.alternative_id][y][1]) for y in common)))
        metrics = {k: {y: raw[a.alternative_id][y][1][k] for y in common} for k in keys}
        harvests = {}
        for c in a.crops:
            crop = aliases.get(c.crop_id, c.crop_id)
            harvests[crop] = {'crop_id': crop, 'sowing_rule': c.sowing_rule, 'year_offset': c.year_offset, 'cycle_days': sum(params['crops'][crop]['phase_days']),
                              'reference_yield_kg_ha': c.reference_yield_kg_ha}
        warnings = [LIMITED] if any(v > 1e-12 for v in metrics['irrigation_unmet_net_mm'].values()) else []
        runs[a.alternative_id] = AlternativeRun(a.alternative_id, a.name, _sha(_config(a)), metrics, {k: summarise(list(v.values())) for k, v in metrics.items()},
                                                harvests, warnings, {y: raw[a.alternative_id][y][0] for y in common})
    ref = runs[request.reference_id]
    differences, not_comparable = {}, {}
    for name, run in runs.items():
        if name == request.reference_id:
            continue
        shared = [k for k in run.metrics if k in ref.metrics]
        differences[name] = {k: summarise([run.metrics[k][y] - ref.metrics[k][y] for y in common]) for k in shared}
        not_comparable[name] = sorted(set(run.metrics) ^ set(ref.metrics))
    ordered = {a.alternative_id: runs[a.alternative_id] for a in request.alternatives}
    return ComparisonResult(request.reference_id, common, dict(sorted(excluded.items())), {'members': len(common), 'candidates': len(members)},
                            ordered, differences, not_comparable, forcing_digest(members))
