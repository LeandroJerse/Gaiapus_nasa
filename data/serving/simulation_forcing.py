"""Forcing for the crop simulation engine: normalise existing NASA POWER payloads, flag quality, enforce the temporal
cutoff and record provenance. Strict mode only: no interpolation, no gap filling, missing is never zero.

Reuses the ingestion format of data/serving/location_weather.py (same POWER parameters and FAO-56 ET0) without
changing it. No downloader lives here: payloads come from the existing fetchers or from archived bundles.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from data.models.crop_simulation.types import ForcingDay, SimulationInputError
from data.models.water_balance import et0_daily

__all__ = ['SimulationInputError']

FILL = -999.0
OPERATIONAL_MODES = {'short_forecast'}
RETROSPECTIVE_MODES = {'historical_replay', 'historical_ensemble', 'sensitivity', 'climate_scenario'}
#: Declared point resolution (degrees) used both for the request point and the cache key.
POINT_DECIMALS = 3
TEMPERATURE_VARS = ('T2M_MIN', 'T2M_MAX', 'T2MDEW')
ET0_VARS = ('T2M_MIN', 'T2M_MAX', 'ALLSKY_SFC_SW_DWN', 'WS2M', 'T2MDEW', 'PS')
UNITS = {'T2M_MIN': 'C', 'T2M_MAX': 'C', 'T2MDEW': 'C', 'ALLSKY_SFC_SW_DWN': 'MJ/m^2/day', 'WS2M': 'm/s', 'PS': 'kPa'}


@dataclass(frozen=True)
class SourceSpec:
    provider: str
    product: str
    variable: str
    version: str
    resolution: str
    temporal_standard: str
    units: str = 'mm/day'


POWER_IMERG = SourceSpec('NASA POWER', 'daily point, community AG', 'IMERG_PRECTOT', 'POWER API v2 (daily)', '0.1° IMERG rain; 0.5°×0.625° MERRA-2 meteorology', 'UTC')
POWER_PRECTOTCORR = SourceSpec('NASA POWER', 'daily point, community AG', 'PRECTOTCORR', 'POWER API v2 (daily)', '0.5°×0.625° MERRA-2 bias-corrected rain', 'UTC')


def equivalent_sources(a: SourceSpec, b: SourceSpec) -> bool:
    """Two series are interchangeable only if provider, product, variable and version are the same."""
    return (a.provider, a.product, a.variable, a.version) == (b.provider, b.product, b.variable, b.version)


@dataclass(frozen=True)
class ForcingRequest:
    location_id: str
    latitude: float
    longitude: float
    start: date
    end: date
    mode: str
    decision_at: datetime | None
    source: SourceSpec


@dataclass(frozen=True)
class ForcingRecord:
    date: date
    precipitation_mm: float | None
    et0_mm: float | None
    tmin_c: float | None
    tmax_c: float | None
    available_at: datetime | None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForcingBundle:
    records: tuple[ForcingRecord, ...]
    manifest: dict


@dataclass(frozen=True)
class QualityReport:
    usable: bool
    reason_code: str | None
    missing_days: list[str] = field(default_factory=list)
    flagged_days: int = 0


def declared_point(req: ForcingRequest) -> tuple[float, float]:
    return round(req.latitude, POINT_DECIMALS), round(req.longitude, POINT_DECIMALS)


def cache_key(req: ForcingRequest) -> str:
    lat, lon = declared_point(req)
    s = req.source
    ident = [s.provider, s.product, s.variable, s.version, s.temporal_standard, f'{lat:.{POINT_DECIMALS}f}', f'{lon:.{POINT_DECIMALS}f}', req.start.isoformat(), req.end.isoformat()]
    return hashlib.sha256('|'.join(ident).encode()).hexdigest()


def _value(v):
    return None if v is None or (isinstance(v, (int, float)) and v <= FILL) else float(v)


def _content_hash(records) -> str:
    rows = [{'date': r.date.isoformat(), 'precipitation_mm': r.precipitation_mm, 'et0_mm': r.et0_mm, 'tmin_c': r.tmin_c, 'tmax_c': r.tmax_c,
             'available_at': r.available_at.isoformat() if r.available_at else None, 'flags': list(r.flags)} for r in records]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_forcing_from_power(payload: dict, req: ForcingRequest, *, retrieved_at: datetime, available_at: Callable[[date], datetime] | None = None) -> ForcingBundle:
    params = payload.get('properties', {}).get('parameter', {})
    units = payload.get('parameters', {})
    rain_var = req.source.variable
    if rain_var not in params:
        raise SimulationInputError('SOURCE_MISMATCH', f'payload has no {rain_var}; another rain product is not equivalent')
    for name, unit in {rain_var: req.source.units, **UNITS}.items():
        if name not in params:
            raise SimulationInputError('FORCING_INCOMPLETE', f'payload has no {name}')
        declared = units.get(name, {}).get('units')
        if declared is not None and declared != unit:
            raise SimulationInputError('SOURCE_MISMATCH', f'{name} in {declared}, expected {unit}')
    keys = sorted(params[rain_var])
    for name in (rain_var, *UNITS):
        if sorted(params[name]) != keys:
            raise SimulationInputError('FORCING_INCOMPLETE', f'{name} does not cover the same days as {rain_var}')
    days = [datetime.strptime(k, '%Y%m%d').date() for k in keys]
    for a, b in zip(days, days[1:]):
        if b != a + timedelta(days=1):
            raise SimulationInputError('FORCING_INCOMPLETE', f'days not contiguous after {a}')
    if not days or days[0] > req.start or days[-1] < req.end:
        raise SimulationInputError('FORCING_INCOMPLETE', f'payload does not cover {req.start}..{req.end}')
    lon, lat, elevation = payload['geometry']['coordinates']
    records = []
    for key, day in zip(keys, days):
        if not req.start <= day <= req.end:
            continue
        flags = []
        rain = _value(params[rain_var][key])
        if rain is None:
            flags.append('precipitation_missing')
        elif not 0 <= rain <= 1000:
            flags.append('precipitation_out_of_range'); rain = None
        v = {name: _value(params[name][key]) for name in ET0_VARS}
        if any(v[t] is not None and not -90 < v[t] < 70 for t in TEMPERATURE_VARS):
            flags.append('temperature_out_of_range'); v = {**v, **{t: None for t in TEMPERATURE_VARS if v[t] is not None and not -90 < v[t] < 70}}
        et0 = None
        if any(x is None for x in v.values()):
            flags.append('et0_inputs_missing')
        elif v['T2M_MIN'] > v['T2M_MAX']:
            flags.append('tmin_above_tmax')
        else:
            et0 = et0_daily(day, lat, elevation, v['T2M_MIN'], v['T2M_MAX'], v['ALLSKY_SFC_SW_DWN'], v['WS2M'], v['PS'], dewpoint=v['T2MDEW'])['et0_mm']
        records.append(ForcingRecord(day, rain, et0, v['T2M_MIN'], v['T2M_MAX'], available_at(day) if available_at else None, tuple(flags)))
    point = declared_point(req)
    manifest = {
        'location_id': req.location_id, 'provider': req.source.provider, 'product': req.source.product, 'variable': req.source.variable,
        'version': req.source.version, 'resolution': req.source.resolution, 'temporal_standard': req.source.temporal_standard,
        'point': {'latitude': point[0], 'longitude': point[1], 'elevation_m': elevation, 'decimals': POINT_DECIMALS},
        'period': {'start': req.start.isoformat(), 'end': req.end.isoformat()},
        'transformation': 'sentinel→null with flag; ET0 FAO-56 Penman-Monteith daily (data/models/water_balance.et0_daily, dewpoint humidity); no interpolation',
        'retrieved_at': retrieved_at.isoformat(), 'availability': 'per_record' if available_at else 'unknown',
        'cache_key': cache_key(req), 'content_sha256': _content_hash(records),
    }
    return ForcingBundle(tuple(records), manifest)


def validate_forcing(bundle: ForcingBundle, mode: str, *, decision_at: datetime | None = None) -> QualityReport:
    if mode not in OPERATIONAL_MODES | RETROSPECTIVE_MODES:
        raise SimulationInputError('MODE_UNSUPPORTED', mode)
    missing = [r.date.isoformat() for r in bundle.records if r.precipitation_mm is None or r.et0_mm is None]
    flagged = sum(1 for r in bundle.records if r.flags)
    if mode in OPERATIONAL_MODES:
        if decision_at is None:
            raise SimulationInputError('INVALID_PARAMETER', 'operational modes need decision_at')
        if any(r.available_at is None or r.available_at > decision_at for r in bundle.records):
            return QualityReport(False, 'SOURCE_NOT_AVAILABLE_AT_CUTOFF', missing, flagged)
    if missing:
        return QualityReport(False, 'FORCING_INCOMPLETE', missing, flagged)
    return QualityReport(True, None, [], flagged)


def cut_at_decision(bundle: ForcingBundle, decision_at: datetime) -> ForcingBundle:
    """Keep only records known to be available at the decision time (unknown availability is dropped)."""
    kept = tuple(r for r in bundle.records if r.available_at is not None and r.available_at <= decision_at)
    return ForcingBundle(kept, {**bundle.manifest, 'cut_at': decision_at.isoformat(), 'content_sha256': _content_hash(kept)})


def to_engine_days(bundle: ForcingBundle) -> list[ForcingDay]:
    return [ForcingDay(r.date, r.precipitation_mm, r.et0_mm) for r in bundle.records]


def save_bundle(bundle: ForcingBundle, path: Path) -> None:
    rows = [{**asdict(r), 'date': r.date.isoformat(), 'available_at': r.available_at.isoformat() if r.available_at else None, 'flags': list(r.flags)} for r in bundle.records]
    path.write_text(json.dumps({'manifest': bundle.manifest, 'records': rows}, sort_keys=True, indent=1), encoding='utf-8')


def load_bundle(path: Path) -> ForcingBundle:
    if not path.is_file():
        raise SimulationInputError('FORCING_INCOMPLETE', f'archived forcing not found: {path.name}; no other place or period is substituted')
    raw = json.loads(path.read_text(encoding='utf-8'))
    records = tuple(ForcingRecord(date.fromisoformat(r['date']), r['precipitation_mm'], r['et0_mm'], r['tmin_c'], r['tmax_c'],
                                  datetime.fromisoformat(r['available_at']) if r['available_at'] else None, tuple(r['flags'])) for r in raw['records'])
    if _content_hash(records) != raw['manifest']['content_sha256']:
        raise SimulationInputError('FORCING_CORRUPT', f'{path.name}: content hash does not match its manifest')
    return ForcingBundle(records, raw['manifest'])
