"""Historical analog-block ensemble (protocol future_simulation_v1 §ensemble_policy).

Each member copies the whole target window from consecutive historical days that start at the same month-day of one
analog year. All variables of a day travel together (multivariate coherence) and consecutive years stay consecutive
(multi-year coherence). Members are never duplicated; missing values exclude a year; nothing after `as_of` is read.

The history source is injectable (`HistorySource`): POWER point files today; gridMET or area-weighted series can be
added as other implementations without touching the generator.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from data.models.crop_simulation.types import SimulationInputError
from data.models.water_balance import et0_daily

__all__ = ['SimulationInputError']

METHOD = 'historical_analog_blocks_v1'
FILL = -999.0
MIN_YEARS, STANDARD_YEARS = 10, 15


@dataclass(frozen=True)
class DailyWeather:
    date: date
    precipitation_mm: float | None
    tmin_c: float | None
    tmax_c: float | None
    radiation_mj_m2: float | None
    wind_m_s: float | None
    dewpoint_c: float | None
    pressure_kpa: float | None
    #: Source-specific variables passed through untouched to the scenario day (e.g. {'rh2m_pct': ...}); not used by ET0.
    extras: Mapping[str, float | None] = field(default_factory=dict)

    def complete(self) -> bool:
        return None not in (self.precipitation_mm, self.tmin_c, self.tmax_c, self.radiation_mj_m2, self.wind_m_s, self.dewpoint_c, self.pressure_kpa)


@dataclass(frozen=True)
class WeatherHistory:
    location_id: str
    latitude: float
    elevation_m: float
    days: tuple[DailyWeather, ...]
    source: dict


class HistorySource(Protocol):
    def load(self, location_id: str) -> WeatherHistory: ...


class PowerPointFile:
    """NASA POWER daily point JSON (community AG) as already collected; sentinels become missing, never zero."""

    VARS = {'precipitation_mm': 'IMERG_PRECTOT', 'tmin_c': 'T2M_MIN', 'tmax_c': 'T2M_MAX', 'radiation_mj_m2': 'ALLSKY_SFC_SW_DWN',
            'wind_m_s': 'WS2M', 'dewpoint_c': 'T2MDEW', 'pressure_kpa': 'PS'}

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self, location_id: str) -> WeatherHistory:
        if not self.path.is_file():
            raise SimulationInputError('source_unavailable', f'weather history not found: {self.path.name}')
        raw = self.path.read_bytes()
        payload = json.loads(raw)
        lon, lat, elevation = payload['geometry']['coordinates']
        p = payload['properties']['parameter']
        val = lambda v: None if v is None or v <= FILL else float(v)
        rh = p.get('RH2M')
        days = tuple(DailyWeather(datetime.strptime(k, '%Y%m%d').date(), *(val(p[name].get(k)) for name in self.VARS.values()),
                                  extras={'rh2m_pct': val(rh.get(k))} if rh is not None else {})
                     for k in sorted(p['IMERG_PRECTOT']))
        return WeatherHistory(location_id, float(lat), float(elevation), days, {
            'provider': 'NASA POWER', 'product': 'NASA POWER daily point, community AG (rain IMERG_PRECTOT)', 'file': self.path.name,
            'sha256': hashlib.sha256(raw).hexdigest(), 'period': {'start': days[0].date.isoformat(), 'end': days[-1].date.isoformat()} if days else None,
            'variables': dict(self.VARS), 'availability': 'unknown (archived retrospective series; observation date <= as_of enforced)'})


@dataclass(frozen=True)
class ScenarioDay:
    simulated_date: date
    source_observation_date: date
    kind: str  # 'observed' | 'climatological_scenario'
    precipitation_mm: float
    et0_mm: float
    tmin_c: float
    tmax_c: float
    radiation_mj_m2: float | None = None
    wind_m_s: float | None = None
    dewpoint_c: float | None = None
    pressure_kpa: float | None = None
    extras: Mapping[str, float | None] = field(default_factory=dict)


def _scenario_day(simulated: date, r: DailyWeather, kind: str, et0: float) -> ScenarioDay:
    return ScenarioDay(simulated, r.date, kind, r.precipitation_mm, et0, r.tmin_c, r.tmax_c, r.radiation_mj_m2, r.wind_m_s, r.dewpoint_c,
                       r.pressure_kpa, dict(r.extras))


@dataclass(frozen=True)
class Member:
    member_id: str
    analog_year: int | None
    days: tuple[ScenarioDay, ...]


@dataclass(frozen=True)
class Ensemble:
    members: tuple[Member, ...]
    manifest: dict = field(default_factory=dict)


def _aligned(year: int, d: date) -> date:
    try:
        return date(year, d.month, d.day)
    except ValueError:  # 29 Feb in a non-leap analog year: start on 1 Mar, recorded via drift
        return date(year, 3, 1)


def _drift(a: date, b: date) -> int:
    ref = lambda x: date(2000, x.month, x.day)
    diff = abs((ref(a) - ref(b)).days)
    return min(diff, 366 - diff)


def _et0(d: DailyWeather, lat: float, elev: float) -> float:
    return et0_daily(d.date, lat, elev, d.tmin_c, d.tmax_c, d.radiation_mj_m2, d.wind_m_s, d.pressure_kpa, dewpoint=d.dewpoint_c)['et0_mm']


def _hash_members(members) -> str:
    rows = [[m.member_id, [[x.simulated_date.isoformat(), x.source_observation_date.isoformat(), x.kind, x.precipitation_mm, round(x.et0_mm, 10),
                          x.tmin_c, x.tmax_c, x.radiation_mj_m2, x.wind_m_s, x.dewpoint_c, x.pressure_kpa, sorted(x.extras.items())] for x in m.days]] for m in members]
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def build_ensemble(history: WeatherHistory, *, target_start: date, target_end: date, as_of: datetime, seed: int,
                   analog_years: list[int] | None = None, max_members: int | None = None) -> Ensemble:
    cutoff = as_of.date()
    if target_start <= cutoff or target_end < target_start:
        raise SimulationInputError('invalid_calendar', 'the scenario window must start after as_of and end after it starts')
    n = (target_end - target_start).days + 1
    by_date = {d.date: d for d in history.days if d.date <= cutoff}
    years = sorted({d.year for d in by_date})
    eligible, excluded, cache = [], {}, {}
    for y in years:
        start = _aligned(y, target_start)
        window = [start + timedelta(days=i) for i in range(n)]
        if window[-1] > cutoff or window[0] not in by_date or window[-1] not in by_date:
            continue
        rows = [by_date.get(x) for x in window]
        if any(r is None or not r.complete() for r in rows):
            excluded[str(y)] = 'missing_values'
            continue
        try:
            cache[y] = [(r, _et0(r, history.latitude, history.elevation_m)) for r in rows]
        except ValueError:
            excluded[str(y)] = 'et0_domain'
            continue
        eligible.append(y)
    if len(eligible) < MIN_YEARS:
        raise SimulationInputError('insufficient_history', f'{len(eligible)} complete analog years; at least {MIN_YEARS} required')
    if analog_years is not None:
        bad = sorted(set(analog_years) - set(eligible))
        if bad:
            raise SimulationInputError('insufficient_history', f'analog years not eligible: {bad}')
        chosen = sorted(set(analog_years))
    else:
        chosen = eligible
    if max_members is not None and max_members < len(chosen):
        chosen = sorted(random.Random(seed).sample(chosen, max_members))
    members, drift = [], 0
    for y in chosen:
        days = tuple(_scenario_day(target_start + timedelta(days=i), r, 'climatological_scenario', e) for i, (r, e) in enumerate(cache[y]))
        drift = max(drift, max(_drift(d.simulated_date, d.source_observation_date) for d in days))
        members.append(Member(f'a{y}', y, days))
    manifest = {
        'method': METHOD, 'seed': seed, 'as_of': as_of.isoformat(), 'location_id': history.location_id,
        'target': {'start': target_start.isoformat(), 'end': target_end.isoformat(), 'days': n},
        'source': history.source, 'eligible_years': eligible, 'excluded_years': excluded,
        'analog_years_requested': sorted(set(analog_years)) if analog_years is not None else None,
        'analog_years': chosen, 'members': len(members), 'effective_source_years': len(set(chosen)),
        'coverage_class': 'standard' if len(eligible) >= STANDARD_YEARS else 'limited',
        'calendar_mapping': {'method': 'day_offset_from_aligned_start', 'max_month_day_drift_days': drift,
                             'note': 'each source day used once; precipitation totals preserved'},
        'weather_segments': [{'kind': 'climatological_scenario', 'start': target_start.isoformat(), 'end': target_end.isoformat(),
                              'origin': f"{METHOD} over {history.source.get('provider', 'unknown')}"}],
        'et0_method': 'FAO-56 Penman-Monteith daily (data/models/water_balance.et0_daily, dewpoint humidity) on the source day',
    }
    manifest['content_sha256'] = _hash_members(members)
    return Ensemble(tuple(members), manifest)


def compose_with_observed(ensemble: Ensemble, observed: WeatherHistory, *, as_of: datetime) -> Ensemble:
    """Prefix every member with the same observed days, which must end exactly on as_of and be complete."""
    cutoff = as_of.date()
    days = list(observed.days)
    if not days or days[-1].date != cutoff:
        raise SimulationInputError('source_unavailable', 'observed series must end on the as_of date')
    for a, b in zip(days, days[1:]):
        if b.date != a.date + timedelta(days=1):
            raise SimulationInputError('source_unavailable', f'observed series not contiguous after {a.date}')
    first_future = date.fromisoformat(ensemble.manifest['target']['start'])
    if first_future != cutoff + timedelta(days=1):
        raise SimulationInputError('invalid_calendar', 'scenario must start the day after as_of')
    if any(not d.complete() for d in days):
        raise SimulationInputError('source_unavailable', 'observed series has missing values')
    prefix = tuple(_scenario_day(d.date, d, 'observed', _et0(d, observed.latitude, observed.elevation_m)) for d in days)
    members = tuple(replace(m, days=prefix + m.days) for m in ensemble.members)
    seg = [{'kind': 'observed', 'start': days[0].date.isoformat(), 'end': cutoff.isoformat(), 'origin': observed.source.get('product') or observed.source.get('provider', 'observed')}]
    manifest = {**ensemble.manifest, 'observed_source': observed.source, 'weather_segments': seg + ensemble.manifest['weather_segments']}
    manifest['content_sha256'] = _hash_members(members)
    return Ensemble(members, manifest)
