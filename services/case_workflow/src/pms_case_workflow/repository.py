"""Persistence boundary for authoritative PostgreSQL case state."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import uuid4

from pms_common.security import (
    AuditEvent,
    AuthorizationContext,
    Classification,
    UserRole,
    apply_postgres_session_context,
    write_audit_event,
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
from sqlalchemy import Connection, text

from pms_case_workflow.models import (
    CaseMessage,
    CaseRecord,
    CaseState,
    CaseTimeline,
    CaseTransition,
)


class CaseStore(Protocol):
    """Atomic persistence operations required by the workflow service."""

    def create_case(self, case: CaseRecord, initial_message: CaseMessage) -> None: ...

    def list_cases(self) -> tuple[CaseRecord, ...]: ...

    def get_case(self, case_id: str) -> CaseRecord | None: ...

    def append_message(
        self,
        case_id: str,
        *,
        body: str,
        supersedes_message_id: str | None,
    ) -> CaseMessage: ...

    def add_references(
        self,
        message_id: str,
        references: Iterable[EvidenceReference],
    ) -> None: ...

    def add_artifacts(
        self,
        case_id: str,
        message_id: str,
        artifacts: Iterable[ArtifactReference],
    ) -> None: ...

    def update_rolling_summary(self, case_id: str, summary: str) -> None: ...

    def transition(
        self,
        case: CaseRecord,
        *,
        to_state: CaseState,
        actor_role: UserRole,
        assigned_subject: str,
        assigned_role: UserRole,
        remarks: str,
    ) -> CaseRecord: ...

    def create_task(self, case_id: str, title: str, assigned_subject: str) -> None: ...

    def close_open_tasks(self, case_id: str) -> None: ...

    def add_decision(
        self,
        case_id: str,
        *,
        outcome: str,
        rationale: str,
    ) -> None: ...

    def context_snapshot(self, case_id: str) -> CaseContextSnapshot: ...

    def next_capsule_version(self, case_id: str) -> int: ...

    def save_capsule(self, capsule: ContextCapsule) -> None: ...

    def timeline(self, case_id: str) -> CaseTimeline | None: ...

    def has_delegated_authority(self, case_id: str, action: str) -> bool: ...

    def record_audit(self, event: AuditEvent) -> None: ...


def _case_from_mapping(row: dict[str, object]) -> CaseRecord:
    return CaseRecord(
        case_id=str(row["case_id"]),
        thread_id=str(row["thread_id"]),
        title=str(row["title"]),
        objective=str(row["objective"]),
        state=CaseState(str(row["state"])),
        created_by_subject=str(row["created_by_subject"]),
        created_by_role=UserRole(str(row["created_by_role"])),
        current_owner_subject=str(row["current_owner_subject"]),
        current_owner_role=UserRole(str(row["current_owner_role"])),
        participant_subjects=tuple(row["participant_subjects"]),  # type: ignore[arg-type]
        department_id=str(row["department_id"]),
        unit_id=str(row["unit_id"]),
        classification=Classification(str(row["classification"])),
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )


class PostgresCaseStore:
    """Parameterized PostgreSQL adapter operating inside one request transaction."""

    def __init__(self, connection: Connection, context: AuthorizationContext) -> None:
        self._connection = connection
        self._context = context
        apply_postgres_session_context(connection, context)

    def create_case(self, case: CaseRecord, initial_message: CaseMessage) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_record "
                "(case_id, thread_id, title, objective, state, created_by_subject, "
                "created_by_role, current_owner_subject, current_owner_role, "
                "participant_subjects, department_id, unit_id, classification, "
                "created_at, updated_at) VALUES "
                "(:case_id, :thread_id, :title, :objective, :state, :creator, "
                ":creator_role, :owner, :owner_role, ARRAY[:owner], :department, "
                ":unit, :classification, :created_at, :updated_at)"
            ),
            {
                "case_id": case.case_id,
                "thread_id": case.thread_id,
                "title": case.title,
                "objective": case.objective,
                "state": case.state.value,
                "creator": case.created_by_subject,
                "creator_role": case.created_by_role.value,
                "owner": case.current_owner_subject,
                "owner_role": case.current_owner_role.value,
                "department": case.department_id,
                "unit": case.unit_id,
                "classification": case.classification.value,
                "created_at": case.created_at,
                "updated_at": case.updated_at,
            },
        )
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_thread "
                "(thread_id, case_id, next_sequence_number, created_at) "
                "VALUES (:thread_id, :case_id, 2, :created_at)"
            ),
            {
                "thread_id": case.thread_id,
                "case_id": case.case_id,
                "created_at": case.created_at,
            },
        )
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_participant "
                "(case_id, subject, role, joined_at, active) "
                "VALUES (:case_id, :subject, :role, :joined_at, true)"
            ),
            {
                "case_id": case.case_id,
                "subject": case.created_by_subject,
                "role": case.created_by_role.value,
                "joined_at": case.created_at,
            },
        )
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_assignment "
                "(assignment_id, case_id, assigned_subject, assigned_role, "
                "assigned_by_subject, assigned_at, active, reason) VALUES "
                "(:id, :case_id, :subject, :role, :by_subject, :at, true, :reason)"
            ),
            {
                "id": str(uuid4()),
                "case_id": case.case_id,
                "subject": case.current_owner_subject,
                "role": case.current_owner_role.value,
                "by_subject": case.created_by_subject,
                "at": case.created_at,
                "reason": "case created",
            },
        )
        self._insert_message(initial_message)
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_rolling_summary "
                "(case_id, version, summary, updated_at, updated_by_subject) "
                "VALUES (:case_id, 1, :summary, :at, :subject)"
            ),
            {
                "case_id": case.case_id,
                "summary": case.objective,
                "at": case.created_at,
                "subject": case.created_by_subject,
            },
        )

    def list_cases(self) -> tuple[CaseRecord, ...]:
        rows = self._connection.execute(
            text(
                "SELECT case_id, thread_id, title, objective, state, "
                "created_by_subject, created_by_role, current_owner_subject, "
                "current_owner_role, participant_subjects, department_id, unit_id, "
                "classification, created_at, updated_at "
                "FROM pms_chat.case_record ORDER BY updated_at DESC, case_id"
            )
        ).mappings()
        return tuple(_case_from_mapping(dict(row)) for row in rows)

    def get_case(self, case_id: str) -> CaseRecord | None:
        row = self._connection.execute(
            text(
                "SELECT case_id, thread_id, title, objective, state, "
                "created_by_subject, created_by_role, current_owner_subject, "
                "current_owner_role, participant_subjects, department_id, unit_id, "
                "classification, created_at, updated_at "
                "FROM pms_chat.case_record WHERE case_id = :case_id"
            ),
            {"case_id": case_id},
        ).mappings().one_or_none()
        return _case_from_mapping(dict(row)) if row is not None else None

    def append_message(
        self,
        case_id: str,
        *,
        body: str,
        supersedes_message_id: str | None,
    ) -> CaseMessage:
        case = self.get_case(case_id)
        if case is None:
            raise LookupError("case is unavailable")
        sequence = self._connection.execute(
            text(
                "UPDATE pms_chat.case_thread "
                "SET next_sequence_number = next_sequence_number + 1 "
                "WHERE case_id = :case_id "
                "RETURNING thread_id, next_sequence_number - 1 AS sequence_number"
            ),
            {"case_id": case_id},
        ).mappings().one()
        message = CaseMessage(
            message_id=str(uuid4()),
            thread_id=str(sequence["thread_id"]),
            sequence_number=int(sequence["sequence_number"]),
            author_subject=self._context.subject,
            author_role=_workflow_role(self._context),
            body=body,
            supersedes_message_id=supersedes_message_id,
            created_at=datetime.now(UTC),
        )
        self._insert_message(message)
        return message

    def _insert_message(self, message: CaseMessage) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_message "
                "(message_id, thread_id, sequence_number, author_subject, "
                "author_role, body, supersedes_message_id, created_at) VALUES "
                "(:id, :thread, :sequence, :subject, :role, :body, :supersedes, :at)"
            ),
            {
                "id": message.message_id,
                "thread": message.thread_id,
                "sequence": message.sequence_number,
                "subject": message.author_subject,
                "role": message.author_role.value,
                "body": message.body,
                "supersedes": message.supersedes_message_id,
                "at": message.created_at,
            },
        )

    def add_references(
        self,
        message_id: str,
        references: Iterable[EvidenceReference],
    ) -> None:
        for reference in references:
            self._connection.execute(
                text(
                    "INSERT INTO pms_chat.message_reference "
                    "(reference_id, message_id, reference_type, source_id, "
                    "source_version, created_at) VALUES "
                    "(:id, :message, :type, :source, :version, now())"
                ),
                {
                    "id": str(uuid4()),
                    "message": message_id,
                    "type": reference.reference_type,
                    "source": reference.reference_id,
                    "version": reference.version,
                },
            )

    def add_artifacts(
        self,
        case_id: str,
        message_id: str,
        artifacts: Iterable[ArtifactReference],
    ) -> None:
        for artifact in artifacts:
            self._connection.execute(
                text(
                    "INSERT INTO pms_chat.case_artifact_version "
                    "(case_id, artifact_id, version, message_id, review_status, "
                    "created_by_subject, created_at) VALUES "
                    "(:case_id, :artifact, :version, :message, :status, :subject, now())"
                ),
                {
                    "case_id": case_id,
                    "artifact": artifact.artifact_id,
                    "version": artifact.version,
                    "message": message_id,
                    "status": artifact.review_status,
                    "subject": self._context.subject,
                },
            )
            self._connection.execute(
                text(
                    "INSERT INTO pms_chat.message_attachment "
                    "(attachment_id, message_id, artifact_id, artifact_version, "
                    "display_name, created_at) VALUES "
                    "(:id, :message, :artifact, :version, :name, now())"
                ),
                {
                    "id": str(uuid4()),
                    "message": message_id,
                    "artifact": artifact.artifact_id,
                    "version": artifact.version,
                    "name": artifact.artifact_id,
                },
            )

    def update_rolling_summary(self, case_id: str, summary: str) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_rolling_summary "
                "(case_id, version, summary, updated_at, updated_by_subject) "
                "SELECT :case_id, COALESCE(MAX(version), 0) + 1, :summary, now(), "
                ":subject FROM pms_chat.case_rolling_summary WHERE case_id = :case_id"
            ),
            {
                "case_id": case_id,
                "summary": summary,
                "subject": self._context.subject,
            },
        )

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
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_transition "
                "(transition_id, case_id, from_state, to_state, actor_subject, "
                "actor_role, assigned_subject, assigned_role, remarks, occurred_at) "
                "VALUES (:id, :case_id, :from_state, :to_state, :actor, :actor_role, "
                ":assigned, :assigned_role, :remarks, :at)"
            ),
            {
                "id": str(uuid4()),
                "case_id": case.case_id,
                "from_state": case.state.value,
                "to_state": to_state.value,
                "actor": self._context.subject,
                "actor_role": actor_role.value,
                "assigned": assigned_subject,
                "assigned_role": assigned_role.value,
                "remarks": remarks,
                "at": now,
            },
        )
        self._connection.execute(
            text(
                "UPDATE pms_chat.case_assignment SET active = false, ended_at = :at "
                "WHERE case_id = :case_id AND active"
            ),
            {"at": now, "case_id": case.case_id},
        )
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_participant "
                "(case_id, subject, role, joined_at, active) "
                "VALUES (:case_id, :subject, :role, :at, true) "
                "ON CONFLICT (case_id, subject) DO UPDATE SET active = true"
            ),
            {
                "case_id": case.case_id,
                "subject": assigned_subject,
                "role": assigned_role.value,
                "at": now,
            },
        )
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_assignment "
                "(assignment_id, case_id, assigned_subject, assigned_role, "
                "assigned_by_subject, assigned_at, active, reason) VALUES "
                "(:id, :case_id, :subject, :role, :by_subject, :at, true, :reason)"
            ),
            {
                "id": str(uuid4()),
                "case_id": case.case_id,
                "subject": assigned_subject,
                "role": assigned_role.value,
                "by_subject": self._context.subject,
                "at": now,
                "reason": remarks,
            },
        )
        row = self._connection.execute(
            text(
                "UPDATE pms_chat.case_record SET state = :state, "
                "current_owner_subject = :subject, current_owner_role = :role, "
                "participant_subjects = CASE WHEN :subject = ANY(participant_subjects) "
                "THEN participant_subjects ELSE array_append(participant_subjects, :subject) "
                "END, updated_at = :at WHERE case_id = :case_id "
                "RETURNING case_id, thread_id, title, objective, state, "
                "created_by_subject, created_by_role, current_owner_subject, "
                "current_owner_role, participant_subjects, department_id, unit_id, "
                "classification, created_at, updated_at"
            ),
            {
                "state": to_state.value,
                "subject": assigned_subject,
                "role": assigned_role.value,
                "at": now,
                "case_id": case.case_id,
            },
        ).mappings().one()
        return _case_from_mapping(dict(row))

    def create_task(self, case_id: str, title: str, assigned_subject: str) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_task "
                "(task_id, case_id, title, status, assigned_subject, created_by_subject, "
                "created_at) VALUES (:id, :case_id, :title, 'open', :assigned, :creator, now())"
            ),
            {
                "id": str(uuid4()),
                "case_id": case_id,
                "title": title,
                "assigned": assigned_subject,
                "creator": self._context.subject,
            },
        )

    def close_open_tasks(self, case_id: str) -> None:
        self._connection.execute(
            text(
                "UPDATE pms_chat.case_task SET status = 'completed', completed_at = now() "
                "WHERE case_id = :case_id AND status = 'open' "
                "AND assigned_subject = :subject"
            ),
            {"case_id": case_id, "subject": self._context.subject},
        )

    def add_decision(
        self,
        case_id: str,
        *,
        outcome: str,
        rationale: str,
    ) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.case_decision "
                "(decision_id, case_id, outcome, rationale, actor_subject, "
                "actor_role, decided_at) VALUES "
                "(:id, :case_id, :outcome, :rationale, :subject, :role, now())"
            ),
            {
                "id": str(uuid4()),
                "case_id": case_id,
                "outcome": outcome,
                "rationale": rationale,
                "subject": self._context.subject,
                "role": _workflow_role(self._context).value,
            },
        )

    def context_snapshot(self, case_id: str) -> CaseContextSnapshot:
        case = self.get_case(case_id)
        if case is None:
            raise LookupError("case is unavailable")
        summary = self._connection.execute(
            text(
                "SELECT summary FROM pms_chat.case_rolling_summary "
                "WHERE case_id = :case_id ORDER BY version DESC LIMIT 1"
            ),
            {"case_id": case_id},
        ).scalar_one()
        messages = self._connection.execute(
            text(
                "SELECT message_id, sequence_number, author_subject, body "
                "FROM pms_chat.case_message WHERE thread_id = :thread_id "
                "ORDER BY sequence_number DESC LIMIT 100"
            ),
            {"thread_id": case.thread_id},
        ).mappings()
        decisions = self._connection.execute(
            text(
                "SELECT decision_id, outcome, rationale FROM pms_chat.case_decision "
                "WHERE case_id = :case_id ORDER BY decided_at"
            ),
            {"case_id": case_id},
        ).mappings()
        tasks = self._connection.execute(
            text(
                "SELECT task_id, title, status FROM pms_chat.case_task "
                "WHERE case_id = :case_id ORDER BY created_at"
            ),
            {"case_id": case_id},
        ).mappings()
        evidence = self._connection.execute(
            text(
                "SELECT mr.reference_type, mr.source_id, mr.source_version "
                "FROM pms_chat.message_reference mr "
                "JOIN pms_chat.case_message m ON m.message_id = mr.message_id "
                "WHERE m.thread_id = :thread_id ORDER BY m.sequence_number, mr.reference_id"
            ),
            {"thread_id": case.thread_id},
        ).mappings()
        artifacts = self._connection.execute(
            text(
                "SELECT artifact_id, version, review_status "
                "FROM pms_chat.case_artifact_version WHERE case_id = :case_id "
                "ORDER BY artifact_id, version"
            ),
            {"case_id": case_id},
        ).mappings()
        return CaseContextSnapshot(
            case_id=case.case_id,
            thread_id=case.thread_id,
            objective=case.objective,
            current_state=case.state.value,
            current_owner_subject=case.current_owner_subject,
            rolling_summary=str(summary),
            decisions=tuple(
                DecisionEntry(str(row["decision_id"]), str(row["outcome"]), str(row["rationale"]))
                for row in decisions
            ),
            tasks=tuple(
                TaskEntry(str(row["task_id"]), str(row["title"]), str(row["status"]))
                for row in tasks
            ),
            evidence=tuple(
                EvidenceReference(
                    str(row["reference_type"]),
                    str(row["source_id"]),
                    str(row["source_version"]) if row["source_version"] else None,
                )
                for row in evidence
            ),
            artifacts=tuple(
                ArtifactReference(
                    str(row["artifact_id"]),
                    int(row["version"]),
                    str(row["review_status"]),
                )
                for row in artifacts
            ),
            recent_messages=tuple(
                ContextMessage(
                    str(row["message_id"]),
                    int(row["sequence_number"]),
                    str(row["author_subject"]),
                    str(row["body"]),
                )
                for row in messages
            ),
        )

    def next_capsule_version(self, case_id: str) -> int:
        latest = self._connection.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) FROM pms_chat.context_capsule "
                "WHERE case_id = :case_id"
            ),
            {"case_id": case_id},
        ).scalar_one()
        return int(latest) + 1

    def save_capsule(self, capsule: ContextCapsule) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.context_capsule "
                "(case_id, version, thread_id, objective, current_state, "
                "current_owner_subject, rolling_summary, verified_facts, "
                "unresolved_issues, decisions, open_tasks, evidence_ids, "
                "artifact_versions, required_next_action, state_hash, created_at) "
                "VALUES (:case_id, :version, :thread, :objective, :state, :owner, "
                ":summary, CAST(:facts AS jsonb), CAST(:issues AS jsonb), "
                "CAST(:decisions AS jsonb), CAST(:tasks AS jsonb), "
                "CAST(:evidence AS jsonb), CAST(:artifacts AS jsonb), :action, "
                ":hash, :at)"
            ),
            {
                "case_id": capsule.case_id,
                "version": capsule.version,
                "thread": capsule.thread_id,
                "objective": capsule.objective,
                "state": capsule.current_state,
                "owner": capsule.current_owner_subject,
                "summary": capsule.rolling_summary,
                "facts": json.dumps(capsule.verified_facts),
                "issues": json.dumps(capsule.unresolved_issues),
                "decisions": json.dumps([asdict(item) for item in capsule.decisions]),
                "tasks": json.dumps([asdict(item) for item in capsule.open_tasks]),
                "evidence": json.dumps([asdict(item) for item in capsule.evidence]),
                "artifacts": json.dumps(
                    [asdict(item) for item in capsule.artifact_versions]
                ),
                "action": capsule.required_next_action,
                "hash": capsule.state_hash,
                "at": capsule.created_at,
            },
        )

    def timeline(self, case_id: str) -> CaseTimeline | None:
        case = self.get_case(case_id)
        if case is None:
            return None
        messages = self._connection.execute(
            text(
                "SELECT message_id, thread_id, sequence_number, author_subject, "
                "author_role, body, supersedes_message_id, created_at "
                "FROM pms_chat.case_message WHERE thread_id = :thread "
                "ORDER BY sequence_number"
            ),
            {"thread": case.thread_id},
        ).mappings()
        transitions = self._connection.execute(
            text(
                "SELECT transition_id, case_id, from_state, to_state, actor_subject, "
                "actor_role, assigned_subject, assigned_role, remarks, occurred_at "
                "FROM pms_chat.case_transition WHERE case_id = :case_id "
                "ORDER BY occurred_at, transition_id"
            ),
            {"case_id": case_id},
        ).mappings()
        capsules = self._connection.execute(
            text(
                "SELECT case_id, thread_id, version, objective, current_state, "
                "current_owner_subject, rolling_summary, verified_facts, "
                "unresolved_issues, decisions, open_tasks, evidence_ids, "
                "artifact_versions, required_next_action, state_hash, created_at "
                "FROM pms_chat.context_capsule WHERE case_id = :case_id ORDER BY version"
            ),
            {"case_id": case_id},
        ).mappings()
        return CaseTimeline(
            case=case,
            messages=tuple(
                CaseMessage(
                    str(row["message_id"]),
                    str(row["thread_id"]),
                    int(row["sequence_number"]),
                    str(row["author_subject"]),
                    UserRole(str(row["author_role"])),
                    str(row["body"]),
                    str(row["supersedes_message_id"])
                    if row["supersedes_message_id"]
                    else None,
                    row["created_at"],
                )
                for row in messages
            ),
            transitions=tuple(
                CaseTransition(
                    str(row["transition_id"]),
                    str(row["case_id"]),
                    CaseState(str(row["from_state"])),
                    CaseState(str(row["to_state"])),
                    str(row["actor_subject"]),
                    UserRole(str(row["actor_role"])),
                    str(row["assigned_subject"]),
                    UserRole(str(row["assigned_role"])),
                    str(row["remarks"]),
                    row["occurred_at"],
                )
                for row in transitions
            ),
            capsules=tuple(_capsule_from_mapping(dict(row)) for row in capsules),
        )

    def has_delegated_authority(self, case_id: str, action: str) -> bool:
        return bool(
            self._connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pms_chat.delegated_authority da "
                    "JOIN pms_chat.case_record c ON c.department_id = da.department_id "
                    "AND c.unit_id = da.unit_id "
                    "WHERE c.case_id = :case_id AND da.subject = :subject "
                    "AND da.action = :action AND da.active "
                    "AND da.valid_from <= now() "
                    "AND (da.valid_to IS NULL OR da.valid_to > now()))"
                ),
                {
                    "case_id": case_id,
                    "subject": self._context.subject,
                    "action": action,
                },
            ).scalar_one()
        )

    def record_audit(self, event: AuditEvent) -> None:
        write_audit_event(self._connection, event)


def _workflow_role(context: AuthorizationContext) -> UserRole:
    for role in (
        UserRole.DATA_ENTRY_OPERATOR,
        UserRole.NODAL_REGIONAL_OFFICER,
        UserRole.HOD,
        UserRole.ADMINISTRATOR,
    ):
        if role in context.roles:
            return role
    raise PermissionError("workflow role is required")


def _capsule_from_mapping(row: dict[str, object]) -> ContextCapsule:
    verified_facts = cast(list[str], row["verified_facts"])
    unresolved_issues = cast(list[str], row["unresolved_issues"])
    decisions = cast(list[dict[str, str]], row["decisions"])
    open_tasks = cast(list[dict[str, str]], row["open_tasks"])
    evidence = cast(list[dict[str, str | None]], row["evidence_ids"])
    artifacts = cast(list[dict[str, str | int]], row["artifact_versions"])
    return ContextCapsule(
        case_id=str(row["case_id"]),
        thread_id=str(row["thread_id"]),
        version=cast(int, row["version"]),
        objective=str(row["objective"]),
        current_state=str(row["current_state"]),
        current_owner_subject=str(row["current_owner_subject"]),
        rolling_summary=str(row["rolling_summary"]),
        verified_facts=tuple(verified_facts),
        unresolved_issues=tuple(unresolved_issues),
        decisions=tuple(DecisionEntry(**item) for item in decisions),
        open_tasks=tuple(TaskEntry(**item) for item in open_tasks),
        evidence=tuple(
            EvidenceReference(
                reference_type=str(item["reference_type"]),
                reference_id=str(item["reference_id"]),
                version=str(item["version"]) if item.get("version") is not None else None,
            )
            for item in evidence
        ),
        artifact_versions=tuple(
            ArtifactReference(
                artifact_id=str(item["artifact_id"]),
                version=cast(int, item["version"]),
                review_status=str(item["review_status"]),
            )
            for item in artifacts
        ),
        recent_messages=(),
        retrieved_older_messages=(),
        required_next_action=str(row["required_next_action"]),
        state_hash=str(row["state_hash"]),
        created_at=cast(datetime, row["created_at"]),
    )
