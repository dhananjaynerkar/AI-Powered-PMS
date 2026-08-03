"""Validate the Phase 05 PostgreSQL registry in an isolated database."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import psycopg
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import ObjectWrite
from pms_ingestion.service import DocumentNotFound
from pms_ingestion.storage import ObjectStore
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MemoryObjectStore(ObjectStore):
    """Keep synthetic bytes outside PostgreSQL while testing the real registry."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str | None], bytes] = {}
        self.get_calls = 0

    def ensure_buckets(self, bucket_names: Iterable[str]) -> tuple[str, ...]:
        return tuple(bucket_names)

    def put_immutable(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content: bytes,
        mime_type: str,
        checksum_sha256: str,
    ) -> ObjectWrite:
        del mime_type, checksum_sha256
        version = f"memory-version-{len(self.objects) + 1}"
        self.objects[(bucket_name, object_key, version)] = content
        return ObjectWrite(bucket_name, object_key, version, version)

    def get(
        self,
        *,
        bucket_name: str,
        object_key: str,
        object_version: str | None,
    ) -> bytes:
        self.get_calls += 1
        return self.objects[(bucket_name, object_key, object_version)]


def _admin_connection(
    settings: Settings,
    database: str,
) -> psycopg.Connection[tuple[object, ...]]:
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


def _create_runtime(settings: Settings, database: str, role: str, password: str) -> None:
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
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))


def _migrate(settings: Settings, database: str) -> None:
    environment = os.environ.copy()
    environment["POSTGRES_DATABASE"] = database
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260729_0004"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "isolated Phase 05 migration failed: "
            + completed.stderr.strip().splitlines()[-1]
        )


def _grant_runtime_access(settings: Settings, database: str, role: str) -> None:
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
                "GRANT USAGE ON SCHEMA pms_app, pms_audit, pms_doc TO {}"
            ).format(role_identifier)
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON ALL TABLES "
                "IN SCHEMA pms_doc TO {}"
            ).format(role_identifier)
        )
        cursor.execute(
            sql.SQL("GRANT INSERT ON pms_audit.security_event TO {}").format(
                role_identifier
            )
        )
        cursor.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION pms_app.has_role(text), "
                "pms_app.classification_rank(text) TO {}"
            ).format(role_identifier)
        )


def _runtime_settings(
    settings: Settings,
    database: str,
    role: str,
    password: str,
) -> Settings:
    url = URL.create(
        "postgresql+psycopg",
        username=role,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database,
    )
    return Settings(
        database_url=SecretStr(url.render_as_string(hide_password=False)),
        db_ssl_mode=settings.db_ssl_mode,
        db_connect_timeout_seconds=settings.db_connect_timeout_seconds,
        db_command_timeout_seconds=settings.db_command_timeout_seconds,
        upload_max_mb=1,
    )


def _context(
    subject: str,
    role: UserRole,
    department: str,
) -> AuthorizationContext:
    return AuthorizationContext(
        subject=subject,
        roles=frozenset({role}),
        tenant_id=None,
        department_id=department,
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _exercise_registry(runtime_settings: Settings) -> str:
    engine = create_database_engine(runtime_settings, read_only=False)
    objects = MemoryObjectStore()
    uploader = _context("phase05-uploader", UserRole.DATA_ENTRY_OPERATOR, "estate")
    stranger = _context("phase05-stranger", UserRole.LEGAL_OFFICER, "legal")
    content = b"%PDF-1.7\nisolated phase 05 registry test\n%%EOF"
    try:
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                uploader,
                runtime_settings,
                object_store=objects,
            )
            created = service.upload(
                title="Synthetic registry validation",
                filename="synthetic.pdf",
                mime_type="application/pdf",
                content=content,
                classification=Classification.INTERNAL,
            )
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                uploader,
                runtime_settings,
                object_store=objects,
            )
            duplicate = service.upload(
                title="Synthetic registry validation",
                filename="synthetic.pdf",
                mime_type="application/pdf",
                content=content,
                classification=Classification.INTERNAL,
            )
            retrieved = service.retrieve(created.document.canonical_document_id)
        if not duplicate.duplicate or retrieved.content != content:
            raise RuntimeError("deduplication or retrieval validation failed")
        get_calls = objects.get_calls
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                stranger,
                runtime_settings,
                object_store=objects,
            )
            try:
                service.retrieve(created.document.canonical_document_id)
            except DocumentNotFound:
                pass
            else:
                raise RuntimeError("unauthorized registry access was not denied")
        if objects.get_calls != get_calls:
            raise RuntimeError("object storage was accessed before authorization")
        return created.document.canonical_document_id
    finally:
        engine.dispose()


def _verify_database_controls(
    settings: Settings,
    database: str,
    document_id: str,
) -> None:
    with _admin_connection(settings, database) as connection, connection.cursor() as cursor:
        version = cursor.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
        if version is None or version[0] != "20260729_0004":
            raise RuntimeError("isolated database did not reach Phase 05 revision")
        forced = cursor.execute(
            """
            SELECT count(*)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'pms_doc'
              AND c.relname IN (
                'document_record', 'stored_object',
                'document_version', 'derived_artifact'
              )
              AND c.relrowsecurity
              AND c.relforcerowsecurity
            """
        ).fetchone()
        if forced is None or forced[0] != 4:
            raise RuntimeError("Phase 05 registry tables do not all force RLS")
        try:
            cursor.execute(
                """
                UPDATE pms_doc.document_version
                SET mime_type = 'text/plain'
                WHERE canonical_document_id = %s
                """,
                (document_id,),
            )
        except psycopg.Error as error:
            if "document objects and lineage are immutable" not in str(error):
                raise
        else:
            raise RuntimeError("database allowed document lineage mutation")


def _drop_runtime(settings: Settings, database: str, role: str) -> None:
    with (
        _admin_connection(settings, settings.postgres_database) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database)
            )
        )
        cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def main() -> int:
    settings = Settings()
    suffix = secrets.token_hex(5)
    database = f"pms_phase05_{suffix}"
    role = f"pms_phase05_role_{suffix}"
    password = secrets.token_urlsafe(32)
    created = False
    try:
        created = True
        _create_runtime(settings, database, role, password)
        _migrate(settings, database)
        _grant_runtime_access(settings, database, role)
        runtime_settings = _runtime_settings(settings, database, role, password)
        document_id = _exercise_registry(runtime_settings)
        _verify_database_controls(settings, database, document_id)
        print("PASS phase05_registry_migration")
        print("PASS phase05_live_rls_and_immutability")
        print("PASS phase05_dedup_retrieval_and_checksum")
        print("PASS phase05_unauthorized_storage_short_circuit")
        return 0
    finally:
        if created:
            _drop_runtime(settings, database, role)
            print("PASS isolated_runtime_cleanup")


if __name__ == "__main__":
    raise SystemExit(main())
