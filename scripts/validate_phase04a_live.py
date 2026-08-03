"""Run the Phase 04A API/RLS gate in an isolated PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path

import psycopg
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import (
    PostgresServiceProvider,
    create_app,
    get_authorization_context,
)
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import URL, Engine, text
from sqlalchemy.exc import DBAPIError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _admin_connection(settings: Settings, database: str) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for live validation")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
        autocommit=True,
    )


def _create_isolated_runtime(
    settings: Settings,
    database: str,
    role: str,
    password: str,
) -> None:
    with (
        _admin_connection(settings, settings.postgres_database) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role),
                sql.Literal(password),
            )
        )
        cursor.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
        )


def _apply_migrations(settings: Settings, database: str) -> None:
    environment = os.environ.copy()
    environment["POSTGRES_DATABASE"] = database
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260728_0003"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "isolated Alembic migration failed: "
            + completed.stderr.strip().splitlines()[-1]
        )
    with _admin_connection(settings, database) as connection, connection.cursor() as cursor:
        version = cursor.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
        if version is None or version[0] != "20260728_0003":
            raise RuntimeError("isolated database did not reach Phase 04A head")


def _grant_runtime_access(
    settings: Settings,
    database: str,
    role: str,
) -> None:
    with _admin_connection(settings, database) as connection, connection.cursor() as cursor:
        role_identifier = sql.Identifier(role)
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database),
                role_identifier,
            )
        )
        cursor.execute(
            sql.SQL(
                "GRANT USAGE ON SCHEMA pms_app, pms_audit, pms_chat TO {}"
            ).format(role_identifier)
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON ALL TABLES "
                "IN SCHEMA pms_chat TO {}"
            ).format(role_identifier)
        )
        cursor.execute(
            sql.SQL(
                "GRANT INSERT ON TABLE pms_audit.security_event TO {}"
            ).format(role_identifier)
        )
        cursor.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION pms_app.has_role(text), "
                "pms_app.classification_rank(text) TO {}"
            ).format(role_identifier)
        )
        cursor.execute(
            """
                INSERT INTO pms_chat.delegated_authority
                (authority_id, subject, department_id, unit_id, action,
                 valid_from, valid_to, active, approved_by_subject, created_at)
                VALUES
                ('phase04a-authority', 'hod-live', 'estate', 'land', 'approve',
                 now(), NULL, true, 'validation-admin', now())
                """
        )


def _runtime_engine(
    settings: Settings,
    database: str,
    role: str,
    password: str,
) -> tuple[Engine, Settings]:
    url = URL.create(
        "postgresql+psycopg",
        username=role,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database,
    )
    runtime_settings = Settings(
        database_url=SecretStr(url.render_as_string(hide_password=False)),
        db_ssl_mode=settings.db_ssl_mode,
        db_connect_timeout_seconds=settings.db_connect_timeout_seconds,
        db_command_timeout_seconds=settings.db_command_timeout_seconds,
    )
    return create_database_engine(runtime_settings, read_only=False), runtime_settings


def _contexts() -> dict[str, AuthorizationContext]:
    return {
        "do-live": AuthorizationContext(
            subject="do-live",
            roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
            tenant_id=None,
            department_id="estate",
            unit_id="land",
            classification=Classification.RESTRICTED,
        ),
        "no-live": AuthorizationContext(
            subject="no-live",
            roles=frozenset({UserRole.NODAL_REGIONAL_OFFICER}),
            tenant_id=None,
            department_id="estate",
            unit_id="land",
            classification=Classification.RESTRICTED,
        ),
        "hod-live": AuthorizationContext(
            subject="hod-live",
            roles=frozenset({UserRole.HOD}),
            tenant_id=None,
            department_id="estate",
            unit_id="land",
            classification=Classification.RESTRICTED,
        ),
        "stranger-live": AuthorizationContext(
            subject="stranger-live",
            roles=frozenset({UserRole.NODAL_REGIONAL_OFFICER}),
            tenant_id=None,
            department_id="estate",
            unit_id="land",
            classification=Classification.RESTRICTED,
        ),
    }


async def _exercise_api(
    engine: Engine,
    runtime_settings: Settings,
) -> tuple[str, str]:
    contexts = _contexts()
    app = create_app(PostgresServiceProvider(engine, runtime_settings))

    def fake_context(request: Request) -> AuthorizationContext:
        token = request.headers["authorization"].removeprefix("Bearer ")
        return contexts[token]

    app.dependency_overrides[get_authorization_context] = fake_context
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://phase04a.test",
    ) as client:
        created = await client.post(
            "/api/v1/cases",
            headers={"Authorization": "Bearer do-live"},
            json={
                "title": "Synthetic Phase 04A validation",
                "objective": "Prove one secure chronological case across handoffs.",
                "initial_message": "DO prepared the initial synthetic case.",
                "unit_id": "land",
                "classification": "internal",
            },
        )
        created.raise_for_status()
        case_id = str(created.json()["case_id"])
        thread_id = str(created.json()["thread_id"])
        first_message_id = ""

        submitted = await client.post(
            f"/api/v1/cases/{case_id}/submit-to-no",
            headers={"Authorization": "Bearer do-live"},
            json={"assigned_subject": "no-live", "remarks": "Verify the case."},
        )
        submitted.raise_for_status()

        no_timeline = await client.get(
            f"/api/v1/cases/{case_id}/timeline",
            headers={"Authorization": "Bearer no-live"},
        )
        no_timeline.raise_for_status()
        no_payload = no_timeline.json()
        first_message_id = str(no_payload["messages"][0]["message_id"])
        if no_payload["case"]["thread_id"] != thread_id:
            raise RuntimeError("NO did not receive the original thread")

        returned = await client.post(
            f"/api/v1/cases/{case_id}/return-to-do",
            headers={"Authorization": "Bearer no-live"},
            json={"remarks": "Correct the synthetic agreement date."},
        )
        returned.raise_for_status()

        correction = await client.post(
            f"/api/v1/cases/{case_id}/messages",
            headers={"Authorization": "Bearer do-live"},
            json={
                "body": "DO corrected the date in the same case.",
                "supersedes_message_id": first_message_id,
                "evidence": [
                    {
                        "reference_type": "sql_record",
                        "reference_id": "synthetic-lease-42",
                        "version": "1",
                    }
                ],
                "artifacts": [
                    {
                        "artifact_id": "synthetic-renewal-note",
                        "version": 2,
                        "review_status": "draft",
                    }
                ],
            },
        )
        correction.raise_for_status()
        if correction.json()["sequence_number"] != 2:
            raise RuntimeError("thread sequence did not increase monotonically")

        resubmitted = await client.post(
            f"/api/v1/cases/{case_id}/submit-to-no",
            headers={"Authorization": "Bearer do-live"},
            json={
                "assigned_subject": "no-live",
                "remarks": "Correction completed and resubmitted.",
            },
        )
        resubmitted.raise_for_status()
        verified = await client.post(
            f"/api/v1/cases/{case_id}/verify",
            headers={"Authorization": "Bearer no-live"},
            json={"remarks": "NO verified the corrected evidence."},
        )
        verified.raise_for_status()
        forwarded = await client.post(
            f"/api/v1/cases/{case_id}/submit-to-hod",
            headers={"Authorization": "Bearer no-live"},
            json={
                "assigned_subject": "hod-live",
                "remarks": "Verified case submitted to HOD.",
            },
        )
        forwarded.raise_for_status()

        hod_timeline = await client.get(
            f"/api/v1/cases/{case_id}/timeline",
            headers={"Authorization": "Bearer hod-live"},
        )
        hod_timeline.raise_for_status()
        capsule = hod_timeline.json()["capsules"][-1]
        if not capsule["decisions"] or not capsule["evidence"]:
            raise RuntimeError("HOD capsule omitted decision or evidence lineage")
        if not capsule["artifact_versions"]:
            raise RuntimeError("HOD capsule omitted artifact versions")
        if len(capsule["state_hash"]) != 64:
            raise RuntimeError("context capsule state hash is invalid")

        approved = await client.post(
            f"/api/v1/cases/{case_id}/approve",
            headers={"Authorization": "Bearer hod-live"},
            json={"remarks": "Approved within synthetic delegated authority."},
        )
        approved.raise_for_status()
        if approved.json()["state"] != "approved":
            raise RuntimeError("case did not reach approved state")

        guessed = await client.get(
            f"/api/v1/cases/{case_id}",
            headers={"Authorization": "Bearer stranger-live"},
        )
        if guessed.status_code != 404:
            raise RuntimeError("unassigned officer accessed a guessed case ID")
    return case_id, thread_id


async def _verify_restart(
    engine: Engine,
    runtime_settings: Settings,
    case_id: str,
    thread_id: str,
) -> None:
    context = _contexts()["hod-live"]
    app = create_app(PostgresServiceProvider(engine, runtime_settings))
    app.dependency_overrides[get_authorization_context] = lambda: context
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://phase04a-restart.test",
    ) as client:
        response = await client.get(f"/api/v1/cases/{case_id}/timeline")
        response.raise_for_status()
        payload = response.json()
        if payload["case"]["thread_id"] != thread_id:
            raise RuntimeError("reconstructed runtime did not preserve the thread")
        if payload["case"]["state"] != "approved":
            raise RuntimeError("reconstructed runtime lost the approved state")


def _verify_database_controls(
    settings: Settings,
    database: str,
    case_id: str,
) -> None:
    engine = create_database_engine(
        Settings(
            postgres_host=settings.postgres_host,
            postgres_port=settings.postgres_port,
            postgres_database=database,
            postgres_user=settings.postgres_user,
            postgres_password=settings.postgres_password,
            db_ssl_mode=settings.db_ssl_mode,
        ),
        read_only=False,
    )
    try:
        with engine.connect() as connection:
            forced = connection.execute(
                text(
                    "SELECT count(*) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='pms_chat' AND c.relkind='r' "
                    "AND c.relrowsecurity AND c.relforcerowsecurity"
                )
            ).scalar_one()
            if forced != 15:
                raise RuntimeError("not all pms_chat tables force RLS")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE pms_chat.case_message SET body='forbidden' "
                        "WHERE thread_id=(SELECT thread_id FROM pms_chat.case_record "
                        "WHERE case_id=:case_id)"
                    ),
                    {"case_id": case_id},
                )
        except DBAPIError as error:
            if "case messages are immutable" not in str(error.orig):
                raise
        else:
            raise RuntimeError("database allowed a silent message edit")
    finally:
        engine.dispose()


def _drop_isolated_runtime(
    settings: Settings,
    database: str,
    role: str,
) -> None:
    with (
        _admin_connection(settings, settings.postgres_database) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database)
            )
        )
        cursor.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
        )


def main() -> int:
    settings = Settings()
    suffix = secrets.token_hex(5)
    database = f"pms_phase04a_{suffix}"
    role = f"pms_phase04a_role_{suffix}"
    password = secrets.token_urlsafe(32)
    created = False
    engine = None
    try:
        created = True
        _create_isolated_runtime(settings, database, role, password)
        _apply_migrations(settings, database)
        _grant_runtime_access(settings, database, role)
        engine, runtime_settings = _runtime_engine(
            settings,
            database,
            role,
            password,
        )
        case_id, thread_id = asyncio.run(_exercise_api(engine, runtime_settings))
        engine.dispose()
        engine, runtime_settings = _runtime_engine(
            settings,
            database,
            role,
            password,
        )
        asyncio.run(_verify_restart(engine, runtime_settings, case_id, thread_id))
        _verify_database_controls(settings, database, case_id)
        print("PASS phase04a_live_workflow")
        print("PASS postgres_rls_and_immutable_messages")
        print("PASS api_restart_continuity")
        return 0
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            _drop_isolated_runtime(settings, database, role)
            print("PASS isolated_runtime_cleanup")


if __name__ == "__main__":
    raise SystemExit(main())
