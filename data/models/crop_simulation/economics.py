"""Per-hectare economics with explicit currency and units. Unavailable yield ⇒ revenue and margin unavailable (None).

1 mm of water over 1 ha = 10 m³. Irrigation cost varies by member, so break-even is computed per member.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def mm_to_m3_per_ha(mm: float) -> float:
    return mm * 10.0


def _nonneg(name, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise ValueError(f'{name} must be a finite, non-negative number')


@dataclass(frozen=True)
class EconomicsSpec:
    currency: str
    price_per_unit: float
    price_unit: str  # 'kg' or 'bag'
    bag_kg: float | None
    price_reference: str  # 'user' or an identified external source
    price_date: str
    fixed_cost_per_ha: float
    variable_cost_per_ha: float
    irrigation_cost_per_m3: float

    def __post_init__(self):
        if not isinstance(self.currency, str) or len(self.currency) != 3:
            raise ValueError('currency is an ISO 4217 code')
        if self.price_unit not in ('kg', 'bag'):
            raise ValueError("price_unit is 'kg' or 'bag'")
        if self.price_unit == 'bag':
            if self.bag_kg is None:
                raise ValueError('a bag price needs the declared bag weight in kg')
            _nonneg('bag_kg', self.bag_kg)
            if self.bag_kg == 0:
                raise ValueError('bag_kg must be positive')
        for name in ('price_per_unit', 'fixed_cost_per_ha', 'variable_cost_per_ha', 'irrigation_cost_per_m3'):
            _nonneg(name, getattr(self, name))
        if not self.price_reference or not self.price_date:
            raise ValueError('price reference and date are required')

    @property
    def price_per_kg(self) -> float:
        return self.price_per_unit / self.bag_kg if self.price_unit == 'bag' else self.price_per_unit


@dataclass(frozen=True)
class EconomicResult:
    currency: str
    irrigation_m3_per_ha: float
    irrigation_cost_per_ha: float
    total_cost_per_ha: float
    revenue_per_ha: float | None
    margin_per_ha: float | None
    break_even_kg_ha: float | None


def compute_economics(yield_kg_ha: float | None, irrigation_gross_mm: float, spec: EconomicsSpec) -> EconomicResult:
    _nonneg('irrigation_gross_mm', irrigation_gross_mm)
    if yield_kg_ha is not None:
        _nonneg('yield_kg_ha', yield_kg_ha)
    m3 = mm_to_m3_per_ha(irrigation_gross_mm)
    irr = m3 * spec.irrigation_cost_per_m3
    cost = spec.fixed_cost_per_ha + spec.variable_cost_per_ha + irr
    revenue = None if yield_kg_ha is None else yield_kg_ha * spec.price_per_kg
    margin = None if revenue is None else revenue - cost
    break_even = cost / spec.price_per_kg if spec.price_per_kg > 0 else None
    return EconomicResult(spec.currency, m3, irr, cost, revenue, margin, break_even)


def ensemble_economics(members: dict[int, tuple[float | None, float]], spec: EconomicsSpec) -> dict:
    """members: {season: (yield_kg_ha or None, irrigation_gross_mm)}."""
    rows = {k: compute_economics(y, irr, spec) for k, (y, irr) in sorted(members.items())}
    with_yield = [r for r in rows.values() if r.margin_per_ha is not None]
    return {'members': rows, 'currency': spec.currency, 'price_reference': spec.price_reference, 'price_date': spec.price_date,
            'negative_margin_frequency': {'members_with_yield': len(with_yield), 'negative': sum(1 for r in with_yield if r.margin_per_ha < 0)}}
