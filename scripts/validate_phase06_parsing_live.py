"""Validate Phase 06 parsing and persistence in an isolated PostgreSQL database."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import psycopg
import pymupdf
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import ObjectWrite
from pms_ingestion.parsing_service import DocumentParsingCoordinator
from pms_ingestion.storage import ObjectStore
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MemoryObjectStore(ObjectStore):
    """Exercise immutable lineage without leaving isolated-test objects in MinIO."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str | None], bytes] = {}
        self.put_calls = 0

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
        self.put_calls += 1
        version = f"memory-version-{self.put_calls}"
        self.objects[(bucket_name, object_key, version)] = content
        return ObjectWrite(bucket_name, object_key, version, f"etag-{self.put_calls}")

    def get(
        self,
        *,
        bucket_name: str,
        object_key: str,
        object_version: str | None,
    ) -> bytes:
        return self.objects[(bucket_name, object_key, object_version)]


def _admin_connection(
    settings: Settings,
    database: str,
) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for isolated validation")
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
        try:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        except Exception:
            cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            raise


def _admin_url(settings: Settings, database: str) -> str:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for isolated validation")
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database,
    ).render_as_string(hide_password=False)


def _migrate(settings: Settings, database: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = _admin_url(settings, database)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260729_0005"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()
        raise RuntimeError(
            "isolated Phase 06 migration failed: "
            + (message[-1] if message else "unknown migration error")
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
            sql.SQL("GRANT USAGE ON SCHEMA pms_app, pms_audit, pms_doc TO {}").format(
                role_identifier
            )
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
        java_home=settings.java_home,
    )


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="phase06-parser",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _controlled_pdf() -> bytes:
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "PMS Phase 06 Controlled Technical Fixture",
        fontsize=16,
    )
    page.insert_text(
        (72, 105),
        "Section 4 applies from 01/04/2026 at 18%.",
        fontsize=11,
    )
    page.insert_text(
        (72, 125),
        "Provided that this file contains no business or personal data.",
        fontsize=11,
    )
    content: bytes = document.tobytes()  # type: ignore[no-untyped-call]
    document.close()  # type: ignore[no-untyped-call]
    return content


def _exercise(runtime_settings: Settings) -> tuple[str, int]:
    engine = create_database_engine(runtime_settings, read_only=False)
    objects = MemoryObjectStore()
    context = _context()
    try:
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                context,
                runtime_settings,
                object_store=objects,
            )
            uploaded = service.upload(
                title="Phase 06 controlled technical fixture",
                filename="phase06-controlled.pdf",
                mime_type="application/pdf",
                content=_controlled_pdf(),
                classification=Classification.INTERNAL,
            )
        coordinator = DocumentParsingCoordinator(
            engine,
            context,
            runtime_settings,
            object_store=objects,
        )
        first = coordinator.parse(uploaded.document.canonical_document_id)
        writes_after_first = objects.put_calls
        second = coordinator.parse(uploaded.document.canonical_document_id)
        if not first.quality_passed or first.review_required:
            raise RuntimeError("controlled parser quality gate did not pass")
        if first.parser != "opendataloader" or first.page_count != 1:
            raise RuntimeError("deterministic primary parser was not selected")
        if not first.raw_object_keys or first.canonical_object_key is None:
            raise RuntimeError("raw and canonical artifacts were not persisted")
        if not second.idempotent or objects.put_calls != writes_after_first:
            raise RuntimeError("repeat parsing was not idempotent")
        forced = coordinator.parse(
            uploaded.document.canonical_document_id,
            force=True,
        )
        if forced.idempotent or objects.put_calls != writes_after_first + 1:
            raise RuntimeError("forced parse did not reuse identical raw output")
        return uploaded.document.canonical_document_id, objects.put_calls
    finally:
        engine.dispose()


def _verify_database(settings: Settings, database: str, document_id: str) -> None:
    with _admin_connection(settings, database) as connection, connection.cursor() as cursor:
        version = cursor.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
        if version is None or version[0] != "20260729_0005":
            raise RuntimeError("isolated database did not reach Phase 06 revision")
        status = cursor.execute(
            """
            SELECT status
            FROM pms_doc.document_record
            WHERE canonical_document_id = %s
            """,
            (document_id,),
        ).fetchone()
        if status is None or status[0] != "canonicalized":
            raise RuntimeError("document did not reach canonicalized status")
        kind_rows = cursor.execute(
            """
            SELECT artifact_kind, count(*)
            FROM pms_doc.derived_artifact
            GROUP BY artifact_kind
            ORDER BY artifact_kind
            """
        ).fetchall()
        kinds = {str(row[0]): int(str(row[1])) for row in kind_rows}
        if kinds != {"canonical_json": 2, "raw_parser": 1}:
            raise RuntimeError("raw/canonical lineage is incomplete")


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
    database = f"pms_phase06_{suffix}"
    role = f"pms_phase06_role_{suffix}"
    password = secrets.token_urlsafe(32)
    created = False
    try:
        _create_runtime(settings, database, role, password)
        created = True
        _migrate(settings, database)
        _grant_runtime_access(settings, database, role)
        runtime_settings = _runtime_settings(settings, database, role, password)
        document_id, object_writes = _exercise(runtime_settings)
        _verify_database(settings, database, document_id)
        print("PASS phase06_migration")
        print("PASS phase06_opendataloader_deterministic")
        print("PASS phase06_quality_gate")
        print("PASS phase06_raw_and_canonical_lineage")
        print("PASS phase06_status_state_machine")
        print("PASS phase06_idempotency")
        print(f"PASS phase06_memory_object_writes={object_writes}")
        return 0
    finally:
        if created:
            _drop_runtime(settings, database, role)
            print("PASS isolated_runtime_cleanup")


if __name__ == "__main__":
    raise SystemExit(main())
