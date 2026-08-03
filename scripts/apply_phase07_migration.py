"""Apply only Phase 07 after pgvector availability and schema-safety checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from pms_common.settings import Settings
from psycopg import sql
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CURRENT = "20260729_0005"
TARGET_REVISION = "20260729_0006"
PROTECTED_SCHEMAS = ("pms_extract_2010_2023", "public")
RUNTIME_ROLE = "pms_app_runtime"


def _connect(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for migration execution")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def _admin_url(settings: Settings) -> str:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for migration execution")
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_database,
    ).render_as_string(hide_password=False)


def _revision(connection: psycopg.Connection[tuple[object, ...]]) -> str:
    with connection.cursor() as cursor:
        row = cursor.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
    if row is None:
        raise RuntimeError("configured database has no Alembic revision")
    return str(row[0])


def _protected_metadata(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[int, str]:
    with connection.cursor() as cursor:
        rows = cursor.execute(
            """
            SELECT table_schema, table_name, column_name, ordinal_position,
                   data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = ANY(%s)
            ORDER BY table_schema, table_name, ordinal_position
            """,
            (list(PROTECTED_SCHEMAS),),
        ).fetchall()
    encoded = json.dumps(rows, default=str, separators=(",", ":")).encode("utf-8")
    return len(rows), hashlib.sha256(encoded).hexdigest()


def _pgvector_available(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[bool, str | None, str | None]:
    with connection.cursor() as cursor:
        available = cursor.execute(
            """
            SELECT default_version
            FROM pg_available_extensions
            WHERE name = 'vector'
            """
        ).fetchone()
        installed = cursor.execute(
            """
            SELECT n.nspname, e.extversion
            FROM pg_extension AS e
            JOIN pg_namespace AS n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
            """
        ).fetchone()
    if available is None:
        return False, None, None
    if installed is None:
        return True, None, str(available[0])
    return True, str(installed[0]), str(installed[1])


def _grant_runtime(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with connection.cursor() as cursor:
        role_exists = cursor.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (RUNTIME_ROLE,),
        ).fetchone()
        if role_exists is None:
            raise RuntimeError("least-privilege runtime role is missing")
        role = sql.Identifier(RUNTIME_ROLE)
        cursor.execute(
            sql.SQL("GRANT USAGE ON SCHEMA pms_vector TO {}").format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON "
                "pms_vector.document_chunk, "
                "pms_vector.chunk_embedding, "
                "pms_vector.index_checkpoint, "
                "pms_vector.chunk_acl TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL("GRANT USAGE ON TYPE pms_vector.vector TO {}").format(role)
        )
    connection.commit()


def _verify_phase07(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with connection.cursor() as cursor:
        extension = cursor.execute(
            """
            SELECT n.nspname
            FROM pg_extension AS e
            JOIN pg_namespace AS n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
            """
        ).fetchone()
        vector_type = cursor.execute(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid =
              'pms_vector.chunk_embedding'::regclass
              AND attribute.attname = 'embedding'
            """
        ).fetchone()
        tables = cursor.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'pms_vector'
              AND table_name = ANY(%s)
            """,
            (
                [
                    "document_chunk",
                    "chunk_embedding",
                    "index_checkpoint",
                ],
            ),
        ).fetchone()
        hnsw = cursor.execute(
            """
            SELECT count(*)
            FROM pg_indexes
            WHERE schemaname = 'pms_vector'
              AND indexdef ILIKE '%%hnsw%%'
            """
        ).fetchone()
    if extension is None or extension[0] != "pms_vector":
        raise RuntimeError("pgvector is not installed in pms_vector")
    if vector_type is None or vector_type[0] != "pms_vector.vector(1024)":
        raise RuntimeError("Phase 07 vector dimension is not 1024")
    if tables is None or int(str(tables[0])) != 3:
        raise RuntimeError("Phase 07 tables are incomplete")
    if hnsw is None or int(str(hnsw[0])) != 0:
        raise RuntimeError("Phase 07 must not create HNSW")


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection:
        current = _revision(connection)
        available, installed_schema, version = _pgvector_available(connection)
        if not available:
            print(f"BLOCKED configured_revision={current}")
            print("BLOCKED pgvector_server_extension=not_available")
            return 2
        if installed_schema not in {None, "pms_vector"}:
            print(f"BLOCKED pgvector_extension_schema={installed_schema}")
            return 2
        if current == TARGET_REVISION:
            _verify_phase07(connection)
            print(f"PASS configured_revision={current} already_applied=true")
            return 0
        if current != EXPECTED_CURRENT:
            raise RuntimeError(
                f"expected configured revision {EXPECTED_CURRENT}; found {current}"
            )
        before_count, before_digest = _protected_metadata(connection)
        print(f"PASS pgvector_server_extension_available={version}")

    environment = os.environ.copy()
    environment["DATABASE_URL"] = _admin_url(settings)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", TARGET_REVISION],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip().splitlines()
        raise RuntimeError(
            "Phase 07 migration failed: "
            + (details[-1] if details else "unknown migration error")
        )

    with _connect(settings) as connection:
        _grant_runtime(connection)
        current = _revision(connection)
        after_count, after_digest = _protected_metadata(connection)
        _verify_phase07(connection)
    if current != TARGET_REVISION:
        raise RuntimeError("configured database did not reach Phase 07")
    if (after_count, after_digest) != (before_count, before_digest):
        raise RuntimeError("protected-schema metadata changed during Phase 07 migration")
    print(f"PASS configured_revision={current}")
    print(f"PASS protected_schema_columns={after_count}")
    print(f"PASS protected_schema_metadata_sha256={after_digest}")
    print("PASS phase07_exact_vector_storage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
