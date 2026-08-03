"""Expand document status values for the Phase 06 parsing state machine.

Revision ID: 20260729_0005
Revises: 20260729_0004
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260729_0005"
down_revision: str | None = "20260729_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_doc"
TABLE = "document_record"
CONSTRAINT = "ck_document_record_status"

PHASE_06_STATUSES = (
    "uploaded",
    "quarantined",
    "rejected",
    "parsing",
    "parsed",
    "quality_failed",
    "review_required",
    "canonicalized",
    "chunk_ready",
    "indexed",
    "failed",
)


def _status_check(statuses: tuple[str, ...]) -> str:
    values = ", ".join(f"'{status}'" for status in statuses)
    return f"status IN ({values})"


def upgrade() -> None:
    """Expand only the application-owned document status constraint."""

    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        _status_check(PHASE_06_STATUSES),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Restore the Phase 05 state set without deleting document lineage."""

    op.execute(
        """
        UPDATE pms_doc.document_record
        SET status = CASE
          WHEN status IN ('quality_failed', 'review_required', 'failed')
            THEN 'rejected'
          WHEN status NOT IN ('uploaded', 'quarantined', 'rejected')
            THEN 'uploaded'
          ELSE status
        END
        """
    )
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        _status_check(("uploaded", "quarantined", "rejected")),
        schema=SCHEMA,
    )
