"""Typed Phase 11 feature, target, evaluation, and prediction contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TargetDefinition(BaseModel):
    """A business-owned prediction target with an unambiguous grain and unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_name: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=500)
    unit: str = Field(min_length=1, max_length=40)
    frequency: str = Field(min_length=1, max_length=40)
    entity_level: str = Field(min_length=1, max_length=200)
    business_owner: str = Field(min_length=1, max_length=100)
    definition_status: Literal["approved", "blocked"]
    blocked_reason: str | None = Field(default=None, min_length=10, max_length=500)

    @model_validator(mode="after")
    def reject_vague_or_inconsistent_target(self) -> TargetDefinition:
        if self.target_name.lower() in {"revenue", "price", "value", "risk"}:
            raise ValueError("target_name is too vague")
        if (self.definition_status == "blocked") != (self.blocked_reason is not None):
            raise ValueError("blocked targets require one reason; approved targets forbid it")
        return self


class FeatureRow(BaseModel):
    """One immutable point-in-time feature row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_date: date
    target_name: str
    entity_id: str
    entity_ids: dict[str, str]
    source_record_ids: dict[str, str]
    target_value: Decimal
    features: dict[str, int | float | str]
    feature_generation_version: str
    data_cutoff: datetime
    leakage_safe_status: Literal["safe", "blocked"]
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def enforce_point_in_time_boundary(self) -> FeatureRow:
        if self.observation_date >= self.data_cutoff.date():
            raise ValueError("observation_date must be before data_cutoff")
        return self


class ForecastMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mae: float = Field(ge=0)
    rmse: float = Field(ge=0)
    wape: float = Field(ge=0)
    mase: float = Field(ge=0)
    bias: float
    coverage_80: float = Field(ge=0, le=1)
    coverage_95: float = Field(ge=0, le=1)
    observations: int = Field(gt=0)
    folds: int = Field(gt=0)


class ForecastPrediction(BaseModel):
    """A versioned prediction with nested uncertainty intervals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    forecast_date: date
    forecast_horizon: int = Field(gt=0)
    point_estimate: Decimal
    lower_80: Decimal
    upper_80: Decimal
    lower_95: Decimal
    upper_95: Decimal
    model_version_id: str
    feature_snapshot_id: str

    @model_validator(mode="after")
    def validate_intervals(self) -> ForecastPrediction:
        if not (
            self.lower_95
            <= self.lower_80
            <= self.point_estimate
            <= self.upper_80
            <= self.upper_95
        ):
            raise ValueError("prediction intervals must be nested around the estimate")
        return self
