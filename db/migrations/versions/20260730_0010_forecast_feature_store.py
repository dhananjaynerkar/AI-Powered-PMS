"""Create the governed Phase 11 feature store and model registry.

Revision ID: 20260730_0010
Revises: 20260730_0009
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0010"
down_revision: str | None = "20260730_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FEATURE_TABLES = (
    "fs_revenue_monthly",
    "fs_payment_bill_level",
    "fs_land_value",
    "fs_lease_lifecycle",
    "fs_inspection_risk",
)


def _reader() -> str:
    return """
      pms_app.has_role('Nodal/Regional Officer')
      OR pms_app.has_role('Finance Officer')
      OR pms_app.has_role('Estate Officer')
      OR pms_app.has_role('HOD')
      OR pms_app.has_role('Auditor')
      OR pms_app.has_role('Administrator')
    """


def _writer() -> str:
    return """
      pms_app.has_role('Finance Officer')
      OR pms_app.has_role('Administrator')
    """


def upgrade() -> None:
    """Create application-owned forecast objects without touching source schemas."""

    op.execute(
        """
        CREATE TABLE pms_forecast.target_definition (
          target_name text PRIMARY KEY,
          description text NOT NULL,
          unit text NOT NULL,
          frequency text NOT NULL,
          entity_level text NOT NULL,
          business_owner text NOT NULL,
          definition_status text NOT NULL
            CHECK (definition_status IN ('approved', 'blocked')),
          blocked_reason text,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (
            (definition_status = 'approved' AND blocked_reason IS NULL)
            OR (definition_status = 'blocked' AND blocked_reason IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        INSERT INTO pms_forecast.target_definition (
          target_name, description, unit, frequency, entity_level,
          business_owner, definition_status, blocked_reason
        ) VALUES
          (
            'monthly_cash_collection',
            'Cash receipts recorded during the calendar month',
            'INR', 'monthly',
            'division x estate x charge category',
            'Finance', 'approved', NULL
          ),
          (
            'monthly_billed_revenue',
            'Approved billed amount during the calendar month',
            'INR', 'monthly',
            'division x estate x charge category',
            'Finance', 'blocked',
            'legacy billed totals and required dimensional mappings are incomplete'
          ),
          (
            'monthly_net_realized_revenue',
            'Finance-defined net realized revenue',
            'INR', 'monthly',
            'division x estate x charge category',
            'Finance', 'blocked',
            'Finance has not supplied an authoritative target definition'
          ),
          (
            'payment_delay_days',
            'Days from contractual due date to receipt date',
            'days', 'bill event', 'bill',
            'Finance', 'blocked',
            'the extracted payment history has no populated due dates'
          ),
          (
            'fair_market_value_per_sqm',
            'Observed fair market value per square metre',
            'INR/sqm', 'event', 'plot',
            'Estate', 'blocked',
            'the configured extract has zero land-value observations'
          ),
          (
            'inspection_breach_risk',
            'Probability that a point-in-time inspection results in a verified breach',
            'probability', 'inspection event', 'inspection',
            'Estate', 'blocked',
            'the configured extract has zero verified breach labels'
          )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_forecast.feature_snapshot (
          feature_snapshot_id text PRIMARY KEY,
          target_name text NOT NULL
            REFERENCES pms_forecast.target_definition(target_name),
          feature_generation_version text NOT NULL,
          data_cutoff timestamptz NOT NULL,
          source_schema text NOT NULL,
          source_tables jsonb NOT NULL,
          row_count bigint NOT NULL CHECK (row_count >= 0),
          feature_hash character(64) NOT NULL,
          leakage_status text NOT NULL
            CHECK (leakage_status IN ('safe', 'blocked')),
          quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
          created_by_subject text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (target_name, feature_generation_version, data_cutoff, feature_hash)
        )
        """
    )
    for table in FEATURE_TABLES:
        op.execute(
            f"""
            CREATE TABLE pms_forecast.{table} (
              feature_row_id text PRIMARY KEY,
              feature_snapshot_id text NOT NULL
                REFERENCES pms_forecast.feature_snapshot(feature_snapshot_id)
                ON DELETE RESTRICT,
              observation_date date NOT NULL,
              target_name text NOT NULL
                REFERENCES pms_forecast.target_definition(target_name),
              entity_id text NOT NULL,
              entity_ids jsonb NOT NULL,
              source_record_ids jsonb NOT NULL,
              target_value numeric,
              features jsonb NOT NULL,
              feature_generation_version text NOT NULL,
              data_cutoff timestamptz NOT NULL,
              leakage_safe_status text NOT NULL
                CHECK (leakage_safe_status IN ('safe', 'blocked')),
              quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
              feature_hash character(64) NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              CHECK (observation_date < data_cutoff::date)
            )
            """
        )
        op.execute(
            f"""
            CREATE INDEX ix_{table}_target_date
            ON pms_forecast.{table}(target_name, observation_date, entity_id)
            """
        )

    op.execute(
        """
        CREATE TABLE pms_forecast.model_definition (
          model_name text PRIMARY KEY,
          algorithm text NOT NULL,
          target_name text NOT NULL
            REFERENCES pms_forecast.target_definition(target_name),
          is_baseline boolean NOT NULL,
          implementation_version text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (model_name, target_name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_forecast.model_version (
          model_version_id text PRIMARY KEY,
          model_name text NOT NULL,
          target_name text NOT NULL,
          feature_snapshot_id text NOT NULL
            REFERENCES pms_forecast.feature_snapshot(feature_snapshot_id),
          training_period_from date NOT NULL,
          training_period_to date NOT NULL,
          training_data_cutoff timestamptz NOT NULL,
          feature_hash character(64) NOT NULL,
          parameters jsonb NOT NULL,
          artifact_hash character(64) NOT NULL,
          approval_status text NOT NULL DEFAULT 'candidate'
            CHECK (approval_status IN ('candidate', 'champion', 'retired', 'rejected')),
          approved_by text,
          approved_at timestamptz,
          limitations jsonb NOT NULL DEFAULT '[]'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (model_name, target_name)
            REFERENCES pms_forecast.model_definition(model_name, target_name),
          CHECK (training_period_to >= training_period_from),
          CHECK (
            (approval_status = 'champion' AND approved_by IS NOT NULL
              AND approved_at IS NOT NULL)
            OR approval_status <> 'champion'
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_one_champion_per_target
        ON pms_forecast.model_version (target_name)
        WHERE approval_status = 'champion'
        """
    )
    op.execute(
        """
        CREATE TABLE pms_forecast.training_run (
          training_run_id text PRIMARY KEY,
          target_name text NOT NULL
            REFERENCES pms_forecast.target_definition(target_name),
          feature_snapshot_id text NOT NULL
            REFERENCES pms_forecast.feature_snapshot(feature_snapshot_id),
          requested_models jsonb NOT NULL,
          status text NOT NULL
            CHECK (status IN ('running', 'completed', 'failed')),
          selected_model_version_id text
            REFERENCES pms_forecast.model_version(model_version_id),
          selection_rule text,
          started_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          created_by_subject text NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_forecast.evaluation_result (
          evaluation_result_id text PRIMARY KEY,
          training_run_id text NOT NULL
            REFERENCES pms_forecast.training_run(training_run_id),
          model_version_id text NOT NULL
            REFERENCES pms_forecast.model_version(model_version_id),
          split_strategy text NOT NULL,
          fold_count integer NOT NULL CHECK (fold_count > 0),
          horizon_months integer NOT NULL CHECK (horizon_months > 0),
          metrics jsonb NOT NULL,
          interval_coverage jsonb NOT NULL,
          baseline_comparison jsonb NOT NULL,
          leakage_check_passed boolean NOT NULL,
          evaluated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (training_run_id, model_version_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_forecast.prediction_feature_snapshot (
          prediction_feature_snapshot_id text PRIMARY KEY,
          feature_snapshot_id text NOT NULL
            REFERENCES pms_forecast.feature_snapshot(feature_snapshot_id),
          entity_id text NOT NULL,
          forecast_origin date NOT NULL,
          features jsonb NOT NULL,
          feature_hash character(64) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_forecast.prediction (
          prediction_id text PRIMARY KEY,
          target_name text NOT NULL
            REFERENCES pms_forecast.target_definition(target_name),
          entity_id text NOT NULL,
          tenant_id text,
          unit_id text,
          department_id text,
          forecast_date date NOT NULL,
          forecast_horizon integer NOT NULL CHECK (forecast_horizon > 0),
          point_estimate numeric NOT NULL,
          lower_80 numeric NOT NULL,
          upper_80 numeric NOT NULL,
          lower_95 numeric NOT NULL,
          upper_95 numeric NOT NULL,
          model_version_id text NOT NULL
            REFERENCES pms_forecast.model_version(model_version_id),
          prediction_feature_snapshot_id text NOT NULL
            REFERENCES pms_forecast.prediction_feature_snapshot(
              prediction_feature_snapshot_id
            ),
          assumptions jsonb NOT NULL DEFAULT '[]'::jsonb,
          review_status text NOT NULL DEFAULT 'candidate'
            CHECK (review_status IN ('candidate', 'approved', 'expired')),
          expires_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (
            target_name, entity_id, forecast_date, model_version_id,
            prediction_feature_snapshot_id
          ),
          CHECK (lower_95 <= lower_80),
          CHECK (lower_80 <= point_estimate),
          CHECK (point_estimate <= upper_80),
          CHECK (upper_80 <= upper_95)
        )
        """
    )

    for table in (
        "target_definition",
        "feature_snapshot",
        *FEATURE_TABLES,
        "model_definition",
        "model_version",
        "training_run",
        "evaluation_result",
        "prediction_feature_snapshot",
        "prediction",
    ):
        op.execute(f"ALTER TABLE pms_forecast.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE pms_forecast.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_select ON pms_forecast.{table}
            FOR SELECT USING ({_reader()})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_write ON pms_forecast.{table}
            FOR ALL USING ({_writer()})
            WITH CHECK ({_writer()})
            """
        )


def downgrade() -> None:
    """Remove only Phase 11 application-owned objects."""

    for table in (
        "prediction",
        "prediction_feature_snapshot",
        "evaluation_result",
        "training_run",
        "model_version",
        "model_definition",
        *reversed(FEATURE_TABLES),
        "feature_snapshot",
        "target_definition",
    ):
        op.execute(f"DROP TABLE IF EXISTS pms_forecast.{table}")
