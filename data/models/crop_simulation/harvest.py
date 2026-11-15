"""Harvest outcome of a management: where the yield comes from, production, revenue and margin per harvest.

The water engine does not estimate yield. A yield is taken from an identified origin, in this order of preference:
a compatible model, an experimental model, a local historical reference, a hypothesis informed by the grower.
Yield and budget can be given per alternative and harvest; otherwise the common ones apply. An effect of the
management on the harvest is stated only when a responsive source was estimated for the exact configuration that was
simulated (its digest), never because a flag says so. A reference cannot differ between managements.

Missing is never zero: without a yield there is no revenue, without a price there is no revenue, without costs there
is no margin; known costs are kept when the yield is missing. A harvest without inputs is listed with its reasons,
and the rotation total is unavailable when any harvest is. Money of different currencies is never added or compared
without a conversion that states rate, date and source. Money is computed in Decimal (inputs read from their decimal
text) and serialised with two decimals, half-even; physical quantities stay in float.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal

from .comparison import ComparisonResult, summarise
from .ensemble import percentile

ORIGINS = ('model', 'experimental_model', 'historical_reference', 'user_hypothesis', 'unavailable')
RESPONSIVE_ORIGINS = ('model', 'experimental_model')
PRICE_UNITS = {'kg': 1.0, 't': 1000.0, 'bag': None, 'bushel': None}  # None: the weight must be declared, never assumed
M3_PER_HA_PER_MM = Decimal(10)
MONEY_KEYS = ('irrigation_cost_per_ha', 'fixed_cost_per_ha', 'cost_per_ha', 'revenue_per_ha', 'margin_per_ha', 'break_even_kg_ha')


def _number(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _nonneg(name, v, optional=False):
    if v is None and optional:
        return
    if not _number(v) or v < 0:
        raise ValueError(f'{name} must be a finite, non-negative number')


def _iso(code) -> bool:
    return isinstance(code, str) and len(code) == 3 and code.isalpha() and code.isupper()


def D(value) -> Decimal:
    """Decimal of the decimal text of a number (0.1 is one tenth, not its binary neighbour)."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _f(value):
    return None if value is None else float(value)


def money(value) -> str | None:
    return None if value is None else str(D(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class YieldSource:
    origin: str
    crop_id: str
    kg_ha: float | None
    reference: str            # model id, statistical series or "informed by the grower"
    validation_status: str
    per_member: dict[int, float | None] | None = None
    responds_to_management: bool = False
    scale: str = 'not_stated'
    period: str | None = None
    information_cutoff: str | None = None
    unusable_reason: str | None = None
    fallback_reasons: tuple[str, ...] = ()
    estimated_for_config_sha256: str | None = None  # the simulated configuration a responsive estimate was made for

    def __post_init__(self):
        if self.origin not in ORIGINS:
            raise ValueError(f'yield origin must be one of {ORIGINS}')
        _nonneg('kg_ha', self.kg_ha, optional=True)
        for v in (self.per_member or {}).values():
            _nonneg('per-member yield', v, optional=True)
        if self.responds_to_management and self.origin not in RESPONSIVE_ORIGINS:
            raise ValueError('a reference or a hypothesis does not respond to management')
        if not self.reference or not self.validation_status:
            raise ValueError('a yield needs its reference and its validation status')

    @property
    def usable(self) -> bool:
        return self.origin != 'unavailable' and self.unusable_reason is None and (self.kg_ha is not None or any(v is not None for v in (self.per_member or {}).values()))


def resolve_yield(crop_id: str, candidates: list[YieldSource]) -> YieldSource:
    """First usable candidate in the order of preference; the reasons the earlier ones were not used are kept."""
    if any(c.crop_id != crop_id for c in candidates):
        raise ValueError('a yield of another crop cannot be used')
    reasons = []
    for c in sorted(candidates, key=lambda c: ORIGINS.index(c.origin)):
        if c.usable:
            return replace(c, fallback_reasons=tuple(reasons))
        reasons.append(f'{c.origin}: {c.unusable_reason or "no value"}')
    return YieldSource(origin='unavailable', crop_id=crop_id, kg_ha=None, reference='none', validation_status='unavailable', fallback_reasons=tuple(reasons))


@dataclass(frozen=True)
class HarvestBudget:
    currency: str                      # ISO 4217; never changed by the language of the interface
    price_per_unit: float | None       # None: not informed
    price_unit: str                    # kg, t, bag or bushel
    unit_kg: float | None              # declared weight of the bag or bushel
    price_reference: str
    price_date: str
    cost_per_ha: float | None          # costs that do not depend on the harvested quantity; None: not informed
    cost_basis: str                    # what the cost includes, in the words of its source
    irrigation_cost_per_m3: float = 0.0
    cost_per_kg: float = 0.0           # costs that follow the quantity (freight, drying)
    costs_complete: bool = True

    def __post_init__(self):
        if not _iso(self.currency):
            raise ValueError('currency is an ISO 4217 code')
        if self.price_unit not in PRICE_UNITS:
            raise ValueError(f'price unit must be one of {tuple(PRICE_UNITS)}')
        declared = PRICE_UNITS[self.price_unit] is None
        if declared and (not _number(self.unit_kg) or self.unit_kg <= 0):
            raise ValueError(f'a price per {self.price_unit} needs its declared weight in kg')
        if not declared and self.unit_kg is not None:
            raise ValueError(f'a price per {self.price_unit} takes no declared weight')
        _nonneg('price_per_unit', self.price_per_unit, optional=True)
        _nonneg('cost_per_ha', self.cost_per_ha, optional=True)
        _nonneg('irrigation_cost_per_m3', self.irrigation_cost_per_m3)
        _nonneg('cost_per_kg', self.cost_per_kg)
        if not self.price_reference or not self.price_date or not self.cost_basis:
            raise ValueError('price reference, price date and cost basis are required')

    @property
    def price_per_kg_exact(self) -> Decimal | None:
        if self.price_per_unit is None:
            return None
        return D(self.price_per_unit) / D(PRICE_UNITS[self.price_unit] or self.unit_kg)

    @property
    def price_per_kg(self) -> float | None:
        return _f(self.price_per_kg_exact)


@dataclass(frozen=True)
class CurrencyConversion:
    """One unit of `from_currency` is `rate` units of `to_currency`, as stated by `source` on `date`."""

    from_currency: str
    to_currency: str
    rate: float
    date: str
    source: str

    def __post_init__(self):
        if not _iso(self.from_currency) or not _iso(self.to_currency) or self.from_currency == self.to_currency:
            raise ValueError('a conversion needs two different ISO 4217 codes')
        if not _number(self.rate) or self.rate <= 0:
            raise ValueError('a conversion rate is a finite, positive number')
        if not self.date or not self.source:
            raise ValueError('a conversion needs its date and its source')


def _line(yield_kg_ha: float | None, irrigation_gross_mm: float, b: HarvestBudget) -> dict:
    """Per-hectare money of one member (or of the single value), in Decimal."""
    _nonneg('irrigation_gross_mm', irrigation_gross_mm)
    price, per_kg = b.price_per_kg_exact, D(b.cost_per_kg)
    y = None if yield_kg_ha is None else D(yield_kg_ha)
    irrigation = D(irrigation_gross_mm) * M3_PER_HA_PER_MM * D(b.irrigation_cost_per_m3)
    fixed = None if b.cost_per_ha is None else D(b.cost_per_ha) + irrigation
    # the part of the cost that follows the quantity is unknown without a yield; with no such part the cost is still known
    cost = None if fixed is None else fixed if per_kg == 0 else None if y is None else fixed + per_kg * y
    revenue = None if y is None or price is None else y * price
    margin = None if revenue is None or cost is None else revenue - cost
    # revenue = cost(Y): Y * price = fixed + cost_per_kg * Y
    break_even = None if fixed is None or price is None or price - per_kg <= 0 else fixed / (price - per_kg)
    return {'irrigation_cost_per_ha': irrigation, 'fixed_cost_per_ha': fixed, 'cost_per_ha': cost, 'revenue_per_ha': revenue, 'margin_per_ha': margin, 'break_even_kg_ha': break_even}


def _unavailable(source: YieldSource, b: HarvestBudget, area_ha) -> list[str]:
    out = []
    if not source.usable:
        out.append('yield_unavailable')
    if b.price_per_unit is None:
        out.append('price_missing')
    if b.cost_per_ha is None:
        out.append('cost_missing')
    if area_ha is None:
        out.append('area_missing')
    return out


def _yield_block(source: YieldSource, mean, stats=None, members=None, effect='not_estimated') -> dict:
    stats = stats or {}
    return {'origin': source.origin, 'reference': source.reference, 'validation_status': source.validation_status, 'scale': source.scale, 'period': source.period,
            'information_cutoff': source.information_cutoff, 'fallback_reasons': list(source.fallback_reasons),
            'mean_kg_ha': mean, 'p50_kg_ha': stats.get('p50'), 'p10_kg_ha': stats.get('p10'), 'p90_kg_ha': stats.get('p90'),
            'weights': 'equal' if stats else None, 'members': members,
            'dispersion_kind': 'climate_members_not_a_predictive_interval' if stats else 'none_single_reference' if mean is not None else 'none',
            'management_effect': effect}


def _totals(line: dict, mean_yield, area_ha, b: HarvestBudget) -> dict:
    area = None if area_ha is None else D(area_ha)
    exact = dict(line)
    exact['total_revenue'] = None if area is None or line['revenue_per_ha'] is None else line['revenue_per_ha'] * area
    exact['total_margin'] = None if area is None or line['margin_per_ha'] is None else line['margin_per_ha'] * area
    out = {k: _f(v) for k, v in exact.items()}
    out.update({'price_per_kg': b.price_per_kg, 'production_kg': None if mean_yield is None or area_ha is None else mean_yield * area_ha,
                'margin_kind': 'margin_over_declared_costs' if b.costs_complete else 'partial_budget_not_net_margin',
                'price': {'currency': b.currency, 'per_unit': b.price_per_unit, 'unit': b.price_unit, 'unit_kg': b.unit_kg, 'reference': b.price_reference, 'date': b.price_date},
                'cost_basis': b.cost_basis, 'exact': exact,
                'money': {'currency': b.currency, **{k: money(exact[k]) for k in ('revenue_per_ha', 'margin_per_ha', 'cost_per_ha', 'total_revenue', 'total_margin')}}})
    return out


def _area(area_ha):
    if area_ha is not None and (not _number(area_ha) or area_ha <= 0):
        raise ValueError('area must be a finite, positive number of hectares or absent')


def outcome_single(source: YieldSource, b: HarvestBudget, *, area_ha: float | None, irrigation_gross_mm: float) -> dict:
    """Outcome from one yield value and one irrigation amount (no members)."""
    _area(area_ha)
    y = source.kg_ha if source.usable else None
    return {**_totals(_line(y, irrigation_gross_mm, b), y, area_ha, b), 'yield': _yield_block(source, y), 'unavailable': _unavailable(source, b, area_ha)}


def _mean(values: list[Decimal]):
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def outcome_members(source: YieldSource, b: HarvestBudget, *, area_ha: float | None, irrigation_gross_mm: dict[int, float], management_effect: str = 'not_estimated') -> dict:
    """Outcome over climate members: yield per member when the source has it, else the single value for every member.

    Costs are summarised over every member whose cost is known, revenue and margin over the members that have them:
    the two coverages are reported separately.
    """
    _area(area_ha)
    ids = sorted(irrigation_gross_mm)
    per_member = source.usable and source.per_member is not None
    yields = {i: (source.per_member.get(i) if per_member else source.kg_ha if source.usable else None) for i in ids}
    lines = {i: _line(yields[i], irrigation_gross_mm[i], b) for i in ids}
    values = [yields[i] for i in ids if yields[i] is not None]
    mean_yield = sum(values) / len(values) if values else None
    stats = {'p10': percentile(values, .1), 'p50': percentile(values, .5), 'p90': percentile(values, .9)} if per_member and values else None
    block = _yield_block(source, mean_yield, stats, {'valid': len(values) if per_member else 0, 'total': len(ids)}, management_effect)
    have = {k: [lines[i][k] for i in ids if lines[i][k] is not None] for k in MONEY_KEYS}
    mean_line = {k: _mean(have[k]) for k in MONEY_KEYS}
    count = lambda n: {'members': n, 'total': len(ids)}
    coverage = {'cost': count(len(have['cost_per_ha'])), 'yield': count(len(values)), 'revenue': count(len(have['revenue_per_ha'])), 'margin': count(len(have['margin_per_ha']))}
    margins = [float(m) for m in have['margin_per_ha']]
    spread = len(set(have['margin_per_ha'])) > 1
    kind = 'none' if not spread else 'climate_members_not_a_predictive_interval' if per_member else 'irrigation_cost_only'
    quant = spread and per_member
    margin = {'mean_per_ha': _f(mean_line['margin_per_ha']), 'dispersion_kind': kind, 'members': len(margins),
              'p10_per_ha': percentile(margins, .1) if quant else None, 'p50_per_ha': percentile(margins, .5) if quant else None, 'p90_per_ha': percentile(margins, .9) if quant else None,
              'min_per_ha': min(margins) if spread else None, 'max_per_ha': max(margins) if spread else None}
    frequency = {'negative': sum(1 for m in margins if m < 0), 'members': len(margins), 'kind': 'frequency_in_simulated_members_not_probability'} if per_member and margins else None
    return {**_totals(mean_line, mean_yield, area_ha, b), 'yield': block, 'margin': margin, 'negative_margin_frequency': frequency, 'coverage': coverage,
            'unavailable': _unavailable(source, b, area_ha), 'member_margin_per_ha': {i: lines[i]['margin_per_ha'] for i in ids}}


def _missing_harvest(reasons: list[str], source: YieldSource | None, ids: list[int]) -> dict:
    none = {'members': 0, 'total': len(ids)}
    return {'unavailable': reasons, 'yield': None if source is None else _yield_block(source, source.kg_ha if source.usable else None),
            'margin': {'mean_per_ha': None, 'dispersion_kind': 'none', 'members': 0}, 'margin_per_ha': None, 'revenue_per_ha': None, 'cost_per_ha': None,
            'negative_margin_frequency': None, 'coverage': {'cost': none, 'yield': none, 'revenue': none, 'margin': none}, 'money': None,
            'member_margin_per_ha': {i: None for i in ids}}


def _effect(source: YieldSource, scope: str, run, common: YieldSource | None) -> str:
    """What may be said about the effect of the management on this harvest; raises on a claim the inputs do not support."""
    if source.responds_to_management:
        if scope != 'alternative' or source.estimated_for_config_sha256 != run.config_sha256:
            raise ValueError('a yield that responds to management must be given for one alternative and estimated for its simulated configuration')
        return 'estimated_by_source_for_this_configuration'
    if scope == 'alternative' and source.origin == 'user_hypothesis':
        return 'grower_hypothesis_for_this_management'
    if scope == 'alternative' and (common is None or (source.kg_ha, source.reference, source.per_member) != (common.kg_ha, common.reference, common.per_member)):
        raise ValueError('a reference yield cannot differ between managements; use a grower hypothesis or an estimate made for the configuration')
    return 'not_estimated'


def _converter(report_currency: str | None, conversions) -> tuple:
    table = {(c.from_currency, c.to_currency): c for c in conversions}

    def convert(currency: str):
        """(rate as Decimal or None, conversion used or None) to express `currency` in the report currency."""
        if report_currency is None or currency == report_currency:
            return Decimal(1), None
        c = table.get((currency, report_currency))
        return (None, None) if c is None else (D(c.rate), c)
    return convert


def compare_harvests(result: ComparisonResult, sources: dict[str, YieldSource], budgets: dict[str, HarvestBudget], *, area_ha: float | None,
                     sources_by_alternative: dict[str, dict[str, YieldSource]] | None = None, budgets_by_alternative: dict[str, dict[str, HarvestBudget]] | None = None,
                     report_currency: str | None = None, conversions: tuple[CurrencyConversion, ...] = ()) -> dict:
    """Harvest outcomes of every alternative of a comparison and the paired money differences against the reference.

    `sources` and `budgets` are common, by crop; the `*_by_alternative` ones replace them for one alternative and
    harvest. Every harvest of every alternative is listed, with reasons when it has no outcome. No alternative is
    ranked: a ranking needs an objective and compatible bases, decided by the caller.
    """
    sources_by_alternative, budgets_by_alternative = sources_by_alternative or {}, budgets_by_alternative or {}
    for name in (*sources_by_alternative, *budgets_by_alternative):
        if name not in result.runs:
            raise ValueError(f'inputs given for {name}, which is not an alternative of this comparison')
    if report_currency is not None and not _iso(report_currency):
        raise ValueError('report currency is an ISO 4217 code')
    for s in sources.values():
        if s.responds_to_management:
            raise ValueError('a common yield cannot respond to management; give it for the alternative it was estimated for')
    convert, ids = _converter(report_currency, conversions), result.member_ids
    alternatives, currency_of = {}, {}
    for name, run in result.runs.items():
        harvests, missing = {}, {}
        for crop in run.harvests:
            own_s, own_b = sources_by_alternative.get(name, {}).get(crop), budgets_by_alternative.get(name, {}).get(crop)
            source, b = own_s or sources.get(crop), own_b or budgets.get(crop)
            scope = {'budget': 'alternative' if own_b else 'common', 'yield': 'alternative' if own_s else 'common'}
            reasons = [r for r, absent in (('yield_source_missing', source is None), ('budget_missing', b is None)) if absent]
            if reasons:
                out = _missing_harvest(reasons, source, ids)
            else:
                out = outcome_members(source, b, area_ha=area_ha, irrigation_gross_mm=run.metrics[f'{crop}.irrigation_gross_mm'],
                                      management_effect=_effect(source, scope['yield'], run, sources.get(crop)))
                currency_of[(name, crop)] = b.currency
            out['inputs_scope'] = scope
            harvests[crop] = out
            if out['margin']['mean_per_ha'] is None:
                missing[crop] = [r for r in out['unavailable'] if r != 'area_missing']
        with_margin = [c for c in harvests if c not in missing]
        currencies = sorted({currency_of[(name, c)] for c in with_margin})
        target = report_currency or (currencies[0] if len(currencies) == 1 else None)
        rates = {c: convert(currency_of[(name, c)]) for c in with_margin}
        problem = ['mixed_currencies'] if target is None and currencies else ['conversion_missing'] if any(r[0] is None for r in rates.values()) else []
        subtotal = None
        if with_margin and not problem:
            per_member = [sum((harvests[c]['member_margin_per_ha'][y] * rates[c][0] for c in with_margin if harvests[c]['member_margin_per_ha'][y] is not None), Decimal(0)) for y in ids]
            subtotal = _f(_mean(per_member))
        complete = not missing and not problem and bool(harvests)
        used = {id(r[1]): r[1] for r in rates.values() if r[1] is not None}
        rotation = {'margin_per_ha_mean': subtotal if complete else None, 'complete': complete, 'currency': target, 'currencies': currencies,
                    'unavailable': problem or (['harvest_without_margin'] if missing else []), 'harvests': list(harvests),
                    'subtotal': None if complete or subtotal is None else {'harvests': with_margin, 'margin_per_ha_mean': subtotal, 'kind': 'subtotal_of_the_listed_harvests_not_the_rotation'},
                    'conversions': [{'from': c.from_currency, 'to': c.to_currency, 'rate': str(c.rate), 'date': c.date, 'source': c.source} for c in used.values()],
                    'note': 'money is added across harvests; kg/ha of different crops is never added'}
        alternatives[name] = {'alternative_id': name, 'name': run.name, 'config_sha256': run.config_sha256, 'harvests': harvests, 'rotation': rotation,
                              'coverage': {'harvests': len(harvests), 'with_margin': len(with_margin), 'missing': missing}, 'warnings': list(run.warnings)}
    ref = alternatives[result.reference_id]
    differences, not_comparable = {}, {}
    for name, alt in alternatives.items():
        if name == result.reference_id:
            continue
        differences[name], not_comparable[name] = {}, sorted(set(alt['harvests']) ^ set(ref['harvests']))
        for crop in alt['harvests']:
            if crop not in ref['harvests']:
                continue
            a, r = alt['harvests'][crop]['member_margin_per_ha'], ref['harvests'][crop]['member_margin_per_ha']
            pairs = [(a[y], r[y]) for y in ids if a[y] is not None and r[y] is not None]
            ca, cr = currency_of.get((name, crop)), currency_of.get((result.reference_id, crop))
            if not pairs:
                differences[name][crop] = {'margin_per_ha': None, 'reason': 'margin_unavailable'}
                continue
            (ra, _), (rr, _) = convert(ca), convert(cr)
            if ca != cr and (report_currency is None or ra is None or rr is None):
                differences[name][crop] = {'margin_per_ha': None, 'reason': 'currency_mismatch'}
                continue
            ra, rr = (ra, rr) if ca != cr else (Decimal(1), Decimal(1))
            differences[name][crop] = {'margin_per_ha': summarise([float(x * ra - y * rr) for x, y in pairs]), 'currency': report_currency if ca != cr else ca}
    effects = {h['yield']['management_effect'] for alt in alternatives.values() for h in alt['harvests'].values() if h['yield'] is not None}
    message = ('yield_estimated_for_some_managements_by_source' if 'estimated_by_source_for_this_configuration' in effects
               else 'yield_differs_by_grower_hypothesis_not_estimated' if 'grower_hypothesis_for_this_management' in effects
               else 'same_reference_yield_management_effect_on_harvest_not_estimated')
    return {'reference_id': result.reference_id, 'member_ids': ids, 'coverage': result.coverage, 'alternatives': alternatives,
            'differences': differences, 'not_comparable': not_comparable, 'ranking': None, 'yield_message': message,
            'evidence_status': result.evidence_status, 'forcing_sha256': result.forcing_sha256}
