"""One conservative daily step of the root-zone store.

Daily order (versioned, DAILY_ORDER): runoff from precipitation → infiltration + net irrigation enter the store →
drainage of the excess above capacity → stress coefficient on the wetted store → ETa limited to Ks·ETc and to the
water present. The residual is computed in float64 before any rounding. Same order as the frozen bucket
(data/models/water_balance.bucket_day), so the two coincide under equivalent configuration.
"""
import math

from .types import SimulationInputError

DAILY_ORDER = ('runoff', 'infiltration+irrigation', 'drainage', 'stress', 'eta')


def _finite(**values):
    for name, v in values.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise SimulationInputError('INVALID_PARAMETER', f'{name} must be a finite number; missing is not zero')


def step_day(*, storage, capacity, precipitation, irrigation, etc, p, runoff_fraction=0.0):
    _finite(storage=storage, capacity=capacity, precipitation=precipitation, irrigation=irrigation, etc=etc, p=p, runoff_fraction=runoff_fraction)
    if capacity <= 0 or not 0 <= storage <= capacity:
        raise SimulationInputError('INVALID_PARAMETER', 'storage must lie within [0, capacity] and capacity > 0')
    if min(precipitation, irrigation, etc) < 0 or not 0 < p < 1 or not 0 <= runoff_fraction <= 1:
        raise SimulationInputError('INVALID_PARAMETER', 'negative flux or parameter outside its domain')
    runoff = precipitation * runoff_fraction
    wet = storage + (precipitation - runoff) + irrigation
    drainage = max(0.0, wet - capacity)
    wet -= drainage
    ks = min(1.0, wet / (capacity * (1 - p)))
    eta = min(wet, ks * etc)
    end = max(0.0, wet - eta)
    residual = storage + precipitation + irrigation - runoff - drainage - eta - end
    return {'storage_start_mm': storage, 'storage_mm': end, 'capacity_mm': capacity, 'runoff_mm': runoff, 'drainage_mm': drainage,
            'etc_mm': etc, 'eta_mm': eta, 'et_deficit_mm': etc - eta, 'stress_coefficient': ks, 'p': p, 'mass_balance_residual_mm': residual}
