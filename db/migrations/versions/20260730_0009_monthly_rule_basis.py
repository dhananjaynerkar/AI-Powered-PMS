"""Add explicit full-calendar-month rule bases discovered in PDF evidence.

Revision ID: 20260730_0009
Revises: 20260730_0008
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0009"
down_revision: str | None = "20260730_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Support published monthly rates without guessing partial-month proration."""

    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        ADD COLUMN proration_method text NOT NULL
          DEFAULT 'actual_days_half_open'
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        ADD CONSTRAINT ck_rule_definition_proration CHECK (
          proration_method IN (
            'actual_days_half_open', 'full_calendar_months'
          )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        DROP CONSTRAINT ck_rule_definition_basis
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        ADD CONSTRAINT ck_rule_definition_basis CHECK (
          calculation_basis IN (
            'fixed_per_day', 'per_area_per_day',
            'fixed_per_month', 'per_area_per_month',
            'percent_of_base', 'percent_of_taxable'
          )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.calculation_component
        ADD COLUMN proration_method text NOT NULL
          DEFAULT 'actual_days_half_open'
        """
    )


def downgrade() -> None:
    """Remove only the Phase 10 monthly-basis extension."""

    op.execute(
        """
        ALTER TABLE pms_rules.calculation_component
        DROP COLUMN proration_method
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        DROP CONSTRAINT ck_rule_definition_basis
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        ADD CONSTRAINT ck_rule_definition_basis CHECK (
          calculation_basis IN (
            'fixed_per_day', 'per_area_per_day',
            'percent_of_base', 'percent_of_taxable'
          )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        DROP CONSTRAINT ck_rule_definition_proration
        """
    )
    op.execute(
        """
        ALTER TABLE pms_rules.rule_definition
        DROP COLUMN proration_method
        """
    )
