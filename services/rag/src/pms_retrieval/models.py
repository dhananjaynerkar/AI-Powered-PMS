"""Typed chunk, secure retrieval and grounded-answer contracts."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pms_common.security import Classification
from pms_ingestion.parsing import BoundingBox
from pydantic import BaseModel, ConfigDict, Field


class ChunkKind(StrEnum):
    PARENT = "parent"
    CHILD = "child"


class ChunkCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    page_number: int = Field(ge=1)
    bounding_box: BoundingBox | None


class DocumentChunk(BaseModel):
    """One immutable logical chunk with authorization and citation metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    canonical_document_id: str
    document_version_id: str
    parent_chunk_id: str | None
    chunk_kind: ChunkKind
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    heading_path: tuple[str, ...]
    page_numbers: tuple[int, ...]
    citations: tuple[ChunkCitation, ...]
    section_number: str | None = None
    clause_number: str | None = None
    language_code: str
    languages: tuple[str, ...]
    script_code: str
    translation_group_id: str | None = None
    authoritative_language: str | None = None
    publication_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    document_status: str
    port_id: str | None = None
    department_id: str | None = None
    security_classification: Classification
    review_status: str
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    parser_name: str
    parser_version: str
    chunking_version: str


class ChunkWriteSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    document_version_id: str
    created: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    deactivated: int = Field(ge=0)
    parent_chunks: int = Field(ge=0)
    child_chunks: int = Field(ge=0)


class EmbeddingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    model: str
    revision: str
    embedding_version: str
    dimension: int
    pending_chunk_ids: tuple[str, ...]
    unchanged_chunk_ids: tuple[str, ...]


class EmbeddingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    content_hash: str
    vector: tuple[float, ...]


class EmbeddingWriteSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    created: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    dimension: int


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    parent_chunk_id: str | None
    document_id: str
    document_version_id: str
    document_title: str
    text: str
    page_numbers: tuple[int, ...]
    citations: tuple[ChunkCitation, ...]
    language_code: str
    languages: tuple[str, ...]
    script_code: str
    heading_path: tuple[str, ...]
    section_number: str | None = None
    clause_number: str | None = None
    translation_group_id: str | None = None
    authoritative_language: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    score: float


class ResponseLanguage(StrEnum):
    AUTO = "auto"
    ENGLISH = "en"
    HINDI = "hi"
    MARATHI = "mr"


class QueryUnderstanding(BaseModel):
    """Deterministic query controls; no LLM decides authorization or scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: str = Field(min_length=1, max_length=2000)
    query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of_date: date
    mentioned_dates: tuple[date, ...]
    entity_references: tuple[str, ...]
    document_type: str | None
    response_language: ResponseLanguage
    difficult: bool


class RankedEvidence(BaseModel):
    """One ACL-safe candidate with bounded fusion and reranking scores."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hit: RetrievalHit
    lexical_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    rrf_score: float = Field(ge=0)
    rerank_score: float | None = None


class ContextEvidence(BaseModel):
    """Validated parent context exposed to the local generator as untrusted data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    chunk_id: str
    child_chunk_ids: tuple[str, ...]
    document_id: str
    document_version_id: str
    document_title: str
    text: str = Field(min_length=1)
    supporting_text: str | None = None
    token_count: int = Field(ge=1)
    page_numbers: tuple[int, ...]
    citations: tuple[ChunkCitation, ...]
    heading_path: tuple[str, ...]
    section_number: str | None
    clause_number: str | None
    language_code: str
    authoritative_language: str | None
    effective_from: date | None
    effective_to: date | None
    score: float


class SourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    document_id: str
    document_version_id: str
    document_title: str
    page_numbers: tuple[int, ...]
    section_number: str | None
    clause_number: str | None
    citations: tuple[ChunkCitation, ...]


class RetrievalTrace(BaseModel):
    """Developer-safe trace: hashes, identifiers and timings, never source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_id: str
    query_hash: str
    as_of_date: date
    lexical_candidates: int = Field(ge=0)
    dense_candidates: int = Field(ge=0)
    fused_candidates: int = Field(ge=0)
    reranked_candidates: int = Field(ge=0)
    context_chunks: int = Field(ge=0)
    context_tokens: int = Field(ge=0)
    selected_chunk_ids: tuple[str, ...]
    embedding_model: str
    reranker_model: str
    generation_model: str | None
    fallback_used: bool
    durations_ms: dict[str, float]


class GroundedAnswer(BaseModel):
    """Phase 08 document-answer contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    route: Literal["DOCUMENT"] = "DOCUMENT"
    structured_facts: tuple[dict[str, Any], ...] = ()
    calculations: tuple[dict[str, Any], ...] = ()
    sources: tuple[SourceCitation, ...] = ()
    graph_paths: tuple[dict[str, Any], ...] = ()
    forecast: dict[str, Any] | None = None
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    warnings: tuple[str, ...] = ()
    review_required: bool
    model: str | None = None
    trace: RetrievalTrace | None = None


class IndexCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_id: str
    canonical_document_id: str
    document_version_id: str
    stage: str
    status: str
    last_chunk_ordinal: int
    chunking_version: str
    embedding_model: str | None
    embedding_revision: str | None
    error_code: str | None
    started_by_subject: str
    started_at: datetime
    updated_at: datetime
