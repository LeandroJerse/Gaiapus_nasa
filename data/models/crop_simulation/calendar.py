"""Strict dates, recurring month-day rules and crop-plan validation. Nothing is clamped or shifted silently."""
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

from .types import CropPlanItem, SimulationInputError, ValidatedCropItem, ValidatedCropPlan

PARAMETERS_DIR = Path(__file__).with_name('parameters')
MAX_ITEMS = 6
MAX_SPAN_DAYS = 5 * 366
_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_MONTH_DAY = re.compile(r'^(\d{2})-(\d{2})$')


def load_parameter_set(parameter_set_id: str) -> dict:
    if not isinstance(parameter_set_id, str) or not re.fullmatch(r'[a-z0-9-]+', parameter_set_id):
        raise SimulationInputError('INVALID_PARAMETER', 'unknown parameter set')
    path = PARAMETERS_DIR / f'{parameter_set_id}.json'
    if not path.is_file():
        raise SimulationInputError('INVALID_PARAMETER', f'unknown parameter set {parameter_set_id}')
    return json.loads(path.read_text(encoding='utf-8'))


def parse_iso_date(value) -> date:
    if not isinstance(value, str) or not _ISO.match(value):
        raise SimulationInputError('INVALID_DATE', 'dates use YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SimulationInputError('INVALID_DATE', f'{value} is not a calendar date') from error


def resolve_month_day(rule, year: int, *, leap_policy: str = 'reject') -> date | None:
    """`MM-DD` in a given year. 02-29 in a non-leap year: 'reject' (error) or 'exclude' (None, caller drops the member)."""
    m = _MONTH_DAY.match(rule) if isinstance(rule, str) else None
    if not m:
        raise SimulationInputError('INVALID_DATE', 'recurring dates use MM-DD')
    month, day = int(m.group(1)), int(m.group(2))
    if (month, day) == (2, 29) and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        if leap_policy == 'exclude':
            return None
        raise SimulationInputError('INVALID_DATE', f'02-29 does not exist in {year}')
    try:
        return date(year, month, day)
    except ValueError as error:
        raise SimulationInputError('INVALID_DATE', f'{rule} is not a calendar date') from error


def validate_crop_plan(items: list[CropPlanItem]) -> ValidatedCropPlan:
    if not items:
        raise SimulationInputError('INVALID_PARAMETER', 'a plan needs at least one crop')
    if len(items) > MAX_ITEMS:
        raise SimulationInputError('RESOURCE_LIMIT', f'at most {MAX_ITEMS} crops per plan')
    set_ids = {i.parameter_set_id for i in items}
    if len(set_ids) != 1:
        raise SimulationInputError('INVALID_PARAMETER', 'one parameter set per plan')
    params = load_parameter_set(next(iter(set_ids)))
    out = []
    for item in items:
        if not isinstance(item.sowing_date, date):
            raise SimulationInputError('INVALID_DATE', 'sowing_date must be a date')
        crop = params.get('aliases', {}).get(item.crop_id, item.crop_id)
        if crop not in params['crops']:
            reason = params.get('rejected_ids', {}).get(item.crop_id, 'no parameters for this crop in the parameter set')
            raise SimulationInputError('CROP_UNSUPPORTED', f'{item.crop_id}: {reason}')
        y = item.reference_yield_kg_ha
        if y is not None and (isinstance(y, bool) or not isinstance(y, (int, float)) or not math.isfinite(y) or y < 0):
            raise SimulationInputError('INVALID_PARAMETER', 'reference yield must be a finite, non-negative kg/ha')
        cycle = sum(params['crops'][crop]['phase_days'])
        out.append(ValidatedCropItem(crop, item.parameter_set_id, item.sowing_date, item.sowing_date + timedelta(days=cycle - 1), cycle, y))
    out.sort(key=lambda i: i.sowing_date)
    for a, b in zip(out, out[1:]):
        if b.sowing_date <= a.harvest_date:
            raise SimulationInputError('OVERLAPPING_CROPS', f'{b.crop_id} sown {b.sowing_date} before {a.crop_id} harvest {a.harvest_date}')
    if (out[-1].harvest_date - out[0].sowing_date).days + 1 > MAX_SPAN_DAYS:
        raise SimulationInputError('RESOURCE_LIMIT', f'a plan spans at most {MAX_SPAN_DAYS} days')
    return ValidatedCropPlan(tuple(out), out[0].parameter_set_id)
