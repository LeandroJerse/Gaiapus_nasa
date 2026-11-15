"""Typed inputs/outputs of the crop simulation engine v1 (ARQUITETURA.md §4 and §6).

Units: water fluxes and states in mm over unit area; depths in m. Missing values are None, never zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class SimulationInputError(ValueError):
    """Structured, user-actionable error. `code` is a stable identifier translated by the product."""

    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CropPlanItem:
    crop_id: str
    parameter_set_id: str
    sowing_date: date
    reference_yield_kg_ha: float | None = None


@dataclass(frozen=True)
class ValidatedCropItem:
    crop_id: str
    parameter_set_id: str
    sowing_date: date
    harvest_date: date
    cycle_days: int
    reference_yield_kg_ha: float | None


@dataclass(frozen=True)
class ValidatedCropPlan:
    items: tuple[ValidatedCropItem, ...]
    parameter_set_id: str

    @property
    def start(self) -> date:
        return self.items[0].sowing_date

    @property
    def end(self) -> date:
        return self.items[-1].harvest_date


@dataclass(frozen=True)
class ForcingDay:
    date: date
    precipitation_mm: float | None
    et0_mm: float | None


@dataclass(frozen=True)
class SoilProfile:
    """v1: one fixed root-zone store. Capacity never changes with the crop."""

    capacity_mm: float
    profile_id: str


@dataclass(frozen=True)
class SoilState:
    storage_mm: float


@dataclass(frozen=True)
class ManagementSpec:
    irrigation_net_mm: dict[date, float] = field(default_factory=dict)
    irrigation_efficiency: float = 1.0
    runoff_fraction: float = 0.0


@dataclass(frozen=True)
class SimulationTrajectory:
    rows: list[dict]
    engine_version: str
    parameter_set_id: str
    soil_profile_id: str
    daily_order: tuple[str, ...]
    evidence_status: str
    totals: dict
