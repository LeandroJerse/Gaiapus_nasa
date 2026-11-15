"""Forcing transformations for the Field Shift sensitivity tests.

A sensitivity test rescales one variable of the historical daily sequence and keeps everything else as observed.
It is not a climate forecast. The generic `water_balance.simulate(rain_multiplier=...)` only accepts 0-1 and removes
rain inside one crop phase (drought by phase); it is left untouched and is not used here.
"""
import math

RAIN_MULTIPLIERS = (0.8, 1.0, 1.2)
MAX_RAIN_MULTIPLIER = 2.0


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def transform_rain(weather, multiplier):
    """Copy of `weather` ({day: row}) with every daily rain multiplied; same days, same other variables."""
    if not _number(multiplier) or not 0 <= multiplier <= MAX_RAIN_MULTIPLIER:
        raise ValueError(f'Rain multiplier must be a finite number between 0 and {MAX_RAIN_MULTIPLIER}')
    out = {}
    for day, row in weather.items():
        rain = row.get('rain_mm')
        if not _number(rain) or rain < 0:
            raise ValueError(f'Rain of {day} is missing, not finite or negative; missing is not zero')
        out[day] = {**row, 'rain_mm': rain * multiplier}
    return out
