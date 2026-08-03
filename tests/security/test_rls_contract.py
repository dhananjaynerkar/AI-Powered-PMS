"""PostgreSQL session, RLS migration, realm, and audit contract tests."""

import json
from pathlib import Path
from typing import cast

from pms_common.migration_safety import validate_revision_directory
from pms_common.security import (
    AuthorizationContext,
    Classification,
    UserRole,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from sqlalchemy import Connection

ROOT = Path(__file__).parents[2]
REVISION = ROOT / "db/migrations/versions/20260728_0002_security_rls_and_audit.py"


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object],
    ) -> None:
        self.calls.append((str(statement), parameters))


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="subject-a",
        roles=frozenset({UserRole.TENANT}),
        tenant_id="tenant-a",
        department_id="estate",
        classification=Classification.INTERNAL,
    )


def test_session_context_uses_only_parameterized_local_settings() -> None:
    recording = RecordingConnection()

    apply_postgres_session_context(cast(Connection, recording), _context())

    assert len(recording.calls) == 6
    assert all(
        "set_config(:setting_name, :setting_value, true)" in sql
        for sql, _ in recording.calls
    )
    settings = {
        parameters["setting_name"]: parameters["setting_value"]
        for _, parameters in recording.calls
    }
    assert settings["pms.subject"] == "subject-a"
    assert settings["pms.tenant_id"] == "tenant-a"
    assert settings["pms.roles"] == "Tenant"
    assert settings["pms.unit_id"] == ""


def test_audit_insert_has_no_response_or_prompt_parameter() -> None:
    recording = RecordingConnection()
    event = create_audit_event(
        _context(),
        query_category="STRUCTURED",
        entity_scope={"canonical_tenant_id": "tenant-a"},
        source_ids=("record-1",),
        result_status="ALLOWED",
    )

    write_audit_event(cast(Connection, recording), event)

    sql, parameters = recording.calls[0]
    assert "pms_audit.security_event" in sql
    assert parameters["query_category"] == "STRUCTURED"
    assert "response" not in sql.lower()
    assert "prompt" not in sql.lower()


def test_phase04_revision_forces_rls_only_on_application_schemas() -> None:
    source = REVISION.read_text(encoding="utf-8")

    validate_revision_directory(REVISION.parent)
    assert source.count("FORCE ROW LEVEL SECURITY") == 1
    for qualified_table in (
        "pms_app.user_tenant_mapping",
        "pms_doc.document_acl",
        "pms_vector.chunk_acl",
        "pms_audit.security_event",
    ):
        assert qualified_table in source
    assert 'schema="public"' not in source
    assert "public." not in source
    assert "pms_extract_2010_2023" not in source


def test_keycloak_realm_contains_exact_approved_roles_and_claim_mappers() -> None:
    realm = json.loads(
        (ROOT / "infra/keycloak/pms-realm.json").read_text(encoding="utf-8")
    )
    roles = {role["name"] for role in realm["roles"]["realm"]}
    mapper_names = {
        mapper["name"]
        for mapper in realm["clients"][0]["protocolMappers"]
    }

    assert roles == {role.value for role in UserRole}
    assert realm["realm"] == "pms"
    assert realm["clients"][0]["clientId"] == "pms-api"
    assert realm["clients"][0]["bearerOnly"] is False
    assert realm["clients"][0]["publicClient"] is False
    assert realm["clients"][0]["standardFlowEnabled"] is True
    assert realm["clients"][0]["directAccessGrantsEnabled"] is False
    assert realm["clients"][0]["serviceAccountsEnabled"] is False
    assert "secret" not in realm["clients"][0]
    assert not realm.get("users")
    assert {"tenant-id", "department", "unit-id", "classification"}.issubset(
        mapper_names
    )


def test_keycloak_compose_has_bounded_restart_and_persistent_state() -> None:
    source = (ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

    assert 'restart: "on-failure:3"' in source
    assert "keycloak-data:/opt/keycloak/data" in source
    assert "\nvolumes:\n  keycloak-data:" in source
    assert '"127.0.0.1:8080:8080"' in source
