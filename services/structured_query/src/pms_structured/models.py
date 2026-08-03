"""Typed contracts for deterministic routing and governed SQL results."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QueryRoute(StrEnum):
    STRUCTURED = "STRUCTURED"
    DOCUMENT = "DOCUMENT"
    GRAPH = "GRAPH"
    RULE_CALCULATION = "RULE_CALCULATION"
    FORECAST = "FORECAST"
    HYBRID = "HYBRID"
    CLARIFY = "CLARIFY"
    REFUSE = "REFUSE"


class EntityDomain(StrEnum):
    TENANT = "tenant"
    TENANCY = "tenancy"
    PLOT = "plot"
    AGREEMENT = "agreement"
    BILL = "bill"
    PAYMENT = "payment"
    OUTSTANDING = "outstanding"
    INSPECTION = "inspection"
    LEGAL_CASE = "legal_case"


class StructuredQuery(BaseModel):
    """Frontend-safe query input; raw SQL is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=4000)
    canonical_entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    as_of_date: date | None = None
    limit: int = Field(default=50, ge=1, le=500)


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: QueryRoute
    domain: EntityDomain | None = None
    template_id: str | None = None
    reason_code: str
    needs_clarification: bool = False
    clarification: str | None = None


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_schema: str
    source_table: str
    source_record_id: str
    freshness_at: datetime | None = None


class StructuredRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    values: dict[str, Any]
    provenance: SourceProvenance


class StructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    route: QueryRoute
    template_id: str | None = None
    records: tuple[StructuredRecord, ...] = ()
    confidence: str
    warnings: tuple[str, ...] = ()
    review_required: bool
    correlation_id: str


class SemanticEntityHit(BaseModel):
    """A semantic result may identify a record but cannot supply exact facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: EntityDomain
    source_schema: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    source_table: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    source_record_id: str = Field(min_length=1, max_length=300)


class CatalogTableMatch(BaseModel):
    """One authorized governed-view metadata match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_schema: str
    source_table: str
    business_description: str
    approved_columns: tuple[str, ...]
    score: float


class ConstrainedSelectPlan(BaseModel):
    """Internal typed AST for uncommon analytical queries.

    It is intentionally not part of the HTTP request contract. The compiler
    accepts only catalog-approved identifiers and operators.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    columns: tuple[str, ...] = Field(min_length=1, max_length=40)
    filters: tuple[tuple[str, str, str | int | float | date], ...] = ()
    order_by: tuple[tuple[str, str], ...] = ()
    limit: int = Field(default=100, ge=1, le=500)
