"""Apply Phase 10 with protected-schema, least-privilege, and candidate checks."""

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
EXPECTED_CURRENT = "20260730_0008"
TARGET_REVISION = "20260730_0009"
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
        raise RuntimeError("POSTGRES_PASSWORD is required")
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
    encoded = json.dumps(rows, default=str, separators=(",", ":")).encode()
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
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA pms_rules TO {}").format(role))
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON pms_rules.rule_definition TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT, INSERT ON pms_rules.calculation_input, "
                "pms_rules.calculation_result, pms_rules.calculation_segment, "
                "pms_rules.calculation_component TO {}"
            ).format(role)
        )
    connection.commit()


def _import_unapproved_candidates(
    connection: psycopg.Connection[tuple[object, ...]],
) -> int:
    statements = (
        """
        INSERT INTO pms_rules.rule_candidate (
          candidate_id, candidate_family, source_schema, source_table,
          source_record_id, proposed_valid_from, proposed_valid_to,
          raw_payload, imported_by_subject
        )
        SELECT 'extract:rule_property_tax_rates:' || tax_rate_id::text,
               'tax', 'pms_extract_2010_2023', 'rule_property_tax_rates',
               tax_rate_id::text, tax_period_from,
               CASE WHEN tax_period_to > tax_period_from
                    THEN tax_period_to ELSE NULL END,
               to_jsonb(source), 'phase10-bounded-import'
        FROM pms_extract_2010_2023.rule_property_tax_rates AS source
        ON CONFLICT (source_schema, source_table, source_record_id) DO NOTHING
        """,
        """
        INSERT INTO pms_rules.rule_candidate (
          candidate_id, candidate_family, source_schema, source_table,
          source_record_id, proposed_valid_from, proposed_valid_to,
          raw_payload, imported_by_subject
        )
        SELECT 'extract:rule_tax_master:' || tax_id::text,
               'tax', 'pms_extract_2010_2023', 'rule_tax_master',
               tax_id::text, valid_from,
               CASE WHEN valid_upto > valid_from THEN valid_upto ELSE NULL END,
               to_jsonb(source), 'phase10-bounded-import'
        FROM pms_extract_2010_2023.rule_tax_master AS source
        ON CONFLICT (source_schema, source_table, source_record_id) DO NOTHING
        """,
        """
        INSERT INTO pms_rules.rule_candidate (
          candidate_id, candidate_family, source_schema, source_table,
          source_record_id, proposed_valid_from, proposed_valid_to,
          raw_payload, imported_by_subject
        )
        SELECT 'extract:rule_tax_period:' || tax_period_id::text,
               'tax', 'pms_extract_2010_2023', 'rule_tax_period',
               tax_period_id::text, valid_from,
               CASE WHEN valid_upto > valid_from THEN valid_upto ELSE NULL END,
               to_jsonb(source), 'phase10-bounded-import'
        FROM pms_extract_2010_2023.rule_tax_period AS source
        ON CONFLICT (source_schema, source_table, source_record_id) DO NOTHING
        """,
    )
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
        count = cursor.execute(
            """
            SELECT count(*) FROM pms_rules.rule_candidate
            WHERE imported_by_subject = 'phase10-bounded-import'
            """
        ).fetchone()
    connection.commit()
    if count is None:
        raise RuntimeError("candidate count query returned no result")
    return int(str(count[0]))


def _verify(
    connection: psycopg.Connection[tuple[object, ...]],
) -> dict[str, int]:
    with connection.cursor() as cursor:
        counts = cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM pms_rules.rule_candidate) AS candidates,
              (SELECT count(*) FROM pms_rules.rule_definition
                WHERE review_status = 'approved') AS approved_rules,
              (SELECT count(*) FROM pms_rules.gold_case
                WHERE status = 'approved') AS approved_gold_cases,
              (SELECT count(*) FROM pg_tables
                WHERE schemaname = 'pms_rules'
                  AND tablename IN (
                    'rule_candidate', 'rule_definition', 'rule_approval',
                    'calculation_input', 'calculation_result',
                    'calculation_segment', 'calculation_component', 'gold_case'
                  )) AS phase_tables
            """
        ).fetchone()
        elevated = cursor.execute(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
            FROM pg_roles WHERE rolname = %s
            """,
            (RUNTIME_ROLE,),
        ).fetchone()
    if counts is None or elevated is None:
        raise RuntimeError("Phase 10 verification query returned no result")
    if any(bool(value) for value in elevated):
        raise RuntimeError("runtime role is elevated")
    result = {
        "candidates": int(str(counts[0])),
        "approved_rules": int(str(counts[1])),
        "approved_gold_cases": int(str(counts[2])),
        "phase_tables": int(str(counts[3])),
    }
    if result["phase_tables"] != 8:
        raise RuntimeError("Phase 10 table set is incomplete")
    return result


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection:
        current = _revision(connection)
        before = _protected_metadata(connection)
        if current not in {"20260730_0007", EXPECTED_CURRENT, TARGET_REVISION}:
            raise RuntimeError(
                f"expected configured revision {EXPECTED_CURRENT}; found {current}"
            )
    if current != TARGET_REVISION:
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
        if completed.returncode:
            details = completed.stderr.strip().splitlines()
            raise RuntimeError("Phase 10 migration failed: " + " | ".join(details[-40:]))
    with _connect(settings) as connection:
        _grant_runtime(connection)
        imported = _import_unapproved_candidates(connection)
        current = _revision(connection)
        after = _protected_metadata(connection)
        counts = _verify(connection)
    if current != TARGET_REVISION:
        raise RuntimeError("configured database did not reach Phase 10")
    if before != after:
        raise RuntimeError("protected-schema metadata changed during Phase 10")
    print(f"PASS configured_revision={current}")
    print(f"PASS protected_schema_columns={after[0]}")
    print(f"PASS protected_schema_metadata_sha256={after[1]}")
    print(f"PASS bounded_unapproved_candidates={imported}")
    for name, value in counts.items():
        print(f"PASS {name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
