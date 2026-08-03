"""Pydantic request and response contracts for Phase 04A."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pms_case_workflow.models import CaseState
from pms_common.security import Classification, UserRole
from pms_ingestion.models import DocumentMetadata
from pms_retrieval.models import ResponseLanguage
from pydantic import BaseModel, ConfigDict, Field


class EvidenceReferenceRequest(BaseModel):
    reference_type: str = Field(min_length=1, max_length=80)
    reference_id: str = Field(min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=100)


class ArtifactReferenceRequest(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    review_status: str = Field(min_length=1, max_length=80)


class CreateCaseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4000)
    initial_message: str = Field(min_length=1, max_length=20_000)
    unit_id: str = Field(min_length=1, max_length=200)
    classification: Classification = Classification.INTERNAL


class MessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    supersedes_message_id: str | None = None
    evidence: tuple[EvidenceReferenceRequest, ...] = ()
    artifacts: tuple[ArtifactReferenceRequest, ...] = ()


class HandoffRequest(BaseModel):
    assigned_subject: str = Field(min_length=1, max_length=200)
    remarks: str = Field(min_length=1, max_length=4000)


class RemarksRequest(BaseModel):
    remarks: str = Field(min_length=1, max_length=4000)


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    thread_id: str
    title: str
    objective: str
    state: CaseState
    created_by_subject: str
    created_by_role: UserRole
    current_owner_subject: str
    current_owner_role: UserRole
    participant_subjects: tuple[str, ...]
    department_id: str
    unit_id: str
    classification: Classification
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: str
    thread_id: str
    sequence_number: int
    author_subject: str
    author_role: UserRole
    body: str
    supersedes_message_id: str | None
    created_at: datetime


class TransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transition_id: str
    case_id: str
    from_state: CaseState
    to_state: CaseState
    actor_subject: str
    actor_role: UserRole
    assigned_subject: str
    assigned_role: UserRole
    remarks: str
    occurred_at: datetime


class CapsuleSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    current_state: str
    objective: str
    rolling_summary: str
    verified_facts: tuple[str, ...]
    unresolved_issues: tuple[str, ...]
    decisions: tuple[DecisionEntryResponse, ...]
    open_tasks: tuple[TaskEntryResponse, ...]
    evidence: tuple[EvidenceEntryResponse, ...]
    artifact_versions: tuple[ArtifactEntryResponse, ...]
    required_next_action: str
    state_hash: str


class DecisionEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision_id: str
    outcome: str
    rationale: str


class TaskEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    title: str
    status: str


class EvidenceEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reference_type: str
    reference_id: str
    version: str | None


class ArtifactEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: str
    version: int
    review_status: str


class TimelineResponse(BaseModel):
    case: CaseResponse
    messages: tuple[MessageResponse, ...]
    transitions: tuple[TransitionResponse, ...]
    capsules: tuple[CapsuleSummaryResponse, ...]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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

    @classmethod
    def from_metadata(cls, metadata: DocumentMetadata) -> DocumentResponse:
        return cls.model_validate(metadata)


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    duplicate: bool


class HealthResponse(BaseModel):
    status: str
    phase: str = "13"


class MeResponse(BaseModel):
    subject: str
    roles: tuple[UserRole, ...]
    tenant_id: str | None
    department_id: str | None
    unit_id: str | None
    classification: Classification


class PolicyQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    response_language: ResponseLanguage = ResponseLanguage.AUTO
    as_of_date: date | None = None
    include_trace: bool = False


class AuditEventResponse(BaseModel):
    event_id: str
    occurred_at: datetime
    query_category: str
    entity_scope: dict[str, Any]
    source_ids: tuple[str, ...]
    result_status: str
    correlation_id: str
