"""Apply Phase 11 with protected-schema and least-privilege verification."""

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

EXPECTED_CURRENT = "20260730_0009"
TARGET_REVISION = "20260730_0011"
PROTECTED_SCHEMAS = ("pms_extract_2010_2023", "public")
RUNTIME_ROLE = "pms_app_runtime"
TRAINER_ROLE = "pms_forecast_trainer"
PHASE_TABLES = (
    "target_definition",
    "feature_snapshot",
    "fs_revenue_monthly",
    "fs_payment_bill_level",
    "fs_land_value",
    "fs_lease_lifecycle",
    "fs_inspection_risk",
    "model_definition",
    "model_version",
    "training_run",
    "evaluation_result",
    "prediction_feature_snapshot",
    "prediction",
)


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
        cursor.execute(
            sql.SQL("GRANT USAGE ON SCHEMA pms_forecast TO {}").format(role)
        )
        for table in PHASE_TABLES:
            cursor.execute(
                sql.SQL("GRANT SELECT ON pms_forecast.{} TO {}").format(
                    sql.Identifier(table),
                    role,
                )
            )
        for table in ("prediction_feature_snapshot", "prediction"):
            cursor.execute(
                sql.SQL("GRANT INSERT ON pms_forecast.{} TO {}").format(
                    sql.Identifier(table),
                    role,
                )
            )
    connection.commit()


def _grant_trainer(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with connection.cursor() as cursor:
        trainer = sql.Identifier(TRAINER_ROLE)
        if cursor.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (TRAINER_ROLE,),
        ).fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOBYPASSRLS").format(trainer)
            )
        elevated = cursor.execute(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin
            FROM pg_roles WHERE rolname = %s
            """,
            (TRAINER_ROLE,),
        ).fetchone()
        if elevated is None or any(bool(value) for value in elevated):
            raise RuntimeError("forecast trainer role is missing or elevated")
        cursor.execute(
            sql.SQL("GRANT {} TO {}").format(
                trainer,
                sql.Identifier(str(connection.info.user)),
            )
        )
        cursor.execute(
            sql.SQL("GRANT USAGE ON SCHEMA pms_extract_2010_2023 TO {}").format(
                trainer
            )
        )
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON "
                "pms_extract_2010_2023.model_revenue_monthly_by_source TO {}"
            ).format(trainer)
        )
        cursor.execute(
            sql.SQL("GRANT USAGE ON SCHEMA pms_forecast TO {}").format(trainer)
        )
        for table in PHASE_TABLES:
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON pms_forecast.{} TO {}"
                ).format(sql.Identifier(table), trainer)
            )
    connection.commit()


def _verify(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        tables = cursor.execute(
            """
            SELECT count(*) FROM pg_tables
            WHERE schemaname = 'pms_forecast' AND tablename = ANY(%s)
            """,
            (list(PHASE_TABLES),),
        ).fetchone()
        rls = cursor.execute(
            """
            SELECT count(*) FROM pg_class AS class
            JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = 'pms_forecast'
              AND class.relname = ANY(%s)
              AND class.relrowsecurity AND class.relforcerowsecurity
            """,
            (list(PHASE_TABLES),),
        ).fetchone()
        targets = cursor.execute(
            "SELECT count(*) FROM pms_forecast.target_definition"
        ).fetchone()
    if tables is None or rls is None or targets is None:
        raise RuntimeError("Phase 11 verification returned no result")
    return int(str(tables[0])), int(str(rls[0])), int(str(targets[0]))


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection:
        current = _revision(connection)
        before = _protected_metadata(connection)
    if current not in {EXPECTED_CURRENT, "20260730_0010", TARGET_REVISION}:
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
            raise RuntimeError("Phase 11 migration failed: " + " | ".join(details[-40:]))
    with _connect(settings) as connection:
        _grant_runtime(connection)
        _grant_trainer(connection)
        current = _revision(connection)
        after = _protected_metadata(connection)
        tables, rls_tables, targets = _verify(connection)
    if current != TARGET_REVISION:
        raise RuntimeError("configured database did not reach Phase 11")
    if before != after:
        raise RuntimeError("protected-schema metadata changed during Phase 11")
    if tables != len(PHASE_TABLES) or rls_tables != len(PHASE_TABLES):
        raise RuntimeError("Phase 11 table or RLS set is incomplete")
    print(f"PASS configured_revision={current}")
    print(f"PASS protected_schema_columns={after[0]}")
    print(f"PASS protected_schema_metadata_sha256={after[1]}")
    print(f"PASS phase11_tables={tables}")
    print(f"PASS phase11_forced_rls_tables={rls_tables}")
    print(f"PASS target_definitions={targets}")
    print(f"PASS runtime_role={RUNTIME_ROLE}")
    print(f"PASS offline_trainer_role={TRAINER_ROLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
