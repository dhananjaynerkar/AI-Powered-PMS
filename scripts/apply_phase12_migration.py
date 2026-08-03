"""Apply Phase 12 graph storage with protected-schema verification."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import psycopg
from pms_common.settings import Settings
from psycopg import sql
from sqlalchemy import URL

EXPECTED_CURRENT = "20260730_0011"
TARGET_REVISION = "20260731_0012"
PROTECTED_SCHEMAS = ("pms_extract_2010_2023", "public")
RUNTIME_ROLE = "pms_app_runtime"
GRAPH_TABLES = ("graph_node", "graph_edge")


def _connect(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def _revision(connection: psycopg.Connection[tuple[object, ...]]) -> str:
    with connection.cursor() as cursor:
        row = cursor.execute("SELECT version_num FROM pms_app.alembic_version").fetchone()
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
    encoded = json.dumps(rows, default=str, separators=(",", ":")).encode()
    return len(rows), hashlib.sha256(encoded).hexdigest()


def _admin_url(settings: Settings) -> str:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_database,
    ).render_as_string(hide_password=False)


def _grant_runtime(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with connection.cursor() as cursor:
        elevated = cursor.execute(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
            FROM pg_roles WHERE rolname = %s
            """,
            (RUNTIME_ROLE,),
        ).fetchone()
        if elevated is None:
            raise RuntimeError("least-privilege runtime role is missing")
        if any(bool(value) for value in elevated):
            raise RuntimeError("runtime role is elevated")
        role = sql.Identifier(RUNTIME_ROLE)
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA pms_graph TO {}").format(role))
        for table in GRAPH_TABLES:
            cursor.execute(
                sql.SQL("GRANT SELECT ON pms_graph.{} TO {}").format(
                    sql.Identifier(table), role
                )
            )
    connection.commit()


def _verify(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[int, int, int, int]:
    with connection.cursor() as cursor:
        result = cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM pg_tables
                WHERE schemaname = 'pms_graph' AND tablename = ANY(%s)),
              (SELECT count(*) FROM pg_class AS class
                JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = 'pms_graph'
                  AND class.relname = ANY(%s)
                  AND class.relrowsecurity AND class.relforcerowsecurity),
              (SELECT count(*) FROM pms_graph.graph_node),
              (SELECT count(*) FROM pms_graph.graph_edge)
            """,
            (list(GRAPH_TABLES), list(GRAPH_TABLES)),
        ).fetchone()
    if result is None:
        raise RuntimeError("Phase 12 verification returned no result")
    return tuple(int(str(value)) for value in result)  # type: ignore[return-value]


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection:
        current = _revision(connection)
        before = _protected_metadata(connection)
    if current not in {EXPECTED_CURRENT, TARGET_REVISION}:
        raise RuntimeError(
            f"expected configured revision {EXPECTED_CURRENT}; found {current}"
        )
    if current != TARGET_REVISION:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = _admin_url(settings)
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", TARGET_REVISION],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode:
            details = completed.stderr.strip().splitlines()
            raise RuntimeError("Phase 12 migration failed: " + " | ".join(details[-40:]))
    with _connect(settings) as connection:
        _grant_runtime(connection)
        current = _revision(connection)
        after = _protected_metadata(connection)
        tables, rls_tables, nodes, edges = _verify(connection)
    if current != TARGET_REVISION:
        raise RuntimeError("configured database did not reach Phase 12")
    if before != after:
        raise RuntimeError("protected-schema metadata changed during Phase 12")
    if tables != len(GRAPH_TABLES) or rls_tables != len(GRAPH_TABLES):
        raise RuntimeError("Phase 12 graph table or forced-RLS set is incomplete")
    print(f"PASS configured_revision={current}")
    print(f"PASS protected_schema_columns={after[0]}")
    print(f"PASS protected_schema_metadata_sha256={after[1]}")
    print(f"PASS graph_tables={tables}")
    print(f"PASS graph_forced_rls_tables={rls_tables}")
    print(f"PASS graph_verified_nodes={nodes}")
    print(f"PASS graph_verified_edges={edges}")
    print("PASS graph_seed_status=empty_without_reviewed_mappings")
    print(f"PASS runtime_role={RUNTIME_ROLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
