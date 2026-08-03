"""Create immutable Phase 05 document registry and object lineage.

Revision ID: 20260729_0004
Revises: 20260728_0003
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0004"
down_revision: str | None = "20260728_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_doc"
IMMUTABLE_TABLES = ("stored_object", "document_version", "derived_artifact")


def upgrade() -> None:
    """Create only application-owned document registry objects."""

    op.create_table(
        "document_record",
        sa.Column("canonical_document_id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('uploaded', 'quarantined', 'rejected')",
            name="ck_document_record_status",
        ),
        sa.CheckConstraint(
            "current_version >= 0",
            name="ck_document_record_current_version",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_record_status_updated",
        "document_record",
        ["status", "updated_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "stored_object",
        sa.Column("object_id", sa.Text(), primary_key=True),
        sa.Column("bucket_name", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("object_version", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("object_kind", sa.Text(), nullable=False),
        sa.Column("retention_mode", sa.Text(), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="ck_stored_object_size"),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_stored_object_sha256",
        ),
        sa.CheckConstraint(
            "object_kind IN ('original', 'raw_parser', 'canonical_json', 'derived')",
            name="ck_stored_object_kind",
        ),
        sa.CheckConstraint(
            "retention_mode IN ('versioned', 'object_lock')",
            name="ck_stored_object_retention",
        ),
        sa.UniqueConstraint(
            "bucket_name",
            "object_key",
            name="uq_stored_object_bucket_key",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stored_object_checksum",
        "stored_object",
        ["checksum_sha256"],
        schema=SCHEMA,
    )

    op.create_table(
        "document_version",
        sa.Column("version_id", sa.Text(), primary_key=True),
        sa.Column(
            "canonical_document_id",
            sa.Text(),
            sa.ForeignKey(
                f"{SCHEMA}.document_record.canonical_document_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "original_object_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.stored_object.object_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_document_version_number",
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_version_sha256",
        ),
        sa.UniqueConstraint(
            "canonical_document_id",
            "version_number",
            name="uq_document_version_number",
        ),
        sa.UniqueConstraint(
            "canonical_document_id",
            "checksum_sha256",
            name="uq_document_version_checksum",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_version_document_time",
        "document_version",
        ["canonical_document_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "derived_artifact",
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.document_version.version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "object_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.stored_object.object_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("artifact_kind", sa.Text(), nullable=False),
        sa.Column("producer", sa.Text(), nullable=False),
        sa.Column("producer_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_kind IN ('raw_parser', 'canonical_json', 'derived')",
            name="ck_derived_artifact_kind",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_derived_artifact_version_kind",
        "derived_artifact",
        ["document_version_id", "artifact_kind"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION pms_doc.reject_immutable_object_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'document objects and lineage are immutable';
        END;
        $$
        """
    )
    for table_name in IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON pms_doc.{table_name}
            FOR EACH ROW
            EXECUTE FUNCTION pms_doc.reject_immutable_object_change()
            """
        )

    for table_name in (
        "document_record",
        "stored_object",
        "document_version",
        "derived_artifact",
    ):
        op.execute(f"ALTER TABLE pms_doc.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE pms_doc.{table_name} FORCE ROW LEVEL SECURITY")

    writer = """
      pms_app.has_role('Data Entry Operator')
      OR pms_app.has_role('Nodal/Regional Officer')
      OR pms_app.has_role('Estate Officer')
      OR pms_app.has_role('Administrator')
    """
    document_visible = """
      EXISTS (
        SELECT 1
        FROM pms_doc.document_acl AS acl
        WHERE acl.canonical_document_id = document_record.canonical_document_id
      )
    """
    version_visible = """
      EXISTS (
        SELECT 1
        FROM pms_doc.document_record AS document_record
        WHERE document_record.canonical_document_id =
              document_version.canonical_document_id
      )
    """
    derived_visible = """
      EXISTS (
        SELECT 1
        FROM pms_doc.document_version AS document_version
        WHERE document_version.version_id =
              derived_artifact.document_version_id
      )
    """
    object_visible = """
      created_by_subject = current_setting('pms.subject', true)
      OR EXISTS (
        SELECT 1
        FROM pms_doc.document_version AS document_version
        WHERE document_version.original_object_id = stored_object.object_id
      )
      OR EXISTS (
        SELECT 1
        FROM pms_doc.derived_artifact AS derived_artifact
        WHERE derived_artifact.object_id = stored_object.object_id
      )
    """

    op.execute(
        f"CREATE POLICY document_record_select ON pms_doc.document_record "
        f"FOR SELECT USING ({document_visible})"
    )
    op.execute(
        f"CREATE POLICY document_record_insert ON pms_doc.document_record "
        f"FOR INSERT WITH CHECK (({writer}) AND "
        "created_by_subject = current_setting('pms.subject', true) AND "
        f"({document_visible}))"
    )
    op.execute(
        f"CREATE POLICY document_record_update ON pms_doc.document_record "
        f"FOR UPDATE USING (({writer}) AND ({document_visible})) "
        f"WITH CHECK (({writer}) AND ({document_visible}))"
    )
    op.execute(
        f"CREATE POLICY stored_object_select ON pms_doc.stored_object "
        f"FOR SELECT USING ({object_visible})"
    )
    op.execute(
        f"CREATE POLICY stored_object_insert ON pms_doc.stored_object "
        f"FOR INSERT WITH CHECK (({writer}) AND "
        "created_by_subject = current_setting('pms.subject', true))"
    )
    op.execute(
        f"CREATE POLICY document_version_select ON pms_doc.document_version "
        f"FOR SELECT USING ({version_visible})"
    )
    op.execute(
        f"CREATE POLICY document_version_insert ON pms_doc.document_version "
        f"FOR INSERT WITH CHECK (({writer}) AND "
        "created_by_subject = current_setting('pms.subject', true) AND "
        f"({version_visible}))"
    )
    op.execute(
        f"CREATE POLICY derived_artifact_select ON pms_doc.derived_artifact "
        f"FOR SELECT USING ({derived_visible})"
    )
    op.execute(
        f"CREATE POLICY derived_artifact_insert ON pms_doc.derived_artifact "
        f"FOR INSERT WITH CHECK (({writer}) AND ({derived_visible}))"
    )

    op.execute("DROP POLICY document_acl_insert ON pms_doc.document_acl")
    op.execute("DROP POLICY document_acl_update ON pms_doc.document_acl")
    op.execute("DROP POLICY document_acl_delete ON pms_doc.document_acl")
    acl_write = f"""
      ({writer})
      AND (
        canonical_tenant_id IS NULL
        OR canonical_tenant_id =
           NULLIF(current_setting('pms.tenant_id', true), '')
        OR pms_app.has_role('Administrator')
      )
      AND (
        cardinality(allowed_departments) = 0
        OR NULLIF(current_setting('pms.department_id', true), '') =
           ANY(allowed_departments)
        OR pms_app.has_role('Administrator')
      )
    """
    op.execute(
        f"CREATE POLICY document_acl_insert ON pms_doc.document_acl "
        f"FOR INSERT WITH CHECK ({acl_write})"
    )
    op.execute(
        f"CREATE POLICY document_acl_update ON pms_doc.document_acl "
        f"FOR UPDATE USING ({acl_write}) WITH CHECK ({acl_write})"
    )
    op.execute(
        f"CREATE POLICY document_acl_delete ON pms_doc.document_acl "
        f"FOR DELETE USING ({acl_write})"
    )


def downgrade() -> None:
    """Remove only Phase 05 document registry objects."""

    op.execute("DROP POLICY document_acl_delete ON pms_doc.document_acl")
    op.execute("DROP POLICY document_acl_update ON pms_doc.document_acl")
    op.execute("DROP POLICY document_acl_insert ON pms_doc.document_acl")
    old_write = """
      pms_app.has_role('Administrator')
      OR (
        pms_app.has_role('Data Entry Operator')
        AND canonical_tenant_id =
            NULLIF(current_setting('pms.tenant_id', true), '')
      )
    """
    op.execute(
        f"CREATE POLICY document_acl_insert ON pms_doc.document_acl "
        f"FOR INSERT WITH CHECK ({old_write})"
    )
    op.execute(
        f"CREATE POLICY document_acl_update ON pms_doc.document_acl "
        f"FOR UPDATE USING ({old_write}) WITH CHECK ({old_write})"
    )
    op.execute(
        f"CREATE POLICY document_acl_delete ON pms_doc.document_acl "
        f"FOR DELETE USING ({old_write})"
    )

    for table_name in reversed(IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON pms_doc.{table_name}")
    op.execute("DROP FUNCTION pms_doc.reject_immutable_object_change()")
    op.drop_table("derived_artifact", schema=SCHEMA)
    op.drop_table("document_version", schema=SCHEMA)
    op.drop_table("stored_object", schema=SCHEMA)
    op.drop_table("document_record", schema=SCHEMA)
