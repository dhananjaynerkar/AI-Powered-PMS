"""Create the seven Phase 03 application schemas.

Revision ID: 20260728_0001
Revises:
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMAS = (
    "pms_app",
    "pms_doc",
    "pms_vector",
    "pms_rules",
    "pms_forecast",
    "pms_graph",
    "pms_audit",
)


def upgrade() -> None:
    """Create only the approved application-owned schemas."""

    for schema_name in APPLICATION_SCHEMAS:
        op.execute(sa.schema.CreateSchema(schema_name, if_not_exists=True))


def downgrade() -> None:
    """Remove empty app schemas while retaining the Alembic version-table schema."""

    for schema_name in reversed(APPLICATION_SCHEMAS[1:]):
        op.execute(sa.schema.DropSchema(schema_name, if_exists=True))
