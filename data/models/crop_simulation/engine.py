"""Continuous daily crop simulation v1: every forcing day is simulated (warm-up, crops and fallow) on one store.

Status: physical_experimental. A software test pass is not an agronomic validation.
Scope of v1: one fixed root-zone store (no root depth, no layers), FAO-56 single Kc, rainfed or net-irrigation events.
"""
from datetime import timedelta

from .calendar import load_parameter_set, validate_crop_plan
from .soil import DAILY_ORDER, step_day
from .types import ForcingDay, ManagementSpec, SimulationInputError, SimulationTrajectory, SoilProfile, SoilState

ENGINE_VERSION = 'crop-simulation-v1'
PHASES = ('establishment', 'vegetative', 'reproductive', 'maturation')


def phase_kc(config: dict, index: int):
    """Same piecewise Kc as the frozen bucket (water_balance.phase_kc)."""
    cumulative = 0
    for phase, length in zip(PHASES, config['phase_days']):
        if index < cumulative + length:
            offset = index - cumulative
            if phase == 'establishment':
                kc = config['kc_initial']
            elif phase == 'vegetative':
                kc = config['kc_initial'] + (config['kc_mid'] - config['kc_initial']) * (offset + 1) / length
            elif phase == 'reproductive':
                kc = config['kc_mid']
            else:
                kc = config['kc_mid'] + (config['kc_end'] - config['kc_mid']) * (offset + 1) / length
            return phase, kc
        cumulative += length
    raise SimulationInputError('INVALID_PARAMETER', 'day outside crop cycle')


def _check_forcing(forcing: list[ForcingDay], start, end):
    if not forcing or forcing[0].date > start or forcing[-1].date < end:
        raise SimulationInputError('FORCING_INCOMPLETE', f'forcing must cover {start}..{end}')
    for a, b in zip(forcing, forcing[1:]):
        if b.date != a.date + timedelta(days=1):
            raise SimulationInputError('FORCING_INCOMPLETE', f'forcing not contiguous after {a.date}')
    for d in forcing:
        if d.precipitation_mm is None or d.et0_mm is None:
            raise SimulationInputError('FORCING_INCOMPLETE', f'missing forcing on {d.date}; missing is not zero')
        if d.precipitation_mm < 0 or d.et0_mm < 0:
            raise SimulationInputError('FORCING_INCOMPLETE', f'negative forcing on {d.date}')


def simulate_daily(plan_items, forcing: list[ForcingDay], soil: SoilProfile, initial_state: SoilState, management: ManagementSpec) -> SimulationTrajectory:
    plan = validate_crop_plan(list(plan_items))
    params = load_parameter_set(plan.parameter_set_id)
    _check_forcing(forcing, plan.start, plan.end)
    if not 0 < management.irrigation_efficiency <= 1:
        raise SimulationInputError('INVALID_PARAMETER', 'irrigation efficiency must be in (0, 1]')
    days = {d.date for d in forcing}
    for when, mm in management.irrigation_net_mm.items():
        if when not in days or not isinstance(mm, (int, float)) or mm != mm or mm < 0:
            raise SimulationInputError('INVALID_PARAMETER', f'irrigation on {when} outside the simulated period or invalid')

    by_day = {}
    for item in plan.items:
        for i in range(item.cycle_days):
            by_day[item.sowing_date + timedelta(days=i)] = (item, i)

    storage = float(initial_state.storage_mm)
    rows = []
    for d in forcing:
        crop = by_day.get(d.date)
        if crop:
            item, das = crop
            config = params['crops'][item.crop_id]
            phase, kc = phase_kc(config, das)
            etc = kc * d.et0_mm
            p = min(0.8, max(0.1, config['p_base'] + 0.04 * (5 - etc)))
        else:
            item, das, phase = None, None, 'fallow'
            kc = params['fallow']['kc']
            etc = kc * d.et0_mm
            p = params['fallow']['p']
        irrigation = float(management.irrigation_net_mm.get(d.date, 0.0))
        step = step_day(storage=storage, capacity=soil.capacity_mm, precipitation=d.precipitation_mm, irrigation=irrigation, etc=etc, p=p,
                        runoff_fraction=management.runoff_fraction)
        storage = step['storage_mm']
        rows.append({'date': d.date.isoformat(), 'crop_id': item.crop_id if item else None, 'phase_code': phase, 'days_after_sowing': das, 'kc': kc,
                     'precipitation_mm': d.precipitation_mm, 'irrigation_net_mm': irrigation, 'irrigation_gross_mm': irrigation / management.irrigation_efficiency,
                     'et0_mm': d.et0_mm, **step, 'root_depth_m': None, 'quality_flags': []})
    totals = {'days': len(rows), 'precipitation_mm': sum(r['precipitation_mm'] for r in rows), 'irrigation_net_mm': sum(r['irrigation_net_mm'] for r in rows),
              'eta_mm': sum(r['eta_mm'] for r in rows), 'drainage_mm': sum(r['drainage_mm'] for r in rows), 'runoff_mm': sum(r['runoff_mm'] for r in rows),
              'storage_start_mm': float(initial_state.storage_mm), 'storage_end_mm': storage,
              'max_abs_daily_residual_mm': max(abs(r['mass_balance_residual_mm']) for r in rows)}
    return SimulationTrajectory(rows, ENGINE_VERSION, plan.parameter_set_id, soil.profile_id, DAILY_ORDER, 'physical_experimental', totals)
