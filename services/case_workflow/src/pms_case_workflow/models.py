"""Typed case, message and transition contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pms_common.security import Classification, UserRole
from pms_context import ContextCapsule


class CaseState(StrEnum):
    DRAFT = "draft"
    SUBMITTED_TO_NO = "submitted_to_no"
    RETURNED_TO_DO = "returned_to_do"
    VERIFIED_BY_NO = "verified_by_no"
    SUBMITTED_TO_HOD = "submitted_to_hod"
    RETURNED_TO_NO = "returned_to_no"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class CreateCase:
    title: str
    objective: str
    initial_message: str
    unit_id: str
    classification: Classification = Classification.INTERNAL


@dataclass(frozen=True, slots=True)
class CaseRecord:
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


@dataclass(frozen=True, slots=True)
class CaseMessage:
    message_id: str
    thread_id: str
    sequence_number: int
    author_subject: str
    author_role: UserRole
    body: str
    supersedes_message_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CaseTransition:
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


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    assigned_subject: str
    remarks: str


@dataclass(frozen=True, slots=True)
class CaseTimeline:
    case: CaseRecord
    messages: tuple[CaseMessage, ...]
    transitions: tuple[CaseTransition, ...]
    capsules: tuple[ContextCapsule, ...]
