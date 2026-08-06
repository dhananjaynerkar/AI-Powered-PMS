"""Create the database-authoritative persistent chat workspace.

The existing ``case_*`` tables model the DO/NO/HOD workflow and deliberately
keep workflow messages immutable.  They cannot represent personal chats,
assistant lifecycle states, or an attachment that is still being indexed, so
this migration adds a separate chat aggregate in the existing ``pms_chat``
schema.  A chat may still link to a case for the shared-case workflow.

No public business tables are changed by this migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_0016"
down_revision: str | None = "20260803_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_chat"
RUNTIME_ROLE = "pms_app_runtime"

CHAT_TYPES = ("PERSONAL", "SHARED_CASE")
CHAT_STATUSES = ("ACTIVE", "ARCHIVED", "CLOSED")
ACCESS_MODES = ("OWNER", "WRITE", "READ")
MESSAGE_ROLES = ("user", "assistant", "system")
MESSAGE_STATUSES = ("pending", "streaming", "completed", "failed", "cancelled")
INGESTION_STATUSES = (
    "UPLOADED",
    "PARSING",
    "CHUNKING",
    "EMBEDDING",
    "READY",
    "FAILED",
    "REVIEW_REQUIRED",
    "CANCELLED",
)
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


def _check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    rendered = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({rendered})", name=name)


def upgrade() -> None:
    """Create chat persistence, indexes, least-privilege grants and RLS."""

    op.execute(sa.schema.CreateSchema(SCHEMA, if_not_exists=True))

    op.create_table(
        "chat",
        sa.Column("chat_id", sa.Text(), primary_key=True),
        sa.Column("owner_subject", sa.Text(), nullable=False),
        sa.Column(
            "owner_admin_id",
            sa.Integer(),
            sa.ForeignKey("public.admin_users.admin_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("chat_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.case_record.case_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        _check("chat_type", CHAT_TYPES, "ck_chat_type"),
        _check("status", CHAT_STATUSES, "ck_chat_status"),
        sa.CheckConstraint(
            "(chat_type = 'PERSONAL' AND case_id IS NULL) "
            "OR (chat_type = 'SHARED_CASE' AND case_id IS NOT NULL)",
            name="ck_chat_case_link",
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_chat_title"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_owner_status_updated",
        "chat",
        ["owner_subject", "status", "updated_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_case_status",
        "chat",
        ["case_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "chat_participant",
        sa.Column(
            "chat_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.chat.chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("participant_subject", sa.Text(), nullable=False),
        sa.Column(
            "participant_admin_id",
            sa.Integer(),
            sa.ForeignKey("public.admin_users.admin_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("participant_role", sa.Text(), nullable=False),
        sa.Column("access_mode", sa.Text(), nullable=False),
        sa.Column("added_by_subject", sa.Text(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        _check("access_mode", ACCESS_MODES, "ck_chat_participant_access_mode"),
        sa.CheckConstraint(
            "removed_at IS NULL OR removed_at >= added_at",
            name="ck_chat_participant_period",
        ),
        sa.PrimaryKeyConstraint("chat_id", "participant_subject", name="pk_chat_participant"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_participant_subject_active",
        "chat_participant",
        ["participant_subject", "removed_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "chat_message",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.chat.chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("sender_subject", sa.Text(), nullable=False),
        sa.Column(
            "sender_admin_id",
            sa.Integer(),
            sa.ForeignKey("public.admin_users.admin_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("message_role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        _check("message_role", MESSAGE_ROLES, "ck_chat_message_role"),
        _check("message_status", MESSAGE_STATUSES, "ck_chat_message_status"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_chat_message_sequence"),
        sa.CheckConstraint("length(btrim(content)) > 0", name="ck_chat_message_content"),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="ck_chat_message_completion_time",
        ),
        sa.UniqueConstraint(
            "chat_id",
            "sequence_number",
            name="uq_chat_message_chat_sequence",
        ),
        sa.UniqueConstraint(
            "chat_id",
            "idempotency_key",
            name="uq_chat_message_idempotency",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_message_chat_sequence",
        "chat_message",
        ["chat_id", "sequence_number"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_message_chat_status",
        "chat_message",
        ["chat_id", "message_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "chat_message_citation",
        sa.Column("citation_id", sa.Text(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.chat_message.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column(
            "canonical_document_id",
            sa.Text(),
            sa.ForeignKey("pms_doc.document_record.canonical_document_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Text(),
            sa.ForeignKey("pms_doc.document_version.version_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Text(), nullable=True),
        sa.Column("section_number", sa.Text(), nullable=True),
        sa.Column("clause_number", sa.Text(), nullable=True),
        sa.Column("bounding_box", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page_number >= 1", name="ck_chat_citation_page"),
        sa.UniqueConstraint(
            "message_id",
            "source_id",
            "canonical_document_id",
            "document_version_id",
            "page_number",
            "clause_number",
            name="uq_chat_message_citation",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_citation_message",
        "chat_message_citation",
        ["message_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "chat_attachment",
        sa.Column("attachment_id", sa.Text(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.chat.chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uploaded_by_subject", sa.Text(), nullable=False),
        sa.Column(
            "uploaded_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("public.admin_users.admin_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "canonical_document_id",
            sa.Text(),
            sa.ForeignKey("pms_doc.document_record.canonical_document_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("ingestion_status", sa.Text(), nullable=False, server_default="UPLOADED"),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        _check("ingestion_status", INGESTION_STATUSES, "ck_chat_attachment_status"),
        _check("classification", CLASSIFICATIONS, "ck_chat_attachment_classification"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_chat_attachment_size"),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-fA-F]{64}$'",
            name="ck_chat_attachment_checksum",
        ),
        sa.CheckConstraint(
            "ready_at IS NULL OR canonical_document_id IS NOT NULL",
            name="ck_chat_attachment_ready_document",
        ),
        sa.UniqueConstraint(
            "chat_id",
            "checksum_sha256",
            name="uq_chat_attachment_checksum",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_attachment_chat_status",
        "chat_attachment",
        ["chat_id", "ingestion_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "chat_memory",
        sa.Column(
            "chat_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.chat.chat_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_summarized_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("summary_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "last_summarized_sequence >= 0",
            name="ck_chat_memory_sequence",
        ),
        sa.CheckConstraint("summary_version >= 1", name="ck_chat_memory_version"),
        schema=SCHEMA,
    )

    for table in (
        "chat",
        "chat_participant",
        "chat_message",
        "chat_message_citation",
        "chat_attachment",
        "chat_memory",
    ):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.is_chat_participant(target_chat_id text, target_subject text)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SCHEMA}, pg_catalog
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM {SCHEMA}.chat_participant
            WHERE chat_id = target_chat_id
              AND participant_subject = target_subject
              AND removed_at IS NULL
          )
        $$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.is_chat_participant(text, text) FROM PUBLIC"
    )

    admin = "pms_app.has_role('Administrator')"
    auditor = "pms_app.has_role('Auditor')"
    subject = "current_setting('pms.subject', true)"
    visible_chat = (
        f"({admin} OR {auditor} OR {SCHEMA}.chat.owner_subject = {subject} "
        f"OR {SCHEMA}.is_chat_participant({SCHEMA}.chat.chat_id, {subject}))"
    )
    writable_chat = (
        f"({admin} OR {SCHEMA}.chat.owner_subject = {subject} "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.chat_participant p "
        f"WHERE p.chat_id = {SCHEMA}.chat.chat_id "
        f"AND p.participant_subject = {subject} "
        "AND p.removed_at IS NULL AND p.access_mode IN ('OWNER', 'WRITE')))"
    )
    op.execute(f"CREATE POLICY chat_select ON {SCHEMA}.chat FOR SELECT USING ({visible_chat})")
    op.execute(
        f"CREATE POLICY chat_insert ON {SCHEMA}.chat FOR INSERT WITH CHECK "
        f"(owner_subject = {subject})"
    )
    op.execute(
        f"CREATE POLICY chat_update ON {SCHEMA}.chat FOR UPDATE USING ({writable_chat}) "
        f"WITH CHECK ({writable_chat})"
    )

    participant_visible = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.chat c "
        f"WHERE c.chat_id = {SCHEMA}.chat_participant.chat_id AND "
        f"{visible_chat.replace('pms_chat.chat.', 'c.')})"
    )
    participant_write = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.chat c "
        f"WHERE c.chat_id = {SCHEMA}.chat_participant.chat_id "
        f"AND ({admin} OR c.owner_subject = {subject}))"
    )
    op.execute(
        f"CREATE POLICY chat_participant_select ON {SCHEMA}.chat_participant "
        f"FOR SELECT USING ({participant_visible})"
    )
    op.execute(
        f"CREATE POLICY chat_participant_insert ON {SCHEMA}.chat_participant "
        f"FOR INSERT WITH CHECK ({participant_write} AND added_by_subject = {subject})"
    )
    op.execute(
        f"CREATE POLICY chat_participant_update ON {SCHEMA}.chat_participant "
        f"FOR UPDATE USING ({participant_write}) WITH CHECK ({participant_write})"
    )

    child_tables = {
        "chat_attachment": "chat_id",
        "chat_memory": "chat_id",
    }
    for table, column in child_tables.items():
        visible = (
            f"EXISTS (SELECT 1 FROM {SCHEMA}.chat c "
            f"WHERE c.chat_id = {SCHEMA}.{table}.{column} AND "
            f"{visible_chat.replace('pms_chat.chat.', 'c.')})"
        )
        writable = (
            f"EXISTS (SELECT 1 FROM {SCHEMA}.chat c "
            f"WHERE c.chat_id = {SCHEMA}.{table}.{column} "
            f"AND ({admin} OR c.owner_subject = {subject} OR EXISTS "
            f"(SELECT 1 FROM {SCHEMA}.chat_participant p WHERE p.chat_id = c.chat_id "
            f"AND p.participant_subject = {subject} AND p.removed_at IS NULL "
            "AND p.access_mode IN ('OWNER', 'WRITE'))))"
        )
        op.execute(
            f"CREATE POLICY {table}_select ON {SCHEMA}.{table} FOR SELECT USING ({visible})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {SCHEMA}.{table} FOR INSERT WITH CHECK ({writable}"
            f"{' AND uploaded_by_subject = ' + subject if table == 'chat_attachment' else ''})"
        )
        op.execute(
            f"CREATE POLICY {table}_update ON {SCHEMA}.{table} FOR UPDATE USING ({writable}) "
            f"WITH CHECK ({writable})"
        )

    message_visible = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.chat c "
        f"WHERE c.chat_id = {SCHEMA}.chat_message.chat_id AND "
        f"{visible_chat.replace('pms_chat.chat.', 'c.')})"
    )
    message_writable = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.chat c "
        f"WHERE c.chat_id = {SCHEMA}.chat_message.chat_id "
        f"AND ({admin} OR c.owner_subject = {subject} OR EXISTS "
        f"(SELECT 1 FROM {SCHEMA}.chat_participant p WHERE p.chat_id = c.chat_id "
        f"AND p.participant_subject = {subject} AND p.removed_at IS NULL "
        "AND p.access_mode IN ('OWNER', 'WRITE'))))"
    )
    op.execute(
        f"CREATE POLICY chat_message_select ON {SCHEMA}.chat_message "
        f"FOR SELECT USING ({message_visible})"
    )
    op.execute(
        f"CREATE POLICY chat_message_insert ON {SCHEMA}.chat_message "
        f"FOR INSERT WITH CHECK ({message_writable} AND "
        f"(message_role <> 'user' OR sender_subject = {subject}))"
    )
    op.execute(
        f"CREATE POLICY chat_message_update ON {SCHEMA}.chat_message "
        f"FOR UPDATE USING ({message_writable}) WITH CHECK ({message_writable})"
    )

    citation_visible = (
        f"EXISTS (SELECT 1 FROM {SCHEMA}.chat_message m JOIN {SCHEMA}.chat c "
        f"ON c.chat_id = m.chat_id WHERE m.message_id = {SCHEMA}.chat_message_citation.message_id "
        f"AND {visible_chat.replace('pms_chat.chat.', 'c.')})"
    )
    op.execute(
        f"CREATE POLICY chat_citation_select ON {SCHEMA}.chat_message_citation "
        f"FOR SELECT USING ({citation_visible})"
    )
    op.execute(
        f"CREATE POLICY chat_citation_insert ON {SCHEMA}.chat_message_citation "
        f"FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM {SCHEMA}.chat_message m "
        f"JOIN {SCHEMA}.chat c ON c.chat_id = m.chat_id "
        f"WHERE m.message_id = {SCHEMA}.chat_message_citation.message_id "
        f"AND ({admin} OR c.owner_subject = {subject} OR EXISTS "
        f"(SELECT 1 FROM {SCHEMA}.chat_participant p "
        f"WHERE p.chat_id = c.chat_id AND p.participant_subject = {subject} "
        "AND p.removed_at IS NULL AND p.access_mode IN ('OWNER', 'WRITE')))))"
    )

    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {SCHEMA}.is_chat_participant(text, text) "
        f"TO {RUNTIME_ROLE}"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.chat, {SCHEMA}.chat_participant, "
        f"{SCHEMA}.chat_message, {SCHEMA}.chat_attachment, {SCHEMA}.chat_memory "
        f"TO {RUNTIME_ROLE}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON {SCHEMA}.chat_message_citation TO {RUNTIME_ROLE}"
    )


def downgrade() -> None:
    """Drop only objects created by this migration."""

    op.execute(
        f"REVOKE EXECUTE ON FUNCTION {SCHEMA}.is_chat_participant(text, text) "
        f"FROM {RUNTIME_ROLE}"
    )
    op.execute(f"REVOKE ALL ON {SCHEMA}.chat_message_citation FROM {RUNTIME_ROLE}")
    op.execute(
        f"REVOKE ALL ON {SCHEMA}.chat, {SCHEMA}.chat_participant, {SCHEMA}.chat_message, "
        f"{SCHEMA}.chat_attachment, {SCHEMA}.chat_memory FROM {RUNTIME_ROLE}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA {SCHEMA} FROM {RUNTIME_ROLE}")
    op.execute(f"DROP FUNCTION {SCHEMA}.is_chat_participant(text, text)")
    for table in (
        "chat_memory",
        "chat_attachment",
        "chat_message_citation",
        "chat_message",
        "chat_participant",
        "chat",
    ):
        op.drop_table(table, schema=SCHEMA)
