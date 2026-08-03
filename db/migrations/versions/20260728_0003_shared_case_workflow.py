"""Create Phase 04A shared case workflow, context and RLS objects.

Revision ID: 20260728_0003
Revises: 20260728_0002
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0003"
down_revision: str | None = "20260728_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_chat"
ROLES = (
    "Tenant",
    "Data Entry Operator",
    "Nodal/Regional Officer",
    "Finance Officer",
    "Estate Officer",
    "Legal Officer",
    "HOD",
    "Auditor",
    "Administrator",
)
WORKFLOW_STATES = (
    "draft",
    "submitted_to_no",
    "returned_to_do",
    "verified_by_no",
    "submitted_to_hod",
    "returned_to_no",
    "approved",
    "rejected",
    "escalated",
    "closed",
)
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


def _values(items: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in items)


def _check(column: str, items: tuple[str, ...], name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} IN ({_values(items)})",
        name=name,
    )


def upgrade() -> None:
    """Create application-owned case workflow and context state."""

    op.execute(sa.schema.CreateSchema(SCHEMA, if_not_exists=True))

    op.create_table(
        "case_record",
        sa.Column("case_id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_by_role", sa.Text(), nullable=False),
        sa.Column("current_owner_subject", sa.Text(), nullable=False),
        sa.Column("current_owner_role", sa.Text(), nullable=False),
        sa.Column("participant_subjects", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("department_id", sa.Text(), nullable=False),
        sa.Column("unit_id", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _check("state", WORKFLOW_STATES, "ck_case_record_state"),
        _check("created_by_role", ROLES, "ck_case_record_creator_role"),
        _check("current_owner_role", ROLES, "ck_case_record_owner_role"),
        _check(
            "classification",
            CLASSIFICATIONS,
            "ck_case_record_classification",
        ),
        sa.CheckConstraint("cardinality(participant_subjects) > 0", "ck_case_participants"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_case_record_queue",
        "case_record",
        ["current_owner_subject", "state", "updated_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_case_record_scope",
        "case_record",
        ["department_id", "unit_id", "classification"],
        schema=SCHEMA,
    )

    op.create_table(
        "case_thread",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("next_sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "next_sequence_number >= 1",
            name="ck_case_thread_positive_sequence",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "case_participant",
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _check("role", ROLES, "ck_case_participant_role"),
        sa.PrimaryKeyConstraint("case_id", "subject", name="pk_case_participant"),
        schema=SCHEMA,
    )
    op.create_table(
        "case_assignment",
        sa.Column("assignment_id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assigned_subject", sa.Text(), nullable=False),
        sa.Column("assigned_role", sa.Text(), nullable=False),
        sa.Column("assigned_by_subject", sa.Text(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _check("assigned_role", ROLES, "ck_case_assignment_role"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= assigned_at",
            name="ck_case_assignment_period",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_case_assignment_active",
        "case_assignment",
        ["case_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "case_transition",
        sa.Column("transition_id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("assigned_subject", sa.Text(), nullable=False),
        sa.Column("assigned_role", sa.Text(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _check("from_state", WORKFLOW_STATES, "ck_case_transition_from_state"),
        _check("to_state", WORKFLOW_STATES, "ck_case_transition_to_state"),
        _check("actor_role", ROLES, "ck_case_transition_actor_role"),
        _check("assigned_role", ROLES, "ck_case_transition_assigned_role"),
        sa.CheckConstraint("length(btrim(remarks)) > 0", "ck_case_transition_remarks"),
        schema=SCHEMA,
    )
    op.create_table(
        "case_task",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("assigned_subject", sa.Text(), nullable=False),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _check("status", ("open", "completed", "cancelled"), "ck_case_task_status"),
        schema=SCHEMA,
    )
    op.create_table(
        "case_decision",
        sa.Column("decision_id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        _check("actor_role", ROLES, "ck_case_decision_actor_role"),
        schema=SCHEMA,
    )
    op.create_table(
        "case_message",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_thread.thread_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("author_subject", sa.Text(), nullable=False),
        sa.Column("author_role", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "supersedes_message_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_message.message_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _check("author_role", ROLES, "ck_case_message_author_role"),
        sa.CheckConstraint("sequence_number >= 1", "ck_case_message_sequence"),
        sa.CheckConstraint("length(btrim(body)) > 0", "ck_case_message_body"),
        sa.UniqueConstraint(
            "thread_id",
            "sequence_number",
            name="uq_case_message_thread_sequence",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "message_attachment",
        sa.Column("attachment_id", sa.Text(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_message.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("artifact_version >= 1", "ck_attachment_version"),
        schema=SCHEMA,
    )
    op.create_table(
        "message_read_receipt",
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_message.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "message_id",
            "subject",
            name="pk_message_read_receipt",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "message_reference",
        sa.Column("reference_id", sa.Text(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_message.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reference_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "case_artifact_version",
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_message.message_id"),
            nullable=False,
        ),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", "ck_case_artifact_version"),
        sa.PrimaryKeyConstraint(
            "case_id",
            "artifact_id",
            "version",
            name="pk_case_artifact_version",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "case_rolling_summary",
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_subject", sa.Text(), nullable=False),
        sa.CheckConstraint("version >= 1", "ck_case_summary_version"),
        sa.PrimaryKeyConstraint("case_id", "version", name="pk_case_rolling_summary"),
        schema=SCHEMA,
    )
    op.create_table(
        "context_capsule",
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("current_state", sa.Text(), nullable=False),
        sa.Column("current_owner_subject", sa.Text(), nullable=False),
        sa.Column("rolling_summary", sa.Text(), nullable=False),
        sa.Column("verified_facts", postgresql.JSONB(), nullable=False),
        sa.Column("unresolved_issues", postgresql.JSONB(), nullable=False),
        sa.Column("decisions", postgresql.JSONB(), nullable=False),
        sa.Column("open_tasks", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("artifact_versions", postgresql.JSONB(), nullable=False),
        sa.Column("required_next_action", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _check("current_state", WORKFLOW_STATES, "ck_context_capsule_state"),
        sa.CheckConstraint("version >= 1", "ck_context_capsule_version"),
        sa.CheckConstraint("length(state_hash) = 64", "ck_context_capsule_hash"),
        sa.PrimaryKeyConstraint("case_id", "version", name="pk_context_capsule"),
        schema=SCHEMA,
    )
    op.create_table(
        "delegated_authority",
        sa.Column("authority_id", sa.Text(), primary_key=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("department_id", sa.Text(), nullable=False),
        sa.Column("unit_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("approved_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _check("action", ("approve",), "ck_delegated_authority_action"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_delegated_authority_period",
        ),
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION pms_chat.reject_message_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'case messages are immutable; create a superseding message';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_message_immutable
        BEFORE UPDATE OR DELETE ON pms_chat.case_message
        FOR EACH ROW EXECUTE FUNCTION pms_chat.reject_message_mutation()
        """
    )

    tables = (
        "case_record",
        "case_thread",
        "case_participant",
        "case_assignment",
        "case_transition",
        "case_task",
        "case_decision",
        "case_message",
        "message_attachment",
        "message_read_receipt",
        "message_reference",
        "case_artifact_version",
        "case_rolling_summary",
        "context_capsule",
        "delegated_authority",
    )
    for table in tables:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")

    case_scope = """
      (
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
    op.execute(
        f"CREATE POLICY case_record_select ON {SCHEMA}.case_record "
        f"FOR SELECT USING ({case_scope})"
    )
    op.execute(
        f"""
        CREATE POLICY case_record_insert ON {SCHEMA}.case_record
        FOR INSERT WITH CHECK (
          (pms_app.has_role('Data Entry Operator') OR pms_app.has_role('Administrator'))
          AND created_by_subject = current_setting('pms.subject', true)
          AND current_owner_subject = current_setting('pms.subject', true)
          AND department_id = NULLIF(current_setting('pms.department_id', true), '')
          AND unit_id = NULLIF(current_setting('pms.unit_id', true), '')
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY case_record_update ON {SCHEMA}.case_record
        FOR UPDATE USING (
          current_owner_subject = current_setting('pms.subject', true)
          OR pms_app.has_role('Administrator')
        )
        WITH CHECK (
          current_setting('pms.subject', true) = ANY(participant_subjects)
          OR pms_app.has_role('Administrator')
        )
        """
    )

    child_case_ids = {
        "case_thread": "case_id",
        "case_participant": "case_id",
        "case_assignment": "case_id",
        "case_transition": "case_id",
        "case_task": "case_id",
        "case_decision": "case_id",
        "case_artifact_version": "case_id",
        "case_rolling_summary": "case_id",
        "context_capsule": "case_id",
    }
    for table, column in child_case_ids.items():
        visible = (
            f"EXISTS (SELECT 1 FROM {SCHEMA}.case_record c "
            f"WHERE c.case_id = {SCHEMA}.{table}.{column})"
        )
        writable = (
            f"EXISTS (SELECT 1 FROM {SCHEMA}.case_record c "
            f"WHERE c.case_id = {SCHEMA}.{table}.{column} "
            "AND (c.current_owner_subject = current_setting('pms.subject', true) "
            "OR pms_app.has_role('Administrator')))"
        )
        op.execute(
            f"CREATE POLICY {table}_select ON {SCHEMA}.{table} "
            f"FOR SELECT USING ({visible})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {SCHEMA}.{table} "
            f"FOR INSERT WITH CHECK ({visible if table == 'context_capsule' else writable})"
        )
        if table in {
            "case_thread",
            "case_participant",
            "case_assignment",
            "case_task",
            "case_rolling_summary",
        }:
            op.execute(
                f"CREATE POLICY {table}_update ON {SCHEMA}.{table} "
                f"FOR UPDATE USING ({writable}) WITH CHECK ({writable})"
            )

    message_case = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.case_thread t "
        f"JOIN {SCHEMA}.case_record c ON c.case_id = t.case_id "
        f"WHERE t.thread_id = {SCHEMA}.case_message.thread_id)"
    )
    message_write = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.case_thread t "
        f"JOIN {SCHEMA}.case_record c ON c.case_id = t.case_id "
        f"WHERE t.thread_id = {SCHEMA}.case_message.thread_id "
        "AND (c.current_owner_subject = current_setting('pms.subject', true) "
        "OR pms_app.has_role('Administrator')))"
    )
    op.execute(
        f"CREATE POLICY case_message_select ON {SCHEMA}.case_message "
        f"FOR SELECT USING ({message_case})"
    )
    op.execute(
        f"CREATE POLICY case_message_insert ON {SCHEMA}.case_message "
        f"FOR INSERT WITH CHECK ({message_write} "
        "AND author_subject = current_setting('pms.subject', true))"
    )

    for table in ("message_attachment", "message_read_receipt", "message_reference"):
        visible = (
            f"EXISTS (SELECT 1 FROM {SCHEMA}.case_message m "
            f"WHERE m.message_id = {SCHEMA}.{table}.message_id)"
        )
        op.execute(
            f"CREATE POLICY {table}_select ON {SCHEMA}.{table} "
            f"FOR SELECT USING ({visible})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {SCHEMA}.{table} "
            f"FOR INSERT WITH CHECK ({visible})"
        )

    op.execute(
        f"""
        CREATE POLICY delegated_authority_select ON {SCHEMA}.delegated_authority
        FOR SELECT USING (
          subject = current_setting('pms.subject', true)
          OR pms_app.has_role('Auditor')
          OR pms_app.has_role('Administrator')
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY delegated_authority_admin_write ON {SCHEMA}.delegated_authority
        FOR ALL USING (pms_app.has_role('Administrator'))
        WITH CHECK (pms_app.has_role('Administrator'))
        """
    )


def downgrade() -> None:
    """Remove only the Phase 04A application-owned schema."""

    op.execute("DROP TRIGGER case_message_immutable ON pms_chat.case_message")
    op.execute("DROP FUNCTION pms_chat.reject_message_mutation()")
    for table in (
        "delegated_authority",
        "context_capsule",
        "case_rolling_summary",
        "case_artifact_version",
        "message_reference",
        "message_read_receipt",
        "message_attachment",
        "case_message",
        "case_decision",
        "case_task",
        "case_transition",
        "case_assignment",
        "case_participant",
        "case_thread",
        "case_record",
    ):
        op.drop_table(table, schema=SCHEMA)
    op.execute(sa.schema.DropSchema(SCHEMA, if_exists=True))
