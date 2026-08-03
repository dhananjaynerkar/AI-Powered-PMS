"""Allow only the dedicated offline trainer role to manage forecast artifacts.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0011"
down_revision: str | None = "20260730_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
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


def _application_writer() -> str:
    return """
      pms_app.has_role('Finance Officer')
      OR pms_app.has_role('Administrator')
    """


def _trainer_writer() -> str:
    return f"""
      current_user = 'pms_forecast_trainer'
      OR {_application_writer()}
    """


def _replace_policy(table: str, expression: str) -> None:
    op.execute(f"DROP POLICY {table}_write ON pms_forecast.{table}")
    op.execute(
        f"""
        CREATE POLICY {table}_write ON pms_forecast.{table}
        FOR ALL USING ({expression})
        WITH CHECK ({expression})
        """
    )


def upgrade() -> None:
    """Authorize the bounded offline trainer without expanding runtime access."""

    for table in TABLES:
        _replace_policy(table, _trainer_writer())


def downgrade() -> None:
    """Restore the original Finance/Administrator application policy."""

    for table in TABLES:
        _replace_policy(table, _application_writer())
