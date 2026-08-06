"""Typed contracts for the persistent chat workspace.

These contracts intentionally remain separate from the immutable case-message
contracts.  A personal chat and an in-progress streamed assistant response
have a different lifecycle from a workflow handoff message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pms_common.security import Classification


class ChatType(StrEnum):
    PERSONAL = "PERSONAL"
    SHARED_CASE = "SHARED_CASE"


class ChatStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    CLOSED = "CLOSED"


class ChatAccessMode(StrEnum):
    OWNER = "OWNER"
    WRITE = "WRITE"
    READ = "READ"


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessageStatus(StrEnum):
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatIngestionStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    READY = "READY"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ChatRecord:
    chat_id: str
    owner_subject: str
    title: str
    chat_type: ChatType
    status: ChatStatus
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    owner_admin_id: int | None = None
    case_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatParticipant:
    chat_id: str
    participant_subject: str
    participant_role: str
    access_mode: ChatAccessMode
    added_by_subject: str
    added_at: datetime
    participant_admin_id: int | None = None
    removed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    message_id: str
    chat_id: str
    sequence_number: int
    sender_subject: str
    message_role: ChatMessageRole
    content: str
    message_status: ChatMessageStatus
    created_at: datetime
    sender_admin_id: int | None = None
    model_name: str | None = None
    route: str | None = None
    review_required: bool = False
    completed_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChatCitation:
    citation_id: str
    message_id: str
    source_id: str
    canonical_document_id: str
    page_number: int
    created_at: datetime
    document_version_id: str | None = None
    block_id: str | None = None
    section_number: str | None = None
    clause_number: str | None = None
    bounding_box: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatAttachment:
    attachment_id: str
    chat_id: str
    uploaded_by_subject: str
    original_filename: str
    checksum_sha256: str
    mime_type: str
    size_bytes: int
    ingestion_status: ChatIngestionStatus
    classification: Classification
    created_at: datetime
    uploaded_by_admin_id: int | None = None
    canonical_document_id: str | None = None
    ready_at: datetime | None = None
    failure_reason: str | None = None
    review_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMemory:
    chat_id: str
    summary: str
    last_summarized_sequence: int
    summary_version: int
    updated_at: datetime
