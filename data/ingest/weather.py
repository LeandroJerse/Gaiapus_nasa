from datetime import date, timedelta
import math
from data.ingest.monthly import monthly_rain_report

_PARAMS = {"IMERG_PRECTOT": "rain_mm", "T2M_MIN": "t_min_c", "T2M_MAX": "t_max_c",
           "ALLSKY_SFC_SW_DWN": "radiation_mj_m2"}
_UNITS = {"IMERG_PRECTOT": {"mm/day": 1.0}, "T2M_MIN": {"C": 1.0}, "T2M_MAX": {"C": 1.0},
          "ALLSKY_SFC_SW_DWN": {"MJ/m^2/day": 1.0, "kWh/m^2/day": 3.6, "kW-hr/m^2/day": 3.6}}


def _finite(value, what):
    # bool is an int subclass, so it must be rejected explicitly
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what}: expected a finite number, got {value!r}")
    return float(value)


def _section(obj, key, what):
    child = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(child, dict):
        raise ValueError(f"{what}: missing or invalid object '{key}'")
    return child


def _parse_day(key):
    if not isinstance(key, str) or len(key) != 8 or not key.isdigit():
        raise ValueError(f"invalid date key {key!r}, expected YYYYMMDD")
    try:
        return date(int(key[:4]), int(key[4:6]), int(key[6:]))
    except ValueError as exc:
        raise ValueError(f"invalid date key {key!r}: {exc}") from None


def normalize_power(payload, start: date, end: date) -> list[dict]:
    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise ValueError("start and end must be dates with start <= end")
    header = _section(payload, "header", "payload")
    meta = _section(payload, "parameters", "payload")
    series = _section(_section(payload, "properties", "payload"), "parameter", "properties")
    if header.get("time_standard") != "UTC":
        raise ValueError(f"time_standard must be UTC, got {header.get('time_standard')!r}")
    fill = _finite(header.get("fill_value"), "header.fill_value")
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    rows = {d: {"day_utc": d.isoformat(), **dict.fromkeys(_PARAMS.values())} for d in days}
    for name, field in _PARAMS.items():
        units = _section(meta, name, "parameters").get("units")
        factor = _UNITS[name].get(units) if isinstance(units, str) else None
        if factor is None:
            raise ValueError(f"{name}: unsupported units {units!r}")
        for key, raw in _section(series, name, "properties.parameter").items():
            day = _parse_day(key)
            if day not in rows:
                raise ValueError(f"{name}: day {day.isoformat()} is outside the requested window")
            if raw is None:
                continue
            value = _finite(raw, f"{name}[{key}]")
            if value != fill:  # fill_value stays null, never coerced to 0
                rows[day][field] = value * factor
    for row in rows.values():
        if row["radiation_mj_m2"] is not None and row["radiation_mj_m2"] < 0:
            raise ValueError(f"{row['day_utc']}: negative radiation")
        if row["rain_mm"] is not None and row["rain_mm"] < 0:
            raise ValueError(f"{row['day_utc']}: negative rain {row['rain_mm']}")
        if None not in (row["t_min_c"], row["t_max_c"]) and row["t_min_c"] > row["t_max_c"]:
            raise ValueError(f"{row['day_utc']}: t_min_c greater than t_max_c")
    return [rows[d] for d in days]


def compare_rain(rows: list[dict], reference: dict[str, float | None], tolerance_mm=.02) -> dict:
    tolerance = _finite(tolerance_mm, "tolerance_mm")
    if tolerance < 0:
        raise ValueError("tolerance_mm must be >= 0")
    if not isinstance(reference, dict):
        raise ValueError("reference must be a dict keyed by ISO day")
    missing_power, missing_reference, mismatched, diffs, seen = [], [], [], [], set()
    for row in rows:
        day = row.get("day_utc") if isinstance(row, dict) else None
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError):
            raise ValueError(f"invalid day_utc in rows: {day!r}") from None
        if day in seen:
            raise ValueError(f"duplicated day in rows: {day}")
        seen.add(day)
        power, ref = row.get("rain_mm"), reference.get(day)
        if power is not None:
            power = _finite(power, f"rows[{day}].rain_mm")
        if ref is not None:
            ref = _finite(ref, f"reference[{day}]")
        if any(value is not None and value < 0 for value in (power, ref)):
            raise ValueError(f"{day}: negative precipitation in comparison")
        if power is None:
            missing_power.append(day)
        if ref is None:
            missing_reference.append(day)
        if power is None or ref is None:
            continue
        diff = power - ref  # POWER minus reference
        diffs.append(diff)
        if abs(diff) > tolerance + 1e-12:
            mismatched.append(day)
    count = len(diffs)
    if not count or missing_power or missing_reference:
        status = "incomplete"
    elif mismatched:
        status = "discrepancy"
    else:
        status = "complete_match"
    return {
        "matched_days": count,
        "missing_power": missing_power,
        "missing_reference": missing_reference,
        "mismatched_days": len(mismatched),
        "mismatched_dates": mismatched,
        "mae_mm": math.fsum(abs(d) for d in diffs) / count if count else None,
        "bias_mm": math.fsum(diffs) / count if count else None,
        "max_abs_mm": max(abs(d) for d in diffs) if count else None,
        "monthly": monthly_rain_report(rows, reference, tolerance),
        "status": status,
    }
