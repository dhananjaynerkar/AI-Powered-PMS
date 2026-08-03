"""Typed contracts for verified graph traversal."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphVerificationStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REJECTED = "rejected"


class GraphNodeType(StrEnum):
    TENANT = "tenant"
    TENANCY = "tenancy"
    PLOT = "plot"
    AGREEMENT = "agreement"
    BILL = "bill"
    PAYMENT = "payment"
    INSPECTION = "inspection"
    BREACH = "breach"
    NOTICE = "notice"
    LEGAL_CASE = "legal_case"
    POLICY = "policy"
    CLAUSE = "clause"


class GraphEdgeType(StrEnum):
    TENANT_HAS_TENANCY = "TENANT_HAS_TENANCY"
    TENANCY_OCCUPIES_PLOT = "TENANCY_OCCUPIES_PLOT"
    TENANCY_GOVERNED_BY_AGREEMENT = "TENANCY_GOVERNED_BY_AGREEMENT"
    TENANCY_HAS_BILL = "TENANCY_HAS_BILL"
    BILL_HAS_PAYMENT = "BILL_HAS_PAYMENT"
    PLOT_HAS_INSPECTION = "PLOT_HAS_INSPECTION"
    INSPECTION_FOUND_BREACH = "INSPECTION_FOUND_BREACH"
    BREACH_TRIGGERED_NOTICE = "BREACH_TRIGGERED_NOTICE"
    NOTICE_ESCALATED_TO_SUIT = "NOTICE_ESCALATED_TO_SUIT"
    POLICY_HAS_CLAUSE = "POLICY_HAS_CLAUSE"
    CIRCULAR_AMENDS_POLICY = "CIRCULAR_AMENDS_POLICY"
    CLAUSE_APPLIES_TO_LEASE_TYPE = "CLAUSE_APPLIES_TO_LEASE_TYPE"


class GraphNodeInput(BaseModel):
    """A source-backed node; candidate status is the only ingest default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=300)
    node_type: GraphNodeType
    canonical_entity_id: str = Field(min_length=1, max_length=300)
    owner_canonical_tenant_id: str | None = None
    source_schema: str = Field(min_length=1, max_length=128)
    source_table: str = Field(min_length=1, max_length=128)
    source_record_id: str = Field(min_length=1, max_length=300)
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_clause: str | None = None
    source_page: int | None = Field(default=None, gt=0)
    valid_from: date | None = None
    valid_to: date | None = None
    security_classification: str = "internal"
    verification_status: GraphVerificationStatus = GraphVerificationStatus.CANDIDATE
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_subject: str = Field(min_length=1, max_length=300)
    reviewed_by_subject: str | None = None


class GraphEdgeInput(BaseModel):
    """A source-backed directed relationship; unreviewed edges stay candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str = Field(min_length=1, max_length=300)
    from_node_id: str = Field(min_length=1, max_length=300)
    to_node_id: str = Field(min_length=1, max_length=300)
    edge_type: GraphEdgeType
    owner_canonical_tenant_id: str | None = None
    source_schema: str = Field(min_length=1, max_length=128)
    source_table: str = Field(min_length=1, max_length=128)
    source_record_id: str = Field(min_length=1, max_length=300)
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_clause: str | None = None
    source_page: int | None = Field(default=None, gt=0)
    valid_from: date | None = None
    valid_to: date | None = None
    security_classification: str = "internal"
    verification_status: GraphVerificationStatus = GraphVerificationStatus.CANDIDATE
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_subject: str = Field(min_length=1, max_length=300)
    reviewed_by_subject: str | None = None


class GraphQuery(BaseModel):
    """Bounded, read-only relationship traversal request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_node_id: str = Field(min_length=1, max_length=300)
    target_node_id: str | None = Field(default=None, max_length=300)
    as_of_date: date | None = None
    max_hops: int = Field(default=4, ge=1, le=8)
    limit: int = Field(default=20, ge=1, le=100)


class GraphEdgeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    edge_type: GraphEdgeType
    from_node_id: str
    to_node_id: str
    source_schema: str
    source_table: str
    source_record_id: str
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_clause: str | None = None
    source_page: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None


class GraphPath(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    depth: int = Field(ge=1, le=8)
    evidence: tuple[GraphEdgeEvidence, ...]


class GraphAnswer(BaseModel):
    """Deterministic GraphRAG answer contract; no generated facts are accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    route: str = "GRAPH"
    graph_paths: tuple[GraphPath, ...] = ()
    confidence: str
    warnings: tuple[str, ...] = ()
    review_required: bool
    correlation_id: str
    generated_at: datetime
