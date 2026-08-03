"""DO → NO → HOD state machine with maker-checker controls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from pms_common.security import (
    AuthorizationContext,
    UserRole,
    create_audit_event,
)
from pms_context import ArtifactReference, ContextCapsule, ContextEngine, EvidenceReference

from pms_case_workflow.models import (
    CaseMessage,
    CaseRecord,
    CaseState,
    CaseTimeline,
    CreateCase,
)
from pms_case_workflow.repository import CaseStore


class CaseWorkflowError(ValueError):
    """Base error returned without leaking internal database details."""


class CaseNotFound(CaseWorkflowError):
    """Case is absent or hidden by the authorization boundary."""


class CaseAccessDenied(PermissionError):
    """Trusted identity is not allowed to perform a workflow operation."""


class InvalidTransition(CaseWorkflowError):
    """Requested state change is not valid from the current state."""


class CaseWorkflowService:
    """Authoritative workflow rules independent of FastAPI and UI code."""

    _role_for_state: Final = {
        CaseState.DRAFT: UserRole.DATA_ENTRY_OPERATOR,
        CaseState.SUBMITTED_TO_NO: UserRole.NODAL_REGIONAL_OFFICER,
        CaseState.RETURNED_TO_DO: UserRole.DATA_ENTRY_OPERATOR,
        CaseState.VERIFIED_BY_NO: UserRole.NODAL_REGIONAL_OFFICER,
        CaseState.SUBMITTED_TO_HOD: UserRole.HOD,
        CaseState.RETURNED_TO_NO: UserRole.NODAL_REGIONAL_OFFICER,
        CaseState.APPROVED: UserRole.HOD,
        CaseState.REJECTED: UserRole.HOD,
        CaseState.ESCALATED: UserRole.HOD,
        CaseState.CLOSED: UserRole.HOD,
    }

    def __init__(
        self,
        store: CaseStore,
        context: AuthorizationContext,
        *,
        context_engine: ContextEngine | None = None,
    ) -> None:
        self._store = store
        self._context = context
        self._context_engine = context_engine or ContextEngine()

    def create_case(self, command: CreateCase) -> CaseRecord:
        self._require_role(UserRole.DATA_ENTRY_OPERATOR)
        if not self._context.department_id or not self._context.unit_id:
            raise CaseAccessDenied("department and unit scope are required")
        if command.unit_id != self._context.unit_id:
            raise CaseAccessDenied("case unit must match trusted unit scope")
        title = command.title.strip()
        objective = command.objective.strip()
        body = command.initial_message.strip()
        if not title or not objective or not body:
            raise CaseWorkflowError("title, objective and initial message are required")
        now = datetime.now(UTC)
        case = CaseRecord(
            case_id=str(uuid4()),
            thread_id=str(uuid4()),
            title=title,
            objective=objective,
            state=CaseState.DRAFT,
            created_by_subject=self._context.subject,
            created_by_role=UserRole.DATA_ENTRY_OPERATOR,
            current_owner_subject=self._context.subject,
            current_owner_role=UserRole.DATA_ENTRY_OPERATOR,
            participant_subjects=(self._context.subject,),
            department_id=self._context.department_id,
            unit_id=command.unit_id,
            classification=command.classification,
            created_at=now,
            updated_at=now,
        )
        message = CaseMessage(
            message_id=str(uuid4()),
            thread_id=case.thread_id,
            sequence_number=1,
            author_subject=self._context.subject,
            author_role=UserRole.DATA_ENTRY_OPERATOR,
            body=body,
            supersedes_message_id=None,
            created_at=now,
        )
        self._store.create_case(case, message)
        self._audit("CASE_CREATE", case.case_id)
        return case

    def list_cases(self) -> tuple[CaseRecord, ...]:
        cases = self._store.list_cases()
        self._audit("CASE_LIST", "authorized-queue")
        return cases

    def get_case(self, case_id: str) -> CaseRecord:
        case = self._require_case(case_id)
        self._audit("CASE_READ", case_id)
        return case

    def timeline(self, case_id: str) -> CaseTimeline:
        timeline = self._store.timeline(case_id)
        if timeline is None:
            self._audit("CASE_TIMELINE_READ", case_id, result_status="DENIED")
            raise CaseNotFound("case not found")
        self._audit("CASE_TIMELINE_READ", case_id)
        return timeline

    def add_message(
        self,
        case_id: str,
        *,
        body: str,
        supersedes_message_id: str | None = None,
        evidence: tuple[EvidenceReference, ...] = (),
        artifacts: tuple[ArtifactReference, ...] = (),
    ) -> CaseMessage:
        case = self._require_case(case_id)
        self._require_current_owner(case)
        clean_body = body.strip()
        if not clean_body:
            raise CaseWorkflowError("message body is required")
        if supersedes_message_id is not None:
            timeline = self._store.timeline(case_id)
            if timeline is None or not any(
                message.message_id == supersedes_message_id
                for message in timeline.messages
            ):
                raise CaseWorkflowError("superseded message is not in this case")
        message = self._store.append_message(
            case_id,
            body=clean_body,
            supersedes_message_id=supersedes_message_id,
        )
        self._store.add_references(message.message_id, evidence)
        self._store.add_artifacts(case_id, message.message_id, artifacts)
        self._store.update_rolling_summary(
            case_id,
            f"{case.objective} Latest update: {clean_body[:500]}",
        )
        self._audit("CASE_MESSAGE_CREATE", case_id, (message.message_id,))
        return message

    def submit_to_no(
        self,
        case_id: str,
        *,
        assigned_subject: str,
        remarks: str,
    ) -> CaseRecord:
        case = self._require_case(case_id)
        self._require_role(UserRole.DATA_ENTRY_OPERATOR)
        if case.state not in {CaseState.DRAFT, CaseState.RETURNED_TO_DO}:
            raise InvalidTransition("case cannot be submitted to NO from this state")
        if case.state is CaseState.RETURNED_TO_DO:
            self._store.close_open_tasks(case_id)
        return self._handoff(
            case,
            CaseState.SUBMITTED_TO_NO,
            assigned_subject,
            UserRole.NODAL_REGIONAL_OFFICER,
            remarks,
            "CASE_SUBMIT_TO_NO",
        )

    def return_to_do(
        self,
        case_id: str,
        *,
        remarks: str,
    ) -> CaseRecord:
        case = self._require_case(case_id)
        self._require_role(UserRole.NODAL_REGIONAL_OFFICER)
        self._require_state(case, CaseState.SUBMITTED_TO_NO)
        self._store.create_task(case_id, remarks.strip(), case.created_by_subject)
        return self._handoff(
            case,
            CaseState.RETURNED_TO_DO,
            case.created_by_subject,
            UserRole.DATA_ENTRY_OPERATOR,
            remarks,
            "CASE_RETURN_TO_DO",
        )

    def verify(self, case_id: str, *, remarks: str) -> CaseRecord:
        case = self._require_case(case_id)
        self._require_role(UserRole.NODAL_REGIONAL_OFFICER)
        self._require_state(case, CaseState.SUBMITTED_TO_NO)
        if self._context.subject == case.created_by_subject:
            raise CaseAccessDenied("maker cannot verify their own case")
        self._store.add_decision(
            case_id,
            outcome="verified_by_no",
            rationale=remarks.strip(),
        )
        return self._handoff(
            case,
            CaseState.VERIFIED_BY_NO,
            self._context.subject,
            UserRole.NODAL_REGIONAL_OFFICER,
            remarks,
            "CASE_VERIFY",
        )

    def submit_to_hod(
        self,
        case_id: str,
        *,
        assigned_subject: str,
        remarks: str,
    ) -> CaseRecord:
        case = self._require_case(case_id)
        self._require_role(UserRole.NODAL_REGIONAL_OFFICER)
        if case.state not in {CaseState.VERIFIED_BY_NO, CaseState.RETURNED_TO_NO}:
            raise InvalidTransition("case cannot be submitted to HOD from this state")
        if case.state is CaseState.RETURNED_TO_NO:
            self._store.close_open_tasks(case_id)
        return self._handoff(
            case,
            CaseState.SUBMITTED_TO_HOD,
            assigned_subject,
            UserRole.HOD,
            remarks,
            "CASE_SUBMIT_TO_HOD",
        )

    def return_to_no(self, case_id: str, *, remarks: str) -> CaseRecord:
        case = self._require_case(case_id)
        self._require_role(UserRole.HOD)
        self._require_state(case, CaseState.SUBMITTED_TO_HOD)
        no_subject = self._latest_participant_for_role(
            self._store.timeline(case_id),
            UserRole.NODAL_REGIONAL_OFFICER,
        )
        self._store.create_task(case_id, remarks.strip(), no_subject)
        return self._handoff(
            case,
            CaseState.RETURNED_TO_NO,
            no_subject,
            UserRole.NODAL_REGIONAL_OFFICER,
            remarks,
            "CASE_RETURN_TO_NO",
        )

    def approve(self, case_id: str, *, remarks: str) -> CaseRecord:
        case = self._hod_decision_preconditions(case_id)
        if not self._store.has_delegated_authority(case_id, "approve"):
            raise CaseAccessDenied("approval exceeds delegated authority")
        self._store.add_decision(case_id, outcome="approved", rationale=remarks.strip())
        return self._handoff(
            case,
            CaseState.APPROVED,
            self._context.subject,
            UserRole.HOD,
            remarks,
            "CASE_APPROVE",
        )

    def reject(self, case_id: str, *, remarks: str) -> CaseRecord:
        case = self._hod_decision_preconditions(case_id)
        self._store.add_decision(case_id, outcome="rejected", rationale=remarks.strip())
        return self._handoff(
            case,
            CaseState.REJECTED,
            self._context.subject,
            UserRole.HOD,
            remarks,
            "CASE_REJECT",
        )

    def escalate(
        self,
        case_id: str,
        *,
        assigned_subject: str,
        remarks: str,
    ) -> CaseRecord:
        case = self._hod_decision_preconditions(case_id)
        return self._handoff(
            case,
            CaseState.ESCALATED,
            assigned_subject,
            UserRole.HOD,
            remarks,
            "CASE_ESCALATE",
        )

    def _hod_decision_preconditions(self, case_id: str) -> CaseRecord:
        case = self._require_case(case_id)
        self._require_role(UserRole.HOD)
        self._require_state(case, CaseState.SUBMITTED_TO_HOD)
        timeline = self._store.timeline(case_id)
        if self._context.subject == case.created_by_subject or (
            timeline is not None
            and any(
                transition.actor_subject == self._context.subject
                and transition.to_state is CaseState.VERIFIED_BY_NO
                for transition in timeline.transitions
            )
        ):
            raise CaseAccessDenied("maker or verifier cannot approve their own case")
        return case

    def _handoff(
        self,
        case: CaseRecord,
        to_state: CaseState,
        assigned_subject: str,
        assigned_role: UserRole,
        remarks: str,
        audit_category: str,
    ) -> CaseRecord:
        self._require_current_owner(case)
        clean_subject = assigned_subject.strip()
        clean_remarks = remarks.strip()
        if not clean_subject or not clean_remarks:
            raise CaseWorkflowError("assigned subject and handoff remarks are required")
        actor_role = self._role_for_state[case.state]
        self._store.update_rolling_summary(
            case.case_id,
            f"{case.objective} State: {to_state.value}. Latest handoff: "
            f"{clean_remarks[:500]}",
        )
        updated = self._store.transition(
            case,
            to_state=to_state,
            actor_role=actor_role,
            assigned_subject=clean_subject,
            assigned_role=assigned_role,
            remarks=clean_remarks,
        )
        capsule = self._create_capsule(updated)
        self._audit(audit_category, case.case_id, (capsule.state_hash,))
        return updated

    def _create_capsule(self, case: CaseRecord) -> ContextCapsule:
        snapshot = self._store.context_snapshot(case.case_id)
        capsule = self._context_engine.create_capsule(
            snapshot,
            version=self._store.next_capsule_version(case.case_id),
        )
        self._store.save_capsule(capsule)
        return capsule

    def _require_case(self, case_id: str) -> CaseRecord:
        case = self._store.get_case(case_id)
        if case is None:
            self._audit("CASE_READ", case_id, result_status="DENIED")
            raise CaseNotFound("case not found")
        return case

    def _require_current_owner(self, case: CaseRecord) -> None:
        if (
            UserRole.ADMINISTRATOR not in self._context.roles
            and case.current_owner_subject != self._context.subject
        ):
            raise CaseAccessDenied("only the current assignee may change the case")

    def _require_role(self, role: UserRole) -> None:
        if role not in self._context.roles and UserRole.ADMINISTRATOR not in self._context.roles:
            raise CaseAccessDenied(f"{role.value} role is required")

    @staticmethod
    def _require_state(case: CaseRecord, expected: CaseState) -> None:
        if case.state is not expected:
            raise InvalidTransition(f"case must be in {expected.value}")

    @staticmethod
    def _latest_participant_for_role(
        timeline: CaseTimeline | None,
        role: UserRole,
    ) -> str:
        if timeline is None:
            raise CaseNotFound("case not found")
        for transition in reversed(timeline.transitions):
            if transition.assigned_role is role:
                return transition.assigned_subject
        raise CaseWorkflowError(f"no {role.value} participant is recorded")

    def _audit(
        self,
        category: str,
        case_id: str,
        source_ids: tuple[str, ...] = (),
        result_status: str = "ALLOWED",
    ) -> None:
        self._store.record_audit(
            create_audit_event(
                self._context,
                query_category=category,
                entity_scope={"case_id": case_id},
                source_ids=source_ids,
                result_status=result_status,
            )
        )
