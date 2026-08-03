"""Phase 04A DO → NO → HOD continuity and negative-access gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from pms_case_workflow.models import CaseState, CreateCase
from pms_case_workflow.service import (
    CaseAccessDenied,
    CaseNotFound,
    CaseWorkflowError,
    CaseWorkflowService,
)
from pms_common.migration_safety import validate_revision_directory
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_context import (
    ArtifactReference,
    CaseContextSnapshot,
    ContextEngine,
    ContextMessage,
    EvidenceReference,
)

from tests.case_workflow.support import MemoryBackend, MemoryCaseStore

ROOT = Path(__file__).parents[2]


def _context(subject: str, role: UserRole) -> AuthorizationContext:
    return AuthorizationContext(
        subject=subject,
        roles=frozenset({role}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _service(
    backend: MemoryBackend,
    subject: str,
    role: UserRole,
) -> CaseWorkflowService:
    context = _context(subject, role)
    return CaseWorkflowService(MemoryCaseStore(backend, context), context)


def test_complete_shared_case_flow_preserves_thread_and_context() -> None:
    backend = MemoryBackend()
    do = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    case = do.create_case(
        CreateCase(
            title="Plot renewal review",
            objective="Verify the renewal record and obtain HOD approval.",
            initial_message="Prepared the initial renewal facts.",
            unit_id="land",
        )
    )
    original_thread = case.thread_id

    case = do.submit_to_no(
        case.case_id,
        assigned_subject="no-1",
        remarks="Please verify the renewal evidence.",
    )
    no = _service(backend, "no-1", UserRole.NODAL_REGIONAL_OFFICER)
    first_no_timeline = no.timeline(case.case_id)

    assert case.thread_id == original_thread
    assert first_no_timeline.messages[0].sequence_number == 1
    assert first_no_timeline.capsules[-1].current_state == "submitted_to_no"

    case = no.return_to_do(case.case_id, remarks="Correct the agreement date.")
    assert case.state is CaseState.RETURNED_TO_DO
    assert backend.capsules[case.case_id][-1].open_tasks[0].title == (
        "Correct the agreement date."
    )

    do = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    correction = do.add_message(
        case.case_id,
        body="Corrected the agreement date and regenerated the note.",
        supersedes_message_id=first_no_timeline.messages[0].message_id,
        evidence=(EvidenceReference("sql_record", "lease-42", "2026-07-28"),),
        artifacts=(ArtifactReference("renewal-note", 2, "draft"),),
    )
    assert correction.sequence_number == 2

    case = do.submit_to_no(
        case.case_id,
        assigned_subject="no-1",
        remarks="Correction completed in the same case.",
    )
    no = _service(backend, "no-1", UserRole.NODAL_REGIONAL_OFFICER)
    case = no.verify(case.case_id, remarks="Agreement date and evidence verified.")
    case = no.submit_to_hod(
        case.case_id,
        assigned_subject="hod-1",
        remarks="Verified case submitted for decision.",
    )

    hod = _service(backend, "hod-1", UserRole.HOD)
    hod_timeline = hod.timeline(case.case_id)
    capsule = hod_timeline.capsules[-1]

    assert case.thread_id == original_thread
    assert [message.sequence_number for message in hod_timeline.messages] == [1, 2]
    assert capsule.decisions[-1].outcome == "verified_by_no"
    assert capsule.evidence[-1].reference_id == "lease-42"
    assert capsule.artifact_versions[-1].version == 2
    assert "submitted_to_hod" in capsule.rolling_summary
    assert capsule.required_next_action.startswith("HOD must")
    assert len(capsule.state_hash) == 64

    backend.approval_authority.add(("hod-1", "approve"))
    approved = hod.approve(case.case_id, remarks="Approved within delegated authority.")

    assert approved.state is CaseState.APPROVED
    assert approved.thread_id == original_thread
    assert any(event.query_category == "CASE_APPROVE" for event in backend.audits)


def test_unauthorized_officer_cannot_guess_case_id() -> None:
    backend = MemoryBackend()
    do = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    case = do.create_case(
        CreateCase("Title", "Objective", "Initial", "land")
    )
    stranger = _service(backend, "no-stranger", UserRole.NODAL_REGIONAL_OFFICER)

    with pytest.raises(CaseNotFound):
        stranger.get_case(case.case_id)
    assert backend.audits[-1].result_status == "DENIED"


def test_return_requires_observations_and_messages_are_superseded_not_edited() -> None:
    backend = MemoryBackend()
    do = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    case = do.create_case(CreateCase("Title", "Objective", "Initial", "land"))
    case = do.submit_to_no(case.case_id, assigned_subject="no-1", remarks="Review")
    no = _service(backend, "no-1", UserRole.NODAL_REGIONAL_OFFICER)

    with pytest.raises(CaseWorkflowError, match="remarks"):
        no.return_to_do(case.case_id, remarks=" ")

    returned = no.return_to_do(case.case_id, remarks="Correct the date")
    do = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    original = backend.messages[returned.thread_id][0]
    correction = do.add_message(
        returned.case_id,
        body="Corrected",
        supersedes_message_id=original.message_id,
    )

    assert backend.messages[returned.thread_id][0] == original
    assert correction.supersedes_message_id == original.message_id


def test_maker_cannot_verify_and_hod_needs_delegated_authority() -> None:
    backend = MemoryBackend()
    do = _service(backend, "same-person", UserRole.DATA_ENTRY_OPERATOR)
    case = do.create_case(CreateCase("Title", "Objective", "Initial", "land"))
    case = do.submit_to_no(
        case.case_id,
        assigned_subject="same-person",
        remarks="Review",
    )
    same_person_no = _service(
        backend,
        "same-person",
        UserRole.NODAL_REGIONAL_OFFICER,
    )
    with pytest.raises(CaseAccessDenied, match="maker"):
        same_person_no.verify(case.case_id, remarks="Verified")

    backend = MemoryBackend()
    do = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    case = do.create_case(CreateCase("Title", "Objective", "Initial", "land"))
    case = do.submit_to_no(case.case_id, assigned_subject="no-1", remarks="Review")
    no = _service(backend, "no-1", UserRole.NODAL_REGIONAL_OFFICER)
    case = no.verify(case.case_id, remarks="Verified")
    case = no.submit_to_hod(case.case_id, assigned_subject="hod-1", remarks="Decide")
    hod = _service(backend, "hod-1", UserRole.HOD)

    with pytest.raises(CaseAccessDenied, match="delegated"):
        hod.approve(case.case_id, remarks="Approve")


def test_service_reconstruction_does_not_own_or_lose_case_state() -> None:
    backend = MemoryBackend()
    first_service = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    case = first_service.create_case(CreateCase("Title", "Objective", "Initial", "land"))

    reconstructed_service = _service(backend, "do-1", UserRole.DATA_ENTRY_OPERATOR)

    assert reconstructed_service.get_case(case.case_id) == case
    assert reconstructed_service.timeline(case.case_id).messages[0].body == "Initial"


def test_context_engine_bounds_retrieved_older_messages() -> None:
    recent = ContextMessage("recent", 10, "do-1", "Recent")
    older = (
        ContextMessage("old-1", 1, "do-1", "Older one"),
        ContextMessage("old-2", 2, "no-1", "Older two"),
    )
    engine = ContextEngine(
        recent_message_window=1,
        retrieved_message_top_k=1,
        older_message_retriever=lambda case_id, query, limit, excluded: older,
    )
    snapshot = CaseContextSnapshot(
        case_id="case-1",
        thread_id="thread-1",
        objective="Review",
        current_state="submitted_to_no",
        current_owner_subject="no-1",
        rolling_summary="Review started",
        recent_messages=(recent,),
    )

    capsule = engine.create_capsule(snapshot, version=1)

    assert capsule.recent_messages == (recent,)
    assert capsule.retrieved_older_messages == (older[0],)
    assert len(capsule.state_hash) == 64


def test_phase04a_migration_has_required_tables_states_rls_and_immutability() -> None:
    migration = (
        ROOT
        / "db/migrations/versions/20260728_0003_shared_case_workflow.py"
    )
    source = migration.read_text(encoding="utf-8")
    validate_revision_directory(migration.parent)

    for table in (
        "case_record",
        "case_participant",
        "case_assignment",
        "case_transition",
        "case_task",
        "case_decision",
        "case_thread",
        "case_message",
        "message_attachment",
        "message_read_receipt",
        "message_reference",
        "case_artifact_version",
        "case_rolling_summary",
        "context_capsule",
        "delegated_authority",
    ):
        assert f'"{table}"' in source
    for state in CaseState:
        assert f'"{state.value}"' in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "reject_message_mutation" in source
    assert "next_sequence_number" in source
    assert 'schema="public"' not in source
    assert "pms_extract_2010_2023" not in source
