"""Deterministic external-state fake for Phase 04A service tests."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from pms_case_workflow.models import (
    CaseMessage,
    CaseRecord,
    CaseState,
    CaseTimeline,
    CaseTransition,
)
from pms_common.security import (
    CLASSIFICATION_RANK,
    AuditEvent,
    AuthorizationContext,
    UserRole,
)
from pms_context import (
    ArtifactReference,
    CaseContextSnapshot,
    ContextCapsule,
    ContextMessage,
    DecisionEntry,
    EvidenceReference,
    TaskEntry,
)


class MemoryBackend:
    """State lives outside service instances, mirroring PostgreSQL ownership."""

    def __init__(self) -> None:
        self.cases: dict[str, CaseRecord] = {}
        self.messages: dict[str, list[CaseMessage]] = defaultdict(list)
        self.transitions: dict[str, list[CaseTransition]] = defaultdict(list)
        self.capsules: dict[str, list[ContextCapsule]] = defaultdict(list)
        self.tasks: dict[str, list[TaskEntry]] = defaultdict(list)
        self.decisions: dict[str, list[DecisionEntry]] = defaultdict(list)
        self.evidence: dict[str, list[EvidenceReference]] = defaultdict(list)
        self.artifacts: dict[str, list[ArtifactReference]] = defaultdict(list)
        self.summaries: dict[str, list[str]] = defaultdict(list)
        self.audits: list[AuditEvent] = []
        self.approval_authority: set[tuple[str, str]] = set()


class MemoryCaseStore:
    """CaseStore fake that applies the same participant/scope boundary."""

    def __init__(self, backend: MemoryBackend, context: AuthorizationContext) -> None:
        self.backend = backend
        self.context = context

    def _visible(self, case: CaseRecord) -> bool:
        if self.context.roles.intersection({UserRole.ADMINISTRATOR, UserRole.AUDITOR}):
            return True
        return (
            self.context.subject in case.participant_subjects
            and self.context.department_id == case.department_id
            and self.context.unit_id == case.unit_id
            and CLASSIFICATION_RANK[self.context.classification]
            >= CLASSIFICATION_RANK[case.classification]
        )

    def create_case(self, case: CaseRecord, initial_message: CaseMessage) -> None:
        self.backend.cases[case.case_id] = case
        self.backend.messages[case.thread_id].append(initial_message)
        self.backend.summaries[case.case_id].append(case.objective)

    def list_cases(self) -> tuple[CaseRecord, ...]:
        return tuple(case for case in self.backend.cases.values() if self._visible(case))

    def get_case(self, case_id: str) -> CaseRecord | None:
        case = self.backend.cases.get(case_id)
        return case if case is not None and self._visible(case) else None

    def append_message(
        self,
        case_id: str,
        *,
        body: str,
        supersedes_message_id: str | None,
    ) -> CaseMessage:
        case = self.get_case(case_id)
        if case is None:
            raise LookupError
        messages = self.backend.messages[case.thread_id]
        message = CaseMessage(
            message_id=str(uuid4()),
            thread_id=case.thread_id,
            sequence_number=len(messages) + 1,
            author_subject=self.context.subject,
            author_role=case.current_owner_role,
            body=body,
            supersedes_message_id=supersedes_message_id,
            created_at=datetime.now(UTC),
        )
        messages.append(message)
        return message

    def add_references(
        self,
        message_id: str,
        references: tuple[EvidenceReference, ...],
    ) -> None:
        self.backend.evidence[message_id].extend(references)

    def add_artifacts(
        self,
        case_id: str,
        message_id: str,
        artifacts: tuple[ArtifactReference, ...],
    ) -> None:
        del message_id
        self.backend.artifacts[case_id].extend(artifacts)

    def update_rolling_summary(self, case_id: str, summary: str) -> None:
        self.backend.summaries[case_id].append(summary)

    def transition(
        self,
        case: CaseRecord,
        *,
        to_state: CaseState,
        actor_role: UserRole,
        assigned_subject: str,
        assigned_role: UserRole,
        remarks: str,
    ) -> CaseRecord:
        now = datetime.now(UTC)
        participants = tuple(dict.fromkeys((*case.participant_subjects, assigned_subject)))
        updated = replace(
            case,
            state=to_state,
            current_owner_subject=assigned_subject,
            current_owner_role=assigned_role,
            participant_subjects=participants,
            updated_at=now,
        )
        self.backend.transitions[case.case_id].append(
            CaseTransition(
                transition_id=str(uuid4()),
                case_id=case.case_id,
                from_state=case.state,
                to_state=to_state,
                actor_subject=self.context.subject,
                actor_role=actor_role,
                assigned_subject=assigned_subject,
                assigned_role=assigned_role,
                remarks=remarks,
                occurred_at=now,
            )
        )
        self.backend.cases[case.case_id] = updated
        return updated

    def create_task(self, case_id: str, title: str, assigned_subject: str) -> None:
        del assigned_subject
        self.backend.tasks[case_id].append(
            TaskEntry(str(uuid4()), title, "open")
        )

    def close_open_tasks(self, case_id: str) -> None:
        self.backend.tasks[case_id] = [
            replace(task, status="completed") if task.status == "open" else task
            for task in self.backend.tasks[case_id]
        ]

    def add_decision(
        self,
        case_id: str,
        *,
        outcome: str,
        rationale: str,
    ) -> None:
        self.backend.decisions[case_id].append(
            DecisionEntry(str(uuid4()), outcome, rationale)
        )

    def context_snapshot(self, case_id: str) -> CaseContextSnapshot:
        case = self.get_case(case_id)
        if case is None:
            raise LookupError
        messages = self.backend.messages[case.thread_id]
        evidence = tuple(
            item
            for message in messages
            for item in self.backend.evidence[message.message_id]
        )
        open_tasks = tuple(
            task for task in self.backend.tasks[case_id] if task.status == "open"
        )
        decisions = tuple(self.backend.decisions[case_id])
        return CaseContextSnapshot(
            case_id=case.case_id,
            thread_id=case.thread_id,
            objective=case.objective,
            current_state=case.state.value,
            current_owner_subject=case.current_owner_subject,
            rolling_summary=self.backend.summaries[case_id][-1],
            verified_facts=tuple(
                decision.rationale
                for decision in decisions
                if decision.outcome == "verified_by_no"
            ),
            unresolved_issues=tuple(task.title for task in open_tasks),
            decisions=decisions,
            tasks=tuple(self.backend.tasks[case_id]),
            evidence=evidence,
            artifacts=tuple(self.backend.artifacts[case_id]),
            recent_messages=tuple(
                ContextMessage(
                    message.message_id,
                    message.sequence_number,
                    message.author_subject,
                    message.body,
                )
                for message in messages
            ),
        )

    def next_capsule_version(self, case_id: str) -> int:
        return len(self.backend.capsules[case_id]) + 1

    def save_capsule(self, capsule: ContextCapsule) -> None:
        self.backend.capsules[capsule.case_id].append(capsule)

    def timeline(self, case_id: str) -> CaseTimeline | None:
        case = self.get_case(case_id)
        if case is None:
            return None
        return CaseTimeline(
            case=case,
            messages=tuple(self.backend.messages[case.thread_id]),
            transitions=tuple(self.backend.transitions[case_id]),
            capsules=tuple(self.backend.capsules[case_id]),
        )

    def has_delegated_authority(self, case_id: str, action: str) -> bool:
        return (self.context.subject, action) in self.backend.approval_authority

    def record_audit(self, event: AuditEvent) -> None:
        self.backend.audits.append(event)
