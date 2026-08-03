"""Add read-only, non-sensitive semantic views for the localhost demo.

Revision ID: 20260803_0015
Revises: 20260803_0014
Create Date: 2026-08-03

The views expose only the reviewed operational subset used by the controlled
local demonstration. They add no role, grant, public-schema, or base-table
privilege. Application code queries only these views through a validated plan.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0015"
down_revision: str | None = "20260803_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_app"
RUNTIME_ROLE = "pms_app_runtime"

VIEWS: dict[str, str] = {
    "semantic_division_reference": """
        CREATE VIEW pms_app.semantic_division_reference (
            div_code, div_name, status, source_refreshed_at
        ) WITH (security_barrier = true, security_invoker = false) AS
        SELECT division.div_code, division.div_name, division.status,
               (SELECT max(config.created_at)
                FROM pms_extract_2010_2023.extract_config AS config)
        FROM pms_extract_2010_2023.dim_division AS division
    """,
    "semantic_estate_reference": """
        CREATE VIEW pms_app.semantic_estate_reference (
            estate_code, estate_name, status, source_refreshed_at
        ) WITH (security_barrier = true, security_invoker = false) AS
        SELECT estate.estate_code, estate.estate_name, estate.status,
               (SELECT max(config.created_at)
                FROM pms_extract_2010_2023.extract_config AS config)
        FROM pms_extract_2010_2023.dim_estate AS estate
    """,
    "semantic_unit_reference": """
        CREATE VIEW pms_app.semantic_unit_reference (
            unit_code, unit_desc, status, source_refreshed_at
        ) WITH (security_barrier = true, security_invoker = false) AS
        SELECT unit_source.unit_code, unit_source.unit_desc, unit_source.status,
               (SELECT max(config.created_at)
                FROM pms_extract_2010_2023.extract_config AS config)
        FROM pms_extract_2010_2023.dim_unit AS unit_source
    """,
    "semantic_plot_summary": """
        CREATE VIEW pms_app.semantic_plot_summary (
            plot_code, area, status, is_vacant, zone_id, source_refreshed_at
        ) WITH (security_barrier = true, security_invoker = false) AS
        SELECT plot.plot_code, plot.area, plot.status, plot.is_vacant, plot.zone_id,
               (SELECT max(config.created_at)
                FROM pms_extract_2010_2023.extract_config AS config)
        FROM pms_extract_2010_2023.dim_plot AS plot
    """,
    "semantic_approved_lease_summary": """
        CREATE VIEW pms_app.semantic_approved_lease_summary (
            tenancy_type, lease_type_id, bill_periodicity, duration_from, duration_to,
            renewal_date, is_renewable, status, source_refreshed_at
        ) WITH (security_barrier = true, security_invoker = false) AS
        SELECT lease.tenancy_type, lease.lease_type_id, lease.bill_periodicity,
               lease.duration_from, lease.duration_to, lease.renewal_date,
               lease.is_renewable, lease.status,
               (SELECT max(config.created_at)
                FROM pms_extract_2010_2023.extract_config AS config)
        FROM pms_extract_2010_2023.dim_property_lease AS lease
        WHERE lease.status = 'APPROVED'
    """,
    "semantic_recent_bill_summary": """
        CREATE VIEW pms_app.semantic_recent_bill_summary (
            bill_date, due_date, bill_status, source_refreshed_at
        ) WITH (security_barrier = true, security_invoker = false) AS
        SELECT bill.bill_date, bill.due_date, bill.bill_status,
               (SELECT max(config.created_at)
                FROM pms_extract_2010_2023.extract_config AS config)
        FROM pms_extract_2010_2023.fact_monthly_bills AS bill
        WHERE bill.bill_status = 'A'
    """,
}


def upgrade() -> None:
    """Create the reviewed views and grant SELECT only to the app runtime role."""

    for statement in VIEWS.values():
        op.execute(statement)
    for view_name in VIEWS:
        op.execute(f"GRANT SELECT ON {SCHEMA}.{view_name} TO {RUNTIME_ROLE}")


def downgrade() -> None:
    """Remove only the six views created by this migration."""

    for view_name in reversed(tuple(VIEWS)):
        op.execute(f"DROP VIEW {SCHEMA}.{view_name}")
