"""Typed contracts for immutable document storage and lineage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pms_common.security import Classification


class ObjectKind(StrEnum):
    """Kinds of immutable objects stored during document processing."""

    ORIGINAL = "original"
    RAW_PARSER = "raw_parser"
    CANONICAL_JSON = "canonical_json"
    DERIVED = "derived"


class DocumentStatus(StrEnum):
    """Approved document lifecycle states through Phase 06."""

    UPLOADED = "uploaded"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    PARSING = "parsing"
    PARSED = "parsed"
    QUALITY_FAILED = "quality_failed"
    REVIEW_REQUIRED = "review_required"
    CANONICALIZED = "canonicalized"
    CHUNK_READY = "chunk_ready"
    INDEXED = "indexed"
    PROVISIONALLY_INDEXED = "provisionally_indexed"
    DEACTIVATED = "deactivated"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ObjectWrite:
    """Object-store result recorded verbatim in PostgreSQL."""

    bucket_name: str
    object_key: str
    object_version: str | None
    etag: str | None


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Immutable object metadata with retention and checksum evidence."""

    object_id: str
    bucket_name: str
    object_key: str
    object_version: str | None
    checksum_sha256: str
    size_bytes: int
    mime_type: str
    object_kind: ObjectKind
    retention_mode: str
    retention_until: datetime | None
    created_by_subject: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """Authorized document metadata returned without exposing object keys."""

    canonical_document_id: str
    version_id: str
    version_number: int
    title: str
    original_filename: str
    status: str
    checksum_sha256: str
    size_bytes: int
    mime_type: str
    classification: Classification
    created_by_subject: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentUploadResult:
    """Result of an original upload or an authorized duplicate."""

    document: DocumentMetadata
    duplicate: bool


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    """Authorized original bytes with registry metadata."""

    document: DocumentMetadata
    content: bytes


@dataclass(frozen=True, slots=True)
class RetrievedArtifact:
    """Authorized immutable derived bytes and their storage metadata."""

    document: DocumentMetadata
    artifact: StoredObject
    producer: str
    producer_version: str
    content: bytes
