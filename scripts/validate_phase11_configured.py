"""Validate the configured Phase 11 feature/model gate without promoting a model."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

import psycopg
from pms_common.settings import Settings

TARGET_REVISION = "20260730_0011"
PROTECTED_SCHEMAS = ("pms_extract_2010_2023", "public")
EXPECTED_PROTECTED_HASH = (
    "6e9e7d2ec1fb8d3ddbf9193ab4e79c0c1e06927545d270d752db2a9870f7f442"
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


def _protected_hash(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[int, str]:
    rows = connection.execute(
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


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        revision = connection.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
        if revision is None or str(revision[0]) != TARGET_REVISION:
            raise RuntimeError("configured database is not at the Phase 11 head")
        protected_count, protected_hash = _protected_hash(connection)
        if protected_hash != EXPECTED_PROTECTED_HASH:
            raise RuntimeError("protected-schema metadata fingerprint changed")
        snapshot = connection.execute(
            """
            SELECT feature_snapshot_id, row_count, feature_hash, leakage_status,
                   quality_flags, data_cutoff
            FROM pms_forecast.feature_snapshot
            WHERE target_name = 'monthly_cash_collection'
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        if snapshot is None:
            raise RuntimeError("monthly cash collection snapshot is missing")
        actual_rows = connection.execute(
            """
            SELECT count(*), max(observation_date),
                   bool_and(observation_date < data_cutoff::date),
                   count(DISTINCT entity_id)
            FROM pms_forecast.fs_revenue_monthly
            WHERE feature_snapshot_id = %s
            """,
            (snapshot[0],),
        ).fetchone()
        evaluations = connection.execute(
            """
            SELECT definition.algorithm, version.model_version_id,
                   version.approval_status, evaluation.metrics,
                   evaluation.interval_coverage,
                   evaluation.baseline_comparison,
                   evaluation.leakage_check_passed
            FROM pms_forecast.training_run AS run
            JOIN pms_forecast.evaluation_result AS evaluation
              ON evaluation.training_run_id = run.training_run_id
            JOIN pms_forecast.model_version AS version
              ON version.model_version_id = evaluation.model_version_id
            JOIN pms_forecast.model_definition AS definition
              ON definition.model_name = version.model_name
            WHERE run.training_run_id = (
              SELECT training_run_id FROM pms_forecast.training_run
              WHERE target_name = 'monthly_cash_collection'
              ORDER BY completed_at DESC LIMIT 1
            )
            ORDER BY definition.algorithm
            """
        ).fetchall()
        predictions = connection.execute(
            """
            SELECT count(*), count(DISTINCT prediction.entity_id),
                   count(DISTINCT prediction.model_version_id),
                   count(DISTINCT feature.feature_snapshot_id),
                   bool_and(prediction.lower_95 <= prediction.lower_80
                     AND prediction.lower_80 <= prediction.point_estimate
                     AND prediction.point_estimate <= prediction.upper_80
                     AND prediction.upper_80 <= prediction.upper_95),
                   bool_and(prediction.review_status = 'candidate')
            FROM pms_forecast.prediction AS prediction
            JOIN pms_forecast.prediction_feature_snapshot AS feature
              ON feature.prediction_feature_snapshot_id =
                 prediction.prediction_feature_snapshot_id
            """
        ).fetchone()
        champions = connection.execute(
            """
            SELECT count(*) FROM pms_forecast.model_version
            WHERE target_name = 'monthly_cash_collection'
              AND approval_status = 'champion'
            """
        ).fetchone()
        trainer_role = connection.execute(
            """
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
            FROM pg_roles WHERE rolname = 'pms_forecast_trainer'
            """
        ).fetchone()
    with _connect(settings) as connection, connection.transaction():
        connection.execute("SET LOCAL ROLE pms_app_runtime")
        unscoped_rows = connection.execute(
            "SELECT count(*) FROM pms_forecast.prediction"
        ).fetchone()
        connection.execute(
            "SELECT set_config('pms.roles', 'Finance Officer', true)"
        )
        scoped_rows = connection.execute(
            "SELECT count(*) FROM pms_forecast.prediction"
        ).fetchone()
    if actual_rows is None or predictions is None or champions is None:
        raise RuntimeError("configured Phase 11 validation returned no result")
    if trainer_role is None or any(bool(value) for value in trainer_role):
        raise RuntimeError("offline trainer role is missing or elevated")
    if unscoped_rows is None or scoped_rows is None:
        raise RuntimeError("runtime RLS check returned no result")
    if int(str(unscoped_rows[0])) != 0:
        raise RuntimeError("unscoped runtime role could read forecast predictions")
    if int(str(scoped_rows[0])) != int(str(predictions[0])):
        raise RuntimeError("authorized Finance scope could not read predictions")
    if int(str(actual_rows[0])) != int(str(snapshot[1])) or not bool(actual_rows[2]):
        raise RuntimeError("persisted feature snapshot failed its cutoff contract")
    algorithms = {str(row[0]) for row in evaluations}
    if algorithms != {"ets", "seasonal_naive"}:
        raise RuntimeError("baseline/challenger evaluation is incomplete")
    if not all(bool(row[6]) for row in evaluations):
        raise RuntimeError("a model evaluation failed the leakage check")
    if (
        int(str(predictions[0])) != 24
        or not bool(predictions[4])
        or not bool(predictions[5])
    ):
        raise RuntimeError("candidate prediction persistence is incomplete")
    print(f"PASS configured_revision={revision[0]}")
    print(f"PASS protected_schema_columns={protected_count}")
    print(f"PASS protected_schema_metadata_sha256={protected_hash}")
    print(f"PASS feature_snapshot={snapshot[0]}")
    print(f"PASS feature_rows={actual_rows[0]}")
    print(f"PASS feature_entities={actual_rows[3]}")
    print(f"PASS max_observation_date={actual_rows[1]}")
    print(f"PASS rolling_models={','.join(sorted(algorithms))}")
    for algorithm, _, _, metrics, coverage, comparison, _ in evaluations:
        metric_values = cast(dict[str, Any], metrics)
        coverage_values = cast(dict[str, Any], coverage)
        comparison_values = cast(dict[str, Any], comparison)
        print(
            "PASS "
            f"{algorithm}_wape={float(metric_values['wape']):.6f} "
            "coverage95="
            f"{float(coverage_values['lower_95_upper_95']):.6f} "
            f"beats_baseline={comparison_values['strictly_beats_baseline']}"
        )
    print(f"PASS candidate_predictions={predictions[0]}")
    print(f"PASS prediction_entities={predictions[1]}")
    print(f"PASS prediction_model_versions={predictions[2]}")
    print(f"PASS prediction_source_snapshots={predictions[3]}")
    print("PASS offline_trainer_role_non_login_non_elevated=true")
    print("PASS runtime_unscoped_prediction_rows=0")
    print(f"PASS finance_scoped_prediction_rows={scoped_rows[0]}")
    print(f"PENDING promoted_champions={champions[0]}")
    print("PENDING explicit promotion is required and current accuracy is weak")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
