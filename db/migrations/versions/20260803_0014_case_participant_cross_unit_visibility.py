"""Preserve shared-case visibility for explicitly assigned cross-unit staff.

Revision ID: 20260803_0014
Revises: 20260801_0013
Create Date: 2026-08-03

Only the existing ``pms_chat.case_record`` SELECT RLS policy is changed.  The
policy remains department- and classification-scoped and grants no base-data,
schema, role, or write privilege.  A cross-unit reader must already be listed
as a participant on that individual case.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0014"
down_revision: str | None = "20260801_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_chat"
POLICY = "case_record_select"


def _case_visibility_predicate() -> str:
    """Build the fixed policy predicate; no user-controlled SQL is interpolated."""

    return """
      (
        pms_app.has_role('Administrator')
        OR pms_app.has_role('Auditor')
        OR (
          current_setting('pms.subject', true) = ANY(participant_subjects)
          AND department_id = NULLIF(current_setting('pms.department_id', true), '')
          AND pms_app.classification_rank(
            COALESCE(current_setting('pms.classification', true), 'public')
          ) >= pms_app.classification_rank(classification)
        )
      )
    """


def upgrade() -> None:
    """Allow an assigned participant to retain same-case visibility across units."""

    op.execute(f"DROP POLICY {POLICY} ON {SCHEMA}.case_record")
    op.execute(
        f"CREATE POLICY {POLICY} ON {SCHEMA}.case_record FOR SELECT "
        f"USING ({_case_visibility_predicate()})"
    )


def downgrade() -> None:
    """Restore the original strict same-unit shared-case visibility policy."""

    op.execute(f"DROP POLICY {POLICY} ON {SCHEMA}.case_record")
    op.execute(
        f"""
        CREATE POLICY {POLICY} ON {SCHEMA}.case_record FOR SELECT USING (
          pms_app.has_role('Administrator')
          OR pms_app.has_role('Auditor')
          OR (
            current_setting('pms.subject', true) = ANY(participant_subjects)
            AND department_id = NULLIF(current_setting('pms.department_id', true), '')
            AND unit_id = NULLIF(current_setting('pms.unit_id', true), '')
            AND pms_app.classification_rank(
              COALESCE(current_setting('pms.classification', true), 'public')
            ) >= pms_app.classification_rank(classification)
          )
        )
        """
    )
