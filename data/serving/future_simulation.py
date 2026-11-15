"""Calculated crop-season planning for harvest years 2027/2028 (protocol data/frozen/future_simulation_v1_protocol.json).

Pipeline: state calendar → analog-block weather ensemble (observed prefix up to as_of when available) → crop-simulation-v1
engine per member → per-season summaries, defined events, bands and representative trajectories → historical yield
reference (never scenario-conditional, never sealed or unpublished labels). Every number comes from the engine run.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from data.forecasting.weather_ensemble import Ensemble, WeatherHistory, build_ensemble, compose_with_observed
from data.ingest.crops import REGISTRY
from data.models.crop_simulation.calendar import load_parameter_set
from data.models.crop_simulation.engine import ENGINE_VERSION, simulate_daily
from data.models.crop_simulation.ensemble import percentile
from data.models.crop_simulation.soil import DAILY_ORDER
from data.models.crop_simulation.types import CropPlanItem, ForcingDay, ManagementSpec, SimulationInputError, SoilProfile, SoilState

__all__ = ['SimulationInputError']

CONTRACT = 'future-simulation-run.v1'
PROTOCOL_ID = 'future_simulation_v1'
PARAMETER_SET = 'gaiapus-fao56-br-v1'
SEALED_LABEL_YEARS = frozenset({2022, 2023, 2024})  # sealed prospective test: never read here
REFERENCE_YEARS = 5
DEFAULT_STORAGE_FRACTION = 0.5
SENSITIVITY_FRACTIONS = (0.2, 0.9)
PLANS = {'soy': ('soy',), 'soy>maize2': ('soy', 'maize2')}
UNSUPPORTED = {'rice': 'flooded_system_not_represented', 'coffee_arabica': 'perennial_engine_missing', 'coffee_conilon': 'perennial_engine_missing',
               'coffee_total_mg': 'unsupported', 'maize1': 'unsupported_crop_engine', 'bean1': 'unsupported_crop_engine',
               'bean2': 'unsupported_crop_engine', 'wheat': 'unsupported_crop_engine', 'maize2': 'unsupported_crop_engine'}
EVENTS = (('reproductive_stress_days_ge_10', lambda s: s['reproductive_stress_days'] >= 10),
          ('crop_deficit_mm_ge_100', lambda s: s['crop_deficit_mm'] >= 100),
          ('dry_spell_ge_15_days_in_season', lambda s: s['max_dry_spell_days'] >= 15))
LIMITATIONS = ['climatological_scenario_not_daily_forecast', 'historical_frequency_not_probability', 'single_bucket_no_root_profile',
               'soil_hypothesis', 'calendar_hypothesis', 'fallow_parameters_hypothesis', 'not_field_validated', 'no_heat_frost_pest_effects']


@dataclass(frozen=True)
class SeasonItem:
    crop_id: str
    harvest_year: int
    sowing_date: date
    harvest_date: date
    cycle_days: int
    calendar_source: str


@dataclass(frozen=True)
class SeasonPlan:
    plan_id: str
    items: tuple[SeasonItem, ...]
    rules: tuple[dict, ...] = ()


@dataclass(frozen=True)
class PlaceSpec:
    location_id: str
    name: str
    state_code: str
    history: WeatherHistory
    observed: WeatherHistory | None
    soil: SoilProfile
    labels: dict = field(default_factory=dict)  # {(crop_id, harvest_year): yield kg/ha}, published PAM only


def plan_seasons(state_code: str, plan_id: str, harvest_years: list[int]) -> SeasonPlan:
    if plan_id not in PLANS:
        raise SimulationInputError(UNSUPPORTED.get(plan_id, 'unsupported'), f'no crop engine plan for {plan_id}')
    params = load_parameter_set(PARAMETER_SET)
    items, rules = [], []
    for year in sorted(set(harvest_years)):
        prev = None
        for crop in PLANS[plan_id]:
            reg = REGISTRY[crop]
            if state_code not in reg['sowing']:
                raise SimulationInputError('invalid_calendar', f'no {crop} calendar for state {state_code}')
            month, day = reg['sowing'][state_code]
            sow = date(year + reg['year_offset'], month, day)
            if prev is not None and sow <= prev.harvest_date:
                shifted = prev.harvest_date + timedelta(days=1)
                rules.append({'rule': f'{crop}_shifted_after_previous_harvest', 'harvest_year': year, 'state_calendar': sow.isoformat(), 'used': shifted.isoformat()})
                sow = shifted
            cycle = sum(params['crops'][crop]['phase_days'])
            item = SeasonItem(crop, year, sow, sow + timedelta(days=cycle - 1), cycle, 'data/ingest/crops.REGISTRY fixed state calendar (hypothesis)')
            items.append(item)
            prev = item
    items.sort(key=lambda i: i.sowing_date)
    for a, b in zip(items, items[1:]):
        if b.sowing_date <= a.harvest_date:
            raise SimulationInputError('invalid_calendar', f'{b.crop_id} {b.sowing_date} overlaps {a.crop_id} harvest {a.harvest_date}')
    return SeasonPlan(plan_id, tuple(items), tuple(rules))


def _season_metrics(rows: list[dict], s: SeasonItem) -> dict:
    lo, hi = s.sowing_date.isoformat(), s.harvest_date.isoformat()
    crop = [r for r in rows if r['crop_id'] == s.crop_id and lo <= r['date'] <= hi]
    spell = best = 0
    for r in crop:
        spell = spell + 1 if r['precipitation_mm'] < 1 else 0
        best = max(best, spell)
    return {'crop_deficit_mm': sum(r['et_deficit_mm'] for r in crop), 'crop_eta_mm': sum(r['eta_mm'] for r in crop),
            'crop_etc_mm': sum(r['etc_mm'] for r in crop), 'stress_days': sum(1 for r in crop if r['stress_coefficient'] < 1),
            'reproductive_stress_days': sum(1 for r in crop if r['phase_code'] == 'reproductive' and r['stress_coefficient'] < 1),
            'max_dry_spell_days': best, 'precipitation_mm': sum(r['precipitation_mm'] for r in crop),
            'mean_storage_mm': sum(r['storage_mm'] for r in crop) / len(crop)}


def _quantiles(values: list[float]) -> dict:
    return {'p10': percentile(values, .1), 'p50': percentile(values, .5), 'p90': percentile(values, .9), 'n': len(values)}


def _reference_yield(place: PlaceSpec, s: SeasonItem, as_of: datetime) -> dict:
    # PAM year Y is published by 31 Oct of Y+1; sealed test years are never read.
    published = as_of.year - 1 if (as_of.month, as_of.day) >= (10, 31) else as_of.year - 2
    years = sorted(y for (c, y) in place.labels if c == s.crop_id and y <= published and y not in SEALED_LABEL_YEARS)
    if not years:
        return {'status': 'unavailable', 'reason_code': 'source_unavailable', 'value_kg_ha': None}
    used = years[-REFERENCE_YEARS:]
    last = used[-1]
    return {'status': 'reference', 'method': 'mean_last_5_published_pam_yields', 'value_kg_ha': sum(place.labels[(s.crop_id, y)] for y in used) / len(used),
            'years_used': used, 'last_label_year': last, 'history_horizon_years': s.harvest_year - last,
            'label_release_rule': 'IBGE PAM year Y published by 31 Oct of Y+1', 'research_status': 'reference_selected',
            'exposure_flags': ['sealed_test_2022_2024_not_read'], 'conditional_on_scenario': False, 'interval_method': None,
            'scale': 'municipal (not a field estimate)'}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def run_future_simulation(place: PlaceSpec, *, plan_id: str, harvest_years: list[int], as_of: datetime, issued_at: datetime, seed: int,
                          analog_years: list[int] | None = None, max_members: int | None = None,
                          storage_fraction: float = DEFAULT_STORAGE_FRACTION) -> dict:
    plan = plan_seasons(place.state_code, plan_id, harvest_years)
    cutoff = as_of.date()
    observed = place.observed
    if observed is not None and (not observed.days or observed.days[-1].date != cutoff):
        observed = None  # an observed series that does not reach as_of cannot be composed
    first_sow = plan.items[0].sowing_date
    if first_sow <= cutoff and (observed is None or observed.days[0].date > first_sow):
        raise SimulationInputError('season_already_started_without_observations', f'{plan.items[0].crop_id} sown {first_sow} before as_of {cutoff} without observations')
    target_end = max(i.harvest_date for i in plan.items)
    ens: Ensemble = build_ensemble(place.history, target_start=cutoff + timedelta(days=1), target_end=target_end, as_of=as_of, seed=seed,
                                   analog_years=analog_years, max_members=max_members)
    if observed is not None:
        ens = compose_with_observed(ens, observed, as_of=as_of)
    items = [CropPlanItem(i.crop_id, PARAMETER_SET, i.sowing_date) for i in plan.items]

    def simulate(member, fraction):
        forcing = [ForcingDay(d.simulated_date, d.precipitation_mm, d.et0_mm) for d in member.days]
        return simulate_daily(items, forcing, place.soil, SoilState(place.soil.capacity_mm * fraction), ManagementSpec())

    runs = {m.member_id: simulate(m, storage_fraction) for m in ens.members}
    ids = [m.member_id for m in ens.members]
    dates = [r['date'] for r in runs[ids[0]].rows]

    seasons = []
    for s in plan.items:
        per = {mid: _season_metrics(runs[mid].rows, s) for mid in ids}
        keys = next(iter(per.values())).keys()
        seasons.append({
            'crop_id': s.crop_id, 'harvest_year': s.harvest_year,
            'season_label': f'{s.sowing_date.year}/{s.harvest_year}' if s.sowing_date.year != s.harvest_year else str(s.harvest_year),
            'sowing_date': s.sowing_date.isoformat(), 'expected_harvest_date': s.harvest_date.isoformat(), 'cycle_days': s.cycle_days,
            'calendar_source': s.calendar_source, 'quantiles': {k: _quantiles([per[m][k] for m in ids]) for k in keys},
            'events': [{'event_id': e, 'share_of_members': sum(1 for m in ids if test(per[m])) / len(ids), 'members': len(ids)} for e, test in EVENTS],
            'interval_method': 'climate_ensemble_spread_only', 'members': {m: per[m] for m in ids}, 'yield': _reference_yield(place, s, as_of)})

    # Representative trajectories: whole members, never a day-by-day patchwork (protocol representative_member).
    whole = {m: (sum(r['precipitation_mm'] for r in runs[m].rows), sum(r['et_deficit_mm'] for r in runs[m].rows if r['crop_id']),
                 sum(r['storage_mm'] for r in runs[m].rows) / len(dates)) for m in ids}
    med = [percentile([whole[m][k] for m in ids], .5) for k in range(3)]
    sd = [math.sqrt(sum((whole[m][k] - med[k]) ** 2 for m in ids) / len(ids)) or 1.0 for k in range(3)]
    central = min(ids, key=lambda m: (sum(((whole[m][k] - med[k]) / sd[k]) ** 2 for k in range(3)), m))
    dry = min(ids, key=lambda m: (-whole[m][1], m)); wet = min(ids, key=lambda m: (whole[m][1], m))

    sens = {}
    member_c = next(m for m in ens.members if m.member_id == central)
    for f in SENSITIVITY_FRACTIONS:
        alt = simulate(member_c, f)
        sens[str(f)] = {f'{s.crop_id}:{s.harvest_year}': _season_metrics(alt.rows, s)['crop_deficit_mm'] - _season_metrics(runs[central].rows, s)['crop_deficit_mm'] for s in plan.items}

    cols = ('crop_id', 'phase_code', 'precipitation_mm', 'et0_mm', 'etc_mm', 'eta_mm', 'et_deficit_mm', 'storage_mm', 'drainage_mm', 'stress_coefficient', 'mass_balance_residual_mm')
    members = [{'member_id': m.member_id, 'analog_year': m.analog_year, **{c: [r[c] for r in runs[m.member_id].rows] for c in cols}} for m in ens.members]
    bands = {v: {q: [percentile([mm[v][i] for mm in members], p) for i in range(len(dates))] for q, p in (('p10', .1), ('p50', .5), ('p90', .9))}
             for v in ('storage_mm', 'precipitation_mm', 'et_deficit_mm')}
    series = {'dates': dates, 'capacity_mm': place.soil.capacity_mm, 'members': members, 'bands': bands,
              'day_kind': [d.kind for d in ens.members[0].days]}

    identity = {'protocol': PROTOCOL_ID, 'contract': CONTRACT, 'location_id': place.location_id, 'plan': plan_id,
                'items': [[i.crop_id, i.sowing_date.isoformat(), i.harvest_date.isoformat()] for i in plan.items], 'harvest_years': sorted(set(harvest_years)),
                'as_of': as_of.isoformat(), 'history_sha256': place.history.source.get('sha256'),
                'observed_sha256': observed.source.get('sha256') if observed else None, 'ensemble_sha256': ens.manifest['content_sha256'],
                'engine': ENGINE_VERSION, 'parameter_set': PARAMETER_SET, 'soil': asdict(place.soil), 'seed': seed,
                'analog_years': analog_years, 'max_members': max_members, 'storage_fraction': storage_fraction,
                'labels_sha256': _sha(sorted((c, y, v) for (c, y), v in place.labels.items()))}
    run_id = 'sim_' + _sha(identity)[:24]
    m = ens.manifest
    run = {
        'run_id': run_id, 'contract': CONTRACT, 'protocol_id': PROTOCOL_ID, 'execution_status': 'succeeded',
        'output_mode': 'simulation_conditional_on_historical_weather',
        'scientific_status': {'weather': 'climatological_scenario', 'water_balance': 'physical_experimental', 'phenology': 'physical_experimental',
                              'yield': 'reference' if any(s['yield']['status'] == 'reference' for s in seasons) else 'unavailable'},
        'location_id': place.location_id, 'place_name': place.name, 'state_code': place.state_code, 'plan_id': plan_id,
        'harvest_years': sorted(set(harvest_years)), 'as_of': as_of.isoformat(), 'issued_at': issued_at.isoformat(),
        'simulation_start': dates[0], 'simulation_end': dates[-1], 'weather_segments': m['weather_segments'],
        'ensemble': {k: m[k] for k in ('method', 'members', 'effective_source_years', 'analog_years', 'eligible_years', 'excluded_years',
                                       'coverage_class', 'seed', 'calendar_mapping', 'analog_years_requested')},
        'sources': {'history': place.history.source, 'observed': observed.source if observed else None, 'et0': m['et0_method']},
        'engine': {'version': ENGINE_VERSION, 'parameter_set_id': PARAMETER_SET, 'daily_order': list(DAILY_ORDER)},
        'soil': {'capacity_mm': place.soil.capacity_mm, 'profile_id': place.soil.profile_id, 'is_hypothesis': True},
        'calendar_rules': list(plan.rules), 'seasons': seasons,
        'representative': {'central': central, 'dry': dry, 'wet': wet, 'rule': 'protocol future_simulation_v1 representative_member; dry/wet = max/min crop deficit'},
        'initial_condition': {'storage_fraction': storage_fraction, 'is_hypothesis': True,
                              'warm_up': {'start': observed.days[0].date.isoformat(), 'end': cutoff.isoformat(), 'source': 'observed'} if observed else None,
                              'sensitivity': sens, 'sensitivity_metric': 'season crop deficit difference (mm) on the central member'},
        'limitations': LIMITATIONS, 'identity': identity,
    }
    run['checksums'] = {'series_sha256': _sha(series), 'identity_sha256': _sha(identity)}
    return {'run': run, 'series': series}
