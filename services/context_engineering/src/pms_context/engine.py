"""Deterministic, bounded context capsules for role handoffs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final


@dataclass(frozen=True, slots=True)
class ContextMessage:
    """One authorized message selected for working context."""

    message_id: str
    sequence_number: int
    author_subject: str
    body: str


@dataclass(frozen=True, slots=True)
class DecisionEntry:
    """One decision-ledger entry."""

    decision_id: str
    outcome: str
    rationale: str


@dataclass(frozen=True, slots=True)
class TaskEntry:
    """One task-ledger entry."""

    task_id: str
    title: str
    status: str


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A stable reference to exact evidence, never copied source content."""

    reference_type: str
    reference_id: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """A stable reference to one generated artifact version."""

    artifact_id: str
    version: int
    review_status: str


@dataclass(frozen=True, slots=True)
class CaseContextSnapshot:
    """Authorized PostgreSQL state used to create a context capsule."""

    case_id: str
    thread_id: str
    objective: str
    current_state: str
    current_owner_subject: str
    rolling_summary: str
    verified_facts: tuple[str, ...] = ()
    unresolved_issues: tuple[str, ...] = ()
    decisions: tuple[DecisionEntry, ...] = ()
    tasks: tuple[TaskEntry, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    artifacts: tuple[ArtifactReference, ...] = ()
    recent_messages: tuple[ContextMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextCapsule:
    """Versioned handoff state; it supplements but never replaces the transcript."""

    case_id: str
    thread_id: str
    version: int
    objective: str
    current_state: str
    current_owner_subject: str
    rolling_summary: str
    verified_facts: tuple[str, ...]
    unresolved_issues: tuple[str, ...]
    decisions: tuple[DecisionEntry, ...]
    open_tasks: tuple[TaskEntry, ...]
    evidence: tuple[EvidenceReference, ...]
    artifact_versions: tuple[ArtifactReference, ...]
    recent_messages: tuple[ContextMessage, ...]
    retrieved_older_messages: tuple[ContextMessage, ...]
    required_next_action: str
    state_hash: str
    created_at: datetime


OlderMessageRetriever = Callable[
    [str, str, int, frozenset[str]],
    Sequence[ContextMessage],
]


class ContextEngine:
    """Assemble a bounded, deterministic context capsule from authorized state."""

    _required_actions: Final = {
        "draft": "Complete the draft and submit it to the assigned NO.",
        "submitted_to_no": "NO must verify the case or return it with observations.",
        "returned_to_do": "DO must address the observations and resubmit the same case.",
        "verified_by_no": "NO must forward the verified case to the assigned HOD.",
        "submitted_to_hod": "HOD must approve, reject, return, or escalate the case.",
        "returned_to_no": "NO must address HOD observations and resubmit to HOD.",
        "approved": "Record completion evidence and close the case.",
        "rejected": "Record the rejection outcome and close the case.",
        "escalated": "The delegated authority must review the escalation.",
        "closed": "No further workflow action is required.",
    }

    def __init__(
        self,
        *,
        recent_message_window: int = 12,
        retrieved_message_top_k: int = 8,
        older_message_retriever: OlderMessageRetriever | None = None,
    ) -> None:
        if recent_message_window < 1 or retrieved_message_top_k < 1:
            raise ValueError("context message limits must be positive")
        self._recent_message_window = recent_message_window
        self._retrieved_message_top_k = retrieved_message_top_k
        self._older_message_retriever = older_message_retriever

    def create_capsule(
        self,
        snapshot: CaseContextSnapshot,
        *,
        version: int,
    ) -> ContextCapsule:
        """Create one capsule and hash the authoritative state it represents."""

        if version < 1:
            raise ValueError("context capsule version must be positive")
        recent = tuple(
            sorted(snapshot.recent_messages, key=lambda item: item.sequence_number)[
                -self._recent_message_window :
            ]
        )
        recent_ids = frozenset(message.message_id for message in recent)
        older: tuple[ContextMessage, ...] = ()
        if self._older_message_retriever is not None:
            candidates = self._older_message_retriever(
                snapshot.case_id,
                snapshot.objective,
                self._retrieved_message_top_k,
                recent_ids,
            )
            older = tuple(candidates[: self._retrieved_message_top_k])

        open_tasks = tuple(task for task in snapshot.tasks if task.status == "open")
        state_payload = {
            "case_id": snapshot.case_id,
            "thread_id": snapshot.thread_id,
            "objective": snapshot.objective,
            "current_state": snapshot.current_state,
            "current_owner_subject": snapshot.current_owner_subject,
            "rolling_summary": snapshot.rolling_summary,
            "verified_facts": snapshot.verified_facts,
            "unresolved_issues": snapshot.unresolved_issues,
            "decisions": [asdict(item) for item in snapshot.decisions],
            "open_tasks": [asdict(item) for item in open_tasks],
            "evidence": [asdict(item) for item in snapshot.evidence],
            "artifact_versions": [asdict(item) for item in snapshot.artifacts],
            "latest_sequence": recent[-1].sequence_number if recent else 0,
        }
        canonical = json.dumps(
            state_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        return ContextCapsule(
            case_id=snapshot.case_id,
            thread_id=snapshot.thread_id,
            version=version,
            objective=snapshot.objective,
            current_state=snapshot.current_state,
            current_owner_subject=snapshot.current_owner_subject,
            rolling_summary=snapshot.rolling_summary,
            verified_facts=snapshot.verified_facts,
            unresolved_issues=snapshot.unresolved_issues,
            decisions=snapshot.decisions,
            open_tasks=open_tasks,
            evidence=snapshot.evidence,
            artifact_versions=snapshot.artifacts,
            recent_messages=recent,
            retrieved_older_messages=older,
            required_next_action=self._required_actions[snapshot.current_state],
            state_hash=hashlib.sha256(canonical).hexdigest(),
            created_at=datetime.now(UTC),
        )
