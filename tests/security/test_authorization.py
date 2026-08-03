"""Negative authorization, ACL, and audit gate tests."""

import pytest
from pms_common.security import (
    AuditRecorder,
    AuthorizationContext,
    AuthorizationDenied,
    AuthorizationService,
    CanonicalTenantMapping,
    Classification,
    DocumentAclService,
    DocumentChunkAccess,
    Permission,
    UserRole,
    create_audit_event,
)


def _tenant_context(tenant_id: str = "tenant-a") -> AuthorizationContext:
    return AuthorizationContext(
        subject="keycloak-subject-a",
        roles=frozenset({UserRole.TENANT}),
        tenant_id=tenant_id,
        department_id=None,
        classification=Classification.INTERNAL,
    )


def test_tenant_a_cannot_read_tenant_b() -> None:
    with pytest.raises(AuthorizationDenied):
        AuthorizationService().require_tenant_record(
            _tenant_context(),
            "tenant-b",
        )


def test_tenant_cannot_read_port_wide_aggregates() -> None:
    with pytest.raises(AuthorizationDenied):
        AuthorizationService().require_permission(
            _tenant_context(),
            Permission.PORT_WIDE_AGGREGATE,
        )


def test_role_restrictions_allow_only_auditor_or_admin_to_read_audit() -> None:
    legal = AuthorizationContext(
        subject="legal-subject",
        roles=frozenset({UserRole.LEGAL_OFFICER}),
        tenant_id=None,
        department_id="legal",
        classification=Classification.CONFIDENTIAL,
    )
    auditor = AuthorizationContext(
        subject="auditor-subject",
        roles=frozenset({UserRole.AUDITOR}),
        tenant_id=None,
        department_id="audit",
        classification=Classification.RESTRICTED,
    )
    service = AuthorizationService()

    with pytest.raises(AuthorizationDenied):
        service.require_permission(legal, Permission.AUDIT_READ)
    service.require_permission(auditor, Permission.AUDIT_READ)


def test_vector_candidates_exclude_unauthorized_chunks() -> None:
    chunks = (
        DocumentChunkAccess(
            chunk_id="own",
            canonical_document_id="doc-a",
            tenant_id="tenant-a",
            classification=Classification.INTERNAL,
        ),
        DocumentChunkAccess(
            chunk_id="other-tenant",
            canonical_document_id="doc-b",
            tenant_id="tenant-b",
            classification=Classification.INTERNAL,
        ),
        DocumentChunkAccess(
            chunk_id="too-secret",
            canonical_document_id="doc-c",
            tenant_id="tenant-a",
            classification=Classification.CONFIDENTIAL,
        ),
        DocumentChunkAccess(
            chunk_id="wrong-role",
            canonical_document_id="doc-d",
            tenant_id="tenant-a",
            classification=Classification.INTERNAL,
            allowed_roles=frozenset({UserRole.LEGAL_OFFICER}),
        ),
    )

    authorized = DocumentAclService().filter_authorized(_tenant_context(), chunks)

    assert tuple(chunk.chunk_id for chunk in authorized) == ("own",)


def test_audit_event_is_generated_without_response_body() -> None:
    recorder = AuditRecorder()
    event = create_audit_event(
        _tenant_context(),
        query_category="DOCUMENT",
        entity_scope={"canonical_tenant_id": "tenant-a"},
        source_ids=("chunk-1",),
        model_version="qwen3.5:4b",
        result_status="ALLOWED",
    )

    recorder.record(event)

    assert recorder.events == [event]
    assert event.source_ids == ("chunk-1",)
    assert not hasattr(event, "response_body")
    assert not hasattr(event, "question")


def test_forged_frontend_tenant_id_is_ignored() -> None:
    effective = AuthorizationService().effective_tenant_id(
        _tenant_context(),
        frontend_tenant_id="tenant-b",
    )

    assert effective == "tenant-a"


def test_signed_tenant_must_match_canonical_mapping() -> None:
    service = AuthorizationService()
    mappings = (
        CanonicalTenantMapping(
            subject="keycloak-subject-a",
            canonical_tenant_id="tenant-b",
            role=UserRole.TENANT,
        ),
    )

    with pytest.raises(AuthorizationDenied):
        service.require_canonical_mapping(_tenant_context(), mappings)

