"""Create secure Phase 07 chunks, exact vectors, FTS and checkpoints.

Revision ID: 20260729_0006
Revises: 20260729_0005
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0006"
down_revision: str | None = "20260729_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_vector"
PHASE_07_STATUSES = (
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
    "deactivated",
    "failed",
)


def _writer_roles() -> str:
    return """
      pms_app.has_role('Data Entry Operator')
      OR pms_app.has_role('Nodal/Regional Officer')
      OR pms_app.has_role('Estate Officer')
    """


def _document_write_scope(document_expression: str) -> str:
    return f"""
      pms_app.has_role('Administrator')
      OR (
        ({_writer_roles()})
        AND EXISTS (
          SELECT 1
          FROM pms_doc.document_acl AS document_acl
          WHERE document_acl.canonical_document_id = {document_expression}
        )
      )
    """


def _embedding_write_scope() -> str:
    return f"""
      pms_app.has_role('Administrator')
      OR (
        ({_writer_roles()})
        AND EXISTS (
          SELECT 1
          FROM pms_vector.document_chunk AS chunk
          WHERE chunk.chunk_id = chunk_embedding.chunk_id
        )
      )
    """


def upgrade() -> None:
    """Create only application-owned Phase 07 storage and policies."""

    op.execute(
        """
        DO $$
        DECLARE
          extension_schema text;
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_available_extensions WHERE name = 'vector'
          ) THEN
            RAISE EXCEPTION
              'Phase 07 requires the pgvector server extension'
              USING ERRCODE = '0A000';
          END IF;

          SELECT n.nspname
          INTO extension_schema
          FROM pg_extension AS e
          JOIN pg_namespace AS n ON n.oid = e.extnamespace
          WHERE e.extname = 'vector';

          IF extension_schema IS NOT NULL AND extension_schema <> 'pms_vector' THEN
            RAISE EXCEPTION
              'pgvector must be installed in pms_vector, found in %',
              extension_schema
              USING ERRCODE = '0A000';
          END IF;
        END
        $$;
        """
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA pms_vector")
    op.drop_constraint(
        "ck_document_record_status",
        "document_record",
        schema="pms_doc",
        type_="check",
    )
    status_values = ", ".join(f"'{status}'" for status in PHASE_07_STATUSES)
    op.create_check_constraint(
        "ck_document_record_status",
        "document_record",
        f"status IN ({status_values})",
        schema="pms_doc",
    )

    for policy in ("chunk_acl_delete", "chunk_acl_update", "chunk_acl_insert"):
        op.execute(f"DROP POLICY {policy} ON pms_vector.chunk_acl")
    chunk_acl_write_scope = """
      pms_app.has_role('Administrator')
      OR (
        pms_app.has_role('Data Entry Operator')
        AND canonical_tenant_id IS NOT DISTINCT FROM
          NULLIF(current_setting('pms.tenant_id', true), '')
      )
    """
    op.execute(
        f"""
        CREATE POLICY chunk_acl_insert ON pms_vector.chunk_acl
        FOR INSERT WITH CHECK ({chunk_acl_write_scope})
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunk_acl_update ON pms_vector.chunk_acl
        FOR UPDATE USING ({chunk_acl_write_scope})
        WITH CHECK ({chunk_acl_write_scope})
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunk_acl_delete ON pms_vector.chunk_acl
        FOR DELETE USING ({chunk_acl_write_scope})
        """
    )

    op.create_table(
        "document_chunk",
        sa.Column("chunk_id", sa.Text(), primary_key=True),
        sa.Column(
            "canonical_document_id",
            sa.Text(),
            sa.ForeignKey(
                "pms_doc.document_record.canonical_document_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Text(),
            sa.ForeignKey("pms_doc.document_version.version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_chunk_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.document_chunk.chunk_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("chunk_kind", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("heading_path", postgresql.JSONB(), nullable=False),
        sa.Column("page_numbers", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("bounding_boxes", postgresql.JSONB(), nullable=False),
        sa.Column("section_number", sa.Text(), nullable=True),
        sa.Column("clause_number", sa.Text(), nullable=True),
        sa.Column("language_code", sa.Text(), nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("script_code", sa.Text(), nullable=False),
        sa.Column("translation_group_id", sa.Text(), nullable=True),
        sa.Column("authoritative_language", sa.Text(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("document_status", sa.Text(), nullable=False),
        sa.Column("port_id", sa.Text(), nullable=True),
        sa.Column("department_id", sa.Text(), nullable=True),
        sa.Column("security_classification", sa.Text(), nullable=False),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("ocr_confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("parser_name", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("chunking_version", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_by_subject", sa.Text(), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fts",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', coalesce(text, ''))", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_kind IN ('parent', 'child')",
            name="ck_document_chunk_kind",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_chunk_ordinal"),
        sa.CheckConstraint(
            "token_count > 0 AND token_count <= 16384",
            name="ck_document_chunk_token_count",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_chunk_sha256",
        ),
        sa.CheckConstraint(
            "(chunk_kind = 'parent' AND parent_chunk_id IS NULL) "
            "OR (chunk_kind = 'child' AND parent_chunk_id IS NOT NULL)",
            name="ck_document_chunk_parent",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL "
            "OR effective_to > effective_from",
            name="ck_document_chunk_effective_dates",
        ),
        sa.CheckConstraint(
            "ocr_confidence IS NULL OR "
            "(ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_chunk_ocr_confidence",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_chunk_document_active",
        "document_chunk",
        ["canonical_document_id", "active", "chunk_kind"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_chunk_hash",
        "document_chunk",
        ["content_hash", "chunking_version"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_document_chunk_active_ordinal",
        "document_chunk",
        [
            "document_version_id",
            "chunking_version",
            "chunk_kind",
            "ordinal",
        ],
        unique=True,
        postgresql_where=sa.text("active"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_chunk_fts",
        "document_chunk",
        ["fts"],
        unique=False,
        postgresql_using="gin",
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE TABLE pms_vector.chunk_embedding (
          embedding_id text PRIMARY KEY,
          chunk_id text NOT NULL
            REFERENCES pms_vector.document_chunk(chunk_id) ON DELETE RESTRICT,
          embedding_model text NOT NULL,
          embedding_revision text NOT NULL,
          embedding_version text NOT NULL,
          dimension integer NOT NULL,
          content_hash varchar(64) NOT NULL,
          embedding pms_vector.vector(1024) NOT NULL,
          active boolean NOT NULL DEFAULT true,
          created_by_subject text NOT NULL,
          created_at timestamptz NOT NULL,
          deactivated_at timestamptz,
          CONSTRAINT ck_chunk_embedding_dimension CHECK (dimension = 1024),
          CONSTRAINT ck_chunk_embedding_sha256
            CHECK (content_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT uq_chunk_embedding_version
            UNIQUE (
              chunk_id,
              embedding_model,
              embedding_revision,
              embedding_version,
              content_hash
            )
        )
        """
    )
    op.create_index(
        "ix_chunk_embedding_active_model",
        "chunk_embedding",
        ["embedding_model", "embedding_revision", "active"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_chunk_embedding_active",
        "chunk_embedding",
        ["chunk_id", "embedding_model", "embedding_revision"],
        unique=True,
        postgresql_where=sa.text("active"),
        schema=SCHEMA,
    )

    op.create_table(
        "index_checkpoint",
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column(
            "canonical_document_id",
            sa.Text(),
            sa.ForeignKey(
                "pms_doc.document_record.canonical_document_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("document_version_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_chunk_ordinal", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("chunking_version", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_revision", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("started_by_subject", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('chunk', 'embed', 'deactivate')",
            name="ck_index_checkpoint_stage",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'complete', 'failed')",
            name="ck_index_checkpoint_status",
        ),
        sa.UniqueConstraint(
            "canonical_document_id",
            "document_version_id",
            "stage",
            "chunking_version",
            "embedding_model",
            "embedding_revision",
            name="uq_index_checkpoint_operation",
        ),
        schema=SCHEMA,
    )

    for table in ("document_chunk", "chunk_embedding", "index_checkpoint"):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY document_chunk_select ON pms_vector.document_chunk
        FOR SELECT USING (
          EXISTS (
            SELECT 1
            FROM pms_vector.chunk_acl AS acl
            WHERE acl.chunk_id = document_chunk.chunk_id
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY document_chunk_write ON pms_vector.document_chunk
        FOR ALL USING (
          {_document_write_scope("document_chunk.canonical_document_id")}
        )
        WITH CHECK (
          {_document_write_scope("document_chunk.canonical_document_id")}
        )
        """
    )
    op.execute(
        """
        CREATE POLICY chunk_embedding_select ON pms_vector.chunk_embedding
        FOR SELECT USING (
          EXISTS (
            SELECT 1
            FROM pms_vector.document_chunk AS chunk
            WHERE chunk.chunk_id = chunk_embedding.chunk_id
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunk_embedding_write ON pms_vector.chunk_embedding
        FOR ALL USING ({_embedding_write_scope()})
        WITH CHECK ({_embedding_write_scope()})
        """
    )
    op.execute(
        """
        CREATE POLICY index_checkpoint_select ON pms_vector.index_checkpoint
        FOR SELECT USING (
          started_by_subject = current_setting('pms.subject', true)
          OR pms_app.has_role('Auditor')
          OR pms_app.has_role('Administrator')
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY index_checkpoint_write ON pms_vector.index_checkpoint
        FOR ALL USING (
          {_document_write_scope("index_checkpoint.canonical_document_id")}
        )
        WITH CHECK (
          (
            {_document_write_scope("index_checkpoint.canonical_document_id")}
          )
          AND started_by_subject = current_setting('pms.subject', true)
        )
        """
    )


def downgrade() -> None:
    """Remove Phase 07 data while retaining the server extension itself."""

    op.drop_table("index_checkpoint", schema=SCHEMA)
    op.drop_table("chunk_embedding", schema=SCHEMA)
    op.drop_table("document_chunk", schema=SCHEMA)
    op.execute(
        """
        UPDATE pms_doc.document_record
        SET status = 'canonicalized'
        WHERE status = 'deactivated'
        """
    )
    op.drop_constraint(
        "ck_document_record_status",
        "document_record",
        schema="pms_doc",
        type_="check",
    )
    phase_06_statuses = tuple(
        status for status in PHASE_07_STATUSES if status != "deactivated"
    )
    status_values = ", ".join(f"'{status}'" for status in phase_06_statuses)
    op.create_check_constraint(
        "ck_document_record_status",
        "document_record",
        f"status IN ({status_values})",
        schema="pms_doc",
    )
    for policy in ("chunk_acl_delete", "chunk_acl_update", "chunk_acl_insert"):
        op.execute(f"DROP POLICY {policy} ON pms_vector.chunk_acl")
    original_scope = """
      pms_app.has_role('Administrator')
      OR (
        pms_app.has_role('Data Entry Operator')
        AND canonical_tenant_id =
          NULLIF(current_setting('pms.tenant_id', true), '')
      )
    """
    op.execute(
        f"""
        CREATE POLICY chunk_acl_insert ON pms_vector.chunk_acl
        FOR INSERT WITH CHECK ({original_scope})
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunk_acl_update ON pms_vector.chunk_acl
        FOR UPDATE USING ({original_scope})
        WITH CHECK ({original_scope})
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunk_acl_delete ON pms_vector.chunk_acl
        FOR DELETE USING ({original_scope})
        """
    )
