"""Add current chat ownership and auditable DO/NO/HOD handoffs.

This migration is intentionally limited to the existing ``pms_chat`` schema.
It does not modify public business data or any document/RAG tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0018"
down_revision: str | None = "20260806_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_chat"
RUNTIME_ROLE = "pms_app_runtime"


def upgrade() -> None:
    op.add_column(
        "chat",
        sa.Column("current_owner_subject", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"UPDATE {SCHEMA}.chat SET current_owner_subject = owner_subject "
        "WHERE current_owner_subject IS NULL"
    )
    op.alter_column(
        "chat",
        "current_owner_subject",
        nullable=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_current_owner_status",
        "chat",
        ["current_owner_subject", "status", "updated_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "chat_handoff_event",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.chat.chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("recipient_subject", sa.Text(), nullable=False),
        sa.Column("recipient_admin_id", sa.Integer(), nullable=True),
        sa.Column("recipient_role", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(btrim(remarks)) > 0", name="ck_chat_handoff_remarks"),
        sa.CheckConstraint(
            "action IN ('SUBMIT_TO_NO', 'RETURN_TO_DO', 'FORWARD_TO_HOD', 'SHARE')",
            name="ck_chat_handoff_action",
        ),
        sa.UniqueConstraint(
            "chat_id",
            "actor_subject",
            "recipient_subject",
            "action",
            "created_at",
            name="uq_chat_handoff_event_identity",
        ),
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.chat_handoff_event ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.chat_handoff_event FORCE ROW LEVEL SECURITY")
    op.create_index(
        "ix_chat_handoff_event_chat_created",
        "chat_handoff_event",
        ["chat_id", "created_at"],
        schema=SCHEMA,
    )

    op.execute(f"DROP POLICY IF EXISTS chat_participant_insert ON {SCHEMA}.chat_participant")
    op.execute(
        f"CREATE POLICY chat_participant_insert ON {SCHEMA}.chat_participant "
        "FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM pms_chat.chat c "
        "WHERE c.chat_id = pms_chat.chat_participant.chat_id "
        "AND (pms_app.has_role('Administrator') OR c.owner_subject = "
        "current_setting('pms.subject', true) OR c.current_owner_subject = "
        "current_setting('pms.subject', true))) "
        "AND added_by_subject = current_setting('pms.subject', true))"
    )
    op.execute(f"DROP POLICY IF EXISTS chat_participant_update ON {SCHEMA}.chat_participant")
    op.execute(
        f"CREATE POLICY chat_participant_update ON {SCHEMA}.chat_participant "
        "FOR UPDATE USING (EXISTS (SELECT 1 FROM pms_chat.chat c "
        "WHERE c.chat_id = pms_chat.chat_participant.chat_id "
        "AND (pms_app.has_role('Administrator') OR c.owner_subject = "
        "current_setting('pms.subject', true) OR c.current_owner_subject = "
        "current_setting('pms.subject', true)))) "
        "WITH CHECK (EXISTS (SELECT 1 FROM pms_chat.chat c "
        "WHERE c.chat_id = pms_chat.chat_participant.chat_id "
        "AND (pms_app.has_role('Administrator') OR c.owner_subject = "
        "current_setting('pms.subject', true) OR c.current_owner_subject = "
        "current_setting('pms.subject', true))))"
    )

    op.execute(
        f"CREATE POLICY chat_handoff_event_select ON {SCHEMA}.chat_handoff_event "
        "FOR SELECT USING (EXISTS (SELECT 1 FROM pms_chat.chat c WHERE c.chat_id = "
        "pms_chat.chat_handoff_event.chat_id AND (pms_app.has_role('Administrator') "
        "OR pms_app.has_role('Auditor') OR c.owner_subject = current_setting('pms.subject', true) "
        "OR c.current_owner_subject = current_setting('pms.subject', true) "
        "OR pms_chat.is_chat_participant(c.chat_id, current_setting('pms.subject', true)))))"
    )
    op.execute(
        f"CREATE POLICY chat_handoff_event_insert ON {SCHEMA}.chat_handoff_event "
        "FOR INSERT WITH CHECK (actor_subject = current_setting('pms.subject', true) "
        "AND EXISTS (SELECT 1 FROM pms_chat.chat c WHERE c.chat_id = "
        "pms_chat.chat_handoff_event.chat_id AND (pms_app.has_role('Administrator') "
        "OR c.owner_subject = current_setting('pms.subject', true) "
        "OR c.current_owner_subject = current_setting('pms.subject', true))))"
    )
    op.execute(f"GRANT SELECT, INSERT ON {SCHEMA}.chat_handoff_event TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON {SCHEMA}.chat_handoff_event FROM {RUNTIME_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS chat_handoff_event_insert ON {SCHEMA}.chat_handoff_event")
    op.execute(f"DROP POLICY IF EXISTS chat_handoff_event_select ON {SCHEMA}.chat_handoff_event")
    op.execute(f"DROP POLICY IF EXISTS chat_participant_insert ON {SCHEMA}.chat_participant")
    op.execute(f"DROP POLICY IF EXISTS chat_participant_update ON {SCHEMA}.chat_participant")
    op.execute(
        f"CREATE POLICY chat_participant_insert ON {SCHEMA}.chat_participant "
        "FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM pms_chat.chat c "
        "WHERE c.chat_id = pms_chat.chat_participant.chat_id "
        "AND (pms_app.has_role('Administrator') OR c.owner_subject = "
        "current_setting('pms.subject', true))) "
        "AND added_by_subject = current_setting('pms.subject', true))"
    )
    op.execute(
        f"CREATE POLICY chat_participant_update ON {SCHEMA}.chat_participant "
        "FOR UPDATE USING (EXISTS (SELECT 1 FROM pms_chat.chat c "
        "WHERE c.chat_id = pms_chat.chat_participant.chat_id "
        "AND (pms_app.has_role('Administrator') OR c.owner_subject = "
        "current_setting('pms.subject', true)))) "
        "WITH CHECK (EXISTS (SELECT 1 FROM pms_chat.chat c "
        "WHERE c.chat_id = pms_chat.chat_participant.chat_id "
        "AND (pms_app.has_role('Administrator') OR c.owner_subject = "
        "current_setting('pms.subject', true))))"
    )
    op.drop_index(
        "ix_chat_handoff_event_chat_created",
        table_name="chat_handoff_event",
        schema=SCHEMA,
    )
    op.drop_table("chat_handoff_event", schema=SCHEMA)
    op.drop_index("ix_chat_current_owner_status", table_name="chat", schema=SCHEMA)
    op.drop_column("chat", "current_owner_subject", schema=SCHEMA)
