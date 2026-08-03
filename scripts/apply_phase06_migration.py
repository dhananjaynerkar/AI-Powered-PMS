"""Apply only the approved Phase 06 revision with protected-schema checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from pms_common.settings import Settings
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CURRENT = "20260729_0004"
TARGET_REVISION = "20260729_0005"
PROTECTED_SCHEMAS = ("pms_extract_2010_2023", "public")


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


def _status_constraint(
    connection: psycopg.Connection[tuple[object, ...]],
) -> str:
    with connection.cursor() as cursor:
        row = cursor.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = 'pms_doc'::regnamespace
              AND conrelid = 'pms_doc.document_record'::regclass
              AND conname = 'ck_document_record_status'
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("document status constraint is missing")
    return str(row[0])


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection:
        current = _revision(connection)
        if current == TARGET_REVISION:
            print(f"PASS configured_revision={TARGET_REVISION} already_applied=true")
            return 0
        if current != EXPECTED_CURRENT:
            raise RuntimeError(
                f"expected configured revision {EXPECTED_CURRENT}; found {current}"
            )
        before_count, before_digest = _protected_metadata(connection)

    environment = os.environ.copy()
    environment["DATABASE_URL"] = _admin_url(settings)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", TARGET_REVISION],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip().splitlines()
        raise RuntimeError(
            "Phase 06 migration failed: "
            + (details[-1] if details else "unknown migration error")
        )

    with _connect(settings) as connection:
        current = _revision(connection)
        after_count, after_digest = _protected_metadata(connection)
        constraint = _status_constraint(connection)
    if current != TARGET_REVISION:
        raise RuntimeError("configured database did not reach the Phase 06 revision")
    if (after_count, after_digest) != (before_count, before_digest):
        raise RuntimeError("protected-schema metadata changed during Phase 06 migration")
    if "canonicalized" not in constraint or "review_required" not in constraint:
        raise RuntimeError("Phase 06 document statuses are absent")

    print(f"PASS configured_revision={current}")
    print(f"PASS protected_schema_columns={after_count}")
    print(f"PASS protected_schema_metadata_sha256={after_digest}")
    print("PASS phase06_document_status_constraint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
