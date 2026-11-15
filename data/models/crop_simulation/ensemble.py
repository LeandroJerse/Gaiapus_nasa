"""Historical ensembles: the same decision replayed over eligible past seasons, and paired A/B comparisons.

Frequencies over past seasons are historical frequencies, not calibrated probabilities of the future climate.
Percentiles are climate spread of the simulation, not confidence intervals of any statistical model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .calendar import resolve_month_day
from .engine import simulate_daily
from .types import CropPlanItem, ForcingDay, ManagementSpec, SimulationInputError, SoilProfile, SoilState

MAX_MEMBERS = 40
MIN_PAIRED_MEMBERS = 3
REPRESENTATIVE_RULE = 'nearest-to-percentile-crop-deficit-v1'


@dataclass(frozen=True)
class RecurringCrop:
    crop_id: str
    sowing_rule: str  # MM-DD
    year_offset: int  # relative to the member's season year
    reference_yield_kg_ha: float | None = None


@dataclass(frozen=True)
class Scenario:
    """Identified hypothetical change applied to a copy of the forcing; the original series is never altered."""

    rain_multiplier: float = 1.0
    et0_multiplier: float = 1.0

    def __post_init__(self):
        for name in ('rain_multiplier', 'et0_multiplier'):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 3:
                raise ValueError(f'{name} must be a finite number in [0, 3]')


@dataclass(frozen=True)
class EnsembleRequest:
    crops: tuple[RecurringCrop, ...]
    parameter_set_id: str
    soil: SoilProfile
    initial_state: SoilState
    management: ManagementSpec
    scenario: Scenario = field(default_factory=Scenario)
    leap_policy: str = 'reject'


def percentile(values: list[float], q: float) -> float:
    """Linear interpolation between order statistics (same as numpy's default)."""
    s = sorted(values)
    if not s:
        raise SimulationInputError('INSUFFICIENT_COVERAGE', 'no members')
    pos = q * (len(s) - 1); lo = math.floor(pos); hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _summary(values: list[float]) -> dict:
    return {'n': len(values), 'p10': percentile(values, .1), 'p50': percentile(values, .5), 'p90': percentile(values, .9), 'min': min(values), 'max': max(values)}


def member_metrics(rows: list[dict]) -> dict:
    crop = [r for r in rows if r['crop_id'] is not None]
    return {
        'crop_deficit_mm': sum(r['et_deficit_mm'] for r in crop), 'crop_eta_mm': sum(r['eta_mm'] for r in crop), 'crop_etc_mm': sum(r['etc_mm'] for r in crop),
        'stress_days': sum(1 for r in crop if r['stress_coefficient'] < 1), 'reproductive_stress_days': sum(1 for r in crop if r['phase_code'] == 'reproductive' and r['stress_coefficient'] < 1),
        'drainage_mm': sum(r['drainage_mm'] for r in rows), 'irrigation_gross_mm': sum(r['irrigation_gross_mm'] for r in rows), 'storage_end_mm': rows[-1]['storage_mm'],
    }


@dataclass
class EnsembleResult:
    metrics: dict[str, dict[int, float]]
    excluded: dict[int, str] = field(default_factory=dict)
    scenario: dict = field(default_factory=dict)
    trajectories: dict[int, object] = field(default_factory=dict)

    @classmethod
    def from_metrics(cls, metrics: dict[str, dict[int, float]], **kw) -> 'EnsembleResult':
        return cls(metrics=metrics, **kw)

    @property
    def member_ids(self) -> list[int]:
        return sorted(next(iter(self.metrics.values())).keys()) if self.metrics else []

    @property
    def summary(self) -> dict:
        out = {k: _summary([v[i] for i in self.member_ids]) for k, v in self.metrics.items()}
        if 'reproductive_stress_days' in self.metrics:
            out['reproductive_stress_frequency'] = {'members_with_stress': sum(1 for i in self.member_ids if self.metrics['reproductive_stress_days'][i] > 0), 'members': len(self.member_ids)}
        out['frequency_kind'] = 'historical_frequency_not_probability'
        return out

    @property
    def representative(self) -> dict:
        d = self.metrics['crop_deficit_mm']; ids = self.member_ids
        pick = lambda target: min(ids, key=lambda i: (abs(d[i] - target), i))
        return {'rule_id': REPRESENTATIVE_RULE, 'members': {q: pick(percentile([d[i] for i in ids], v)) for q, v in (('p10', .1), ('p50', .5), ('p90', .9))}}


def _apply(forcing: list[ForcingDay], s: Scenario) -> list[ForcingDay]:
    if s.rain_multiplier == 1 and s.et0_multiplier == 1:
        return forcing
    return [ForcingDay(d.date, None if d.precipitation_mm is None else d.precipitation_mm * s.rain_multiplier,
                       None if d.et0_mm is None else d.et0_mm * s.et0_multiplier) for d in forcing]


def run_ensemble(request: EnsembleRequest, members: dict[int, list[ForcingDay]]) -> EnsembleResult:
    if len(members) > MAX_MEMBERS:
        raise SimulationInputError('RESOURCE_LIMIT', f'at most {MAX_MEMBERS} members')
    metrics: dict[str, dict[int, float]] = {}
    excluded: dict[int, str] = {}
    trajectories = {}
    for year in sorted(members):
        try:
            items = []
            for c in request.crops:
                sow = resolve_month_day(c.sowing_rule, year + c.year_offset, leap_policy=request.leap_policy)
                if sow is None:
                    raise SimulationInputError('INVALID_DATE', f'{c.sowing_rule} does not exist in {year + c.year_offset}')
                items.append(CropPlanItem(c.crop_id, request.parameter_set_id, sow, c.reference_yield_kg_ha))
            t = simulate_daily(items, _apply(members[year], request.scenario), request.soil, request.initial_state, request.management)
        except SimulationInputError as error:
            if error.code in ('FORCING_INCOMPLETE', 'INVALID_DATE'):
                excluded[year] = error.code
                continue
            raise
        trajectories[year] = t
        for k, v in member_metrics(t.rows).items():
            metrics.setdefault(k, {})[year] = v
    if not metrics:
        raise SimulationInputError('INSUFFICIENT_COVERAGE', 'no member could be simulated')
    return EnsembleResult(metrics, excluded, {'rain_multiplier': request.scenario.rain_multiplier, 'et0_multiplier': request.scenario.et0_multiplier}, trajectories)


@dataclass
class PairedComparison:
    member_ids: list[int]
    excluded: dict[int, str]
    deltas: dict[str, dict[int, float]]
    summary: dict[str, dict]
    naive_p50_difference: dict[str, float]


def compare_paired(a: EnsembleResult, b: EnsembleResult) -> PairedComparison:
    """B − A per member first, then percentiles of the differences. Members missing on either side are excluded on both."""
    ia, ib = set(a.member_ids), set(b.member_ids)
    common = sorted(ia & ib)
    excluded = {**{i: 'missing_in_b' for i in ia - ib}, **{i: 'missing_in_a' for i in ib - ia}}
    if len(common) < MIN_PAIRED_MEMBERS:
        raise SimulationInputError('INSUFFICIENT_COVERAGE', f'{len(common)} paired members; at least {MIN_PAIRED_MEMBERS} needed')
    keys = [k for k in a.metrics if k in b.metrics]
    deltas = {k: {i: b.metrics[k][i] - a.metrics[k][i] for i in common} for k in keys}
    summary = {k: _summary(list(v.values())) for k, v in deltas.items()}
    naive = {k: percentile([b.metrics[k][i] for i in common], .5) - percentile([a.metrics[k][i] for i in common], .5) for k in keys}
    return PairedComparison(common, dict(sorted(excluded.items())), deltas, summary, naive)
