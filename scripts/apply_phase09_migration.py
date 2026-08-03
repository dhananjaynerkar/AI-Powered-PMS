"""Apply Phase 09 with protected-schema and catalog-completeness checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from pms_common.settings import Settings
from pms_retrieval.embedding import BgeM3EmbeddingAdapter
from pms_structured.catalog import SemanticCatalogBuilder
from pms_structured.cli import GOVERNED_VIEWS
from psycopg import sql
from sqlalchemy import URL, create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CURRENT = "20260729_0006"
TARGET_REVISION = "20260730_0007"
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


def _grant_runtime(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with connection.cursor() as cursor:
        if cursor.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (RUNTIME_ROLE,),
        ).fetchone() is None:
            raise RuntimeError("least-privilege runtime role is missing")
        role = sql.Identifier(RUNTIME_ROLE)
        cursor.execute(
            sql.SQL("GRANT USAGE ON SCHEMA pms_catalog, pms_app TO {}").format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON "
                "pms_catalog.entity_identity_map, "
                "pms_catalog.approved_semantic_table, "
                "pms_catalog.approved_semantic_column, "
                "pms_catalog.approved_join_path TO {}"
            ).format(role)
        )
        views = sql.SQL(", ").join(
            sql.Identifier("pms_app", view_name) for view_name in GOVERNED_VIEWS
        )
        cursor.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(views, role)
        )
    connection.commit()


def _refresh_catalog(settings: Settings) -> None:
    engine = create_engine(_admin_url(settings), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            result = SemanticCatalogBuilder(
                extract_schema=settings.extract_schema,
                governed_views=GOVERNED_VIEWS,
            ).refresh(
                connection,
                embedder=BgeM3EmbeddingAdapter(settings),
            )
    finally:
        engine.dispose()
    if result.extracted_tables != 61 or result.extracted_columns != 1010:
        raise RuntimeError(
            "semantic catalog does not match the verified 61-table/1010-column extract"
        )
    if result.governed_views != len(GOVERNED_VIEWS):
        raise RuntimeError("governed view catalog is incomplete")
    if result.metadata_embeddings != 61 + len(GOVERNED_VIEWS):
        raise RuntimeError("semantic table metadata embeddings are incomplete")


def _verify(
    connection: psycopg.Connection[tuple[object, ...]],
) -> dict[str, int]:
    with connection.cursor() as cursor:
        values = cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM pms_catalog.semantic_table
                WHERE table_kind = 'extracted_table') AS extracted_tables,
              (SELECT count(*) FROM pms_catalog.semantic_column
                WHERE source_schema = 'pms_extract_2010_2023') AS extracted_columns,
              (SELECT count(*) FROM pms_catalog.semantic_table
                WHERE table_kind = 'governed_view') AS governed_views,
              (SELECT count(*) FROM pms_catalog.semantic_table
                WHERE embedding IS NOT NULL) AS embeddings,
              (SELECT count(*) FROM pms_catalog.join_path
                WHERE approved) AS approved_joins,
              (SELECT count(*) FROM information_schema.views
                WHERE table_schema = 'pms_app'
                  AND table_name = ANY(%s)) AS installed_views
            """,
            (list(GOVERNED_VIEWS),),
        ).fetchone()
        elevated = cursor.execute(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
            FROM pg_roles WHERE rolname = %s
            """,
            (RUNTIME_ROLE,),
        ).fetchone()
    if values is None or elevated is None:
        raise RuntimeError("Phase 09 verification query returned no result")
    counts = {
        "extracted_tables": int(str(values[0])),
        "extracted_columns": int(str(values[1])),
        "governed_views": int(str(values[2])),
        "embeddings": int(str(values[3])),
        "approved_joins": int(str(values[4])),
        "installed_views": int(str(values[5])),
    }
    expected = {
        "extracted_tables": 61,
        "extracted_columns": 1010,
        "governed_views": len(GOVERNED_VIEWS),
        "embeddings": 61 + len(GOVERNED_VIEWS),
        "installed_views": len(GOVERNED_VIEWS),
    }
    for name, expected_value in expected.items():
        if counts[name] != expected_value:
            raise RuntimeError(f"Phase 09 verification failed for {name}")
    if any(bool(value) for value in elevated):
        raise RuntimeError("runtime role is elevated")
    return counts


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection:
        current = _revision(connection)
        before_count, before_digest = _protected_metadata(connection)
        if current not in {EXPECTED_CURRENT, TARGET_REVISION}:
            raise RuntimeError(
                f"expected configured revision {EXPECTED_CURRENT}; found {current}"
            )

    if current == EXPECTED_CURRENT:
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
                "Phase 09 migration failed: "
                + (
                    " | ".join(details[-40:])
                    if details
                    else "unknown migration error"
                )
            )

    with _connect(settings) as connection:
        _grant_runtime(connection)
    _refresh_catalog(settings)
    with _connect(settings) as connection:
        current = _revision(connection)
        after_count, after_digest = _protected_metadata(connection)
        counts = _verify(connection)
    if current != TARGET_REVISION:
        raise RuntimeError("configured database did not reach Phase 09")
    if (after_count, after_digest) != (before_count, before_digest):
        raise RuntimeError("protected-schema metadata changed during Phase 09")
    print(f"PASS configured_revision={current}")
    print(f"PASS protected_schema_columns={after_count}")
    print(f"PASS protected_schema_metadata_sha256={after_digest}")
    for name, value in counts.items():
        print(f"PASS {name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
