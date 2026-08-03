"""Create the Phase 12 PostgreSQL adjacency graph.

The graph is intentionally empty after migration.  Nodes and edges require
reviewed source provenance; this revision does not fabricate relationships from
the extracted database or from an LLM.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0012"
down_revision: str | None = "20260730_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "pms_graph"
NODE_TYPES = (
    "tenant",
    "tenancy",
    "plot",
    "agreement",
    "bill",
    "payment",
    "inspection",
    "breach",
    "notice",
    "legal_case",
    "policy",
    "clause",
)
EDGE_TYPES = (
    "TENANT_HAS_TENANCY",
    "TENANCY_OCCUPIES_PLOT",
    "TENANCY_GOVERNED_BY_AGREEMENT",
    "TENANCY_HAS_BILL",
    "BILL_HAS_PAYMENT",
    "PLOT_HAS_INSPECTION",
    "INSPECTION_FOUND_BREACH",
    "BREACH_TRIGGERED_NOTICE",
    "NOTICE_ESCALATED_TO_SUIT",
    "POLICY_HAS_CLAUSE",
    "CIRCULAR_AMENDS_POLICY",
    "CLAUSE_APPLIES_TO_LEASE_TYPE",
)
STATUSES = ("candidate", "verified", "rejected")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


def _classification_scope(alias: str) -> str:
    return f"""
      CASE {alias}.security_classification
        WHEN 'public' THEN 0
        WHEN 'internal' THEN 1
        WHEN 'confidential' THEN 2
        WHEN 'restricted' THEN 3
      END <= CASE NULLIF(current_setting('pms.classification', true), '')
        WHEN 'public' THEN 0
        WHEN 'internal' THEN 1
        WHEN 'confidential' THEN 2
        WHEN 'restricted' THEN 3
        ELSE 0
      END
      AND (
        {alias}.owner_canonical_tenant_id IS NULL
        OR pms_app.has_role('Nodal/Regional Officer')
        OR pms_app.has_role('Finance Officer')
        OR pms_app.has_role('Estate Officer')
        OR pms_app.has_role('HOD')
        OR pms_app.has_role('Auditor')
        OR pms_app.has_role('Administrator')
        OR {alias}.owner_canonical_tenant_id =
          NULLIF(current_setting('pms.tenant_id', true), '')
      )
    """


def _candidate_write_scope(alias: str) -> str:
    return f"""
      {alias}.verification_status = 'candidate'
      AND {alias}.created_by_subject =
        NULLIF(current_setting('pms.subject', true), '')
      AND (
        pms_app.has_role('Administrator')
        OR pms_app.has_role('Data Entry Operator')
        OR pms_app.has_role('Nodal/Regional Officer')
        OR pms_app.has_role('Estate Officer')
      )
    """


def _reviewer_scope(alias: str) -> str:
    del alias
    return """
      pms_app.has_role('Auditor') OR pms_app.has_role('Administrator')
    """


def upgrade() -> None:
    """Create application-owned graph tables with forced RLS."""

    node_types = ", ".join(f"'{value}'" for value in NODE_TYPES)
    edge_types = ", ".join(f"'{value}'" for value in EDGE_TYPES)
    statuses = ", ".join(f"'{value}'" for value in STATUSES)
    classifications = ", ".join(f"'{value}'" for value in CLASSIFICATIONS)

    op.create_table(
        "graph_node",
        sa.Column("node_id", sa.Text(), primary_key=True),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("canonical_entity_id", sa.Text(), nullable=False),
        sa.Column("owner_canonical_tenant_id", sa.Text(), nullable=True),
        sa.Column("source_schema", sa.Text(), nullable=False),
        sa.Column("source_table", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=True),
        sa.Column("source_chunk_id", sa.Text(), nullable=True),
        sa.Column("source_clause", sa.Text(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("security_classification", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_subject", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "node_type",
            "canonical_entity_id",
            "source_schema",
            "source_table",
            "source_record_id",
            name="uq_graph_node_identity",
        ),
        sa.CheckConstraint(
            f"node_type IN ({node_types})", name="ck_graph_node_type"
        ),
        sa.CheckConstraint(
            f"security_classification IN ({classifications})",
            name="ck_graph_node_classification",
        ),
        sa.CheckConstraint(
            f"verification_status IN ({statuses})",
            name="ck_graph_node_status",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_graph_node_validity",
        ),
        sa.CheckConstraint(
            "source_page IS NULL OR source_page > 0",
            name="ck_graph_node_page",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_node_confidence",
        ),
        sa.CheckConstraint(
            "(verification_status = 'candidate' AND reviewed_by_subject IS NULL) "
            "OR (verification_status <> 'candidate' AND reviewed_by_subject IS NOT NULL)",
            name="ck_graph_node_review",
        ),
        sa.CheckConstraint(
            "(verification_status = 'candidate') OR confidence IS NULL",
            name="ck_graph_node_confidence_candidate_only",
        ),
        sa.CheckConstraint(
            "(source_document_id IS NULL AND source_chunk_id IS NULL "
            "AND source_clause IS NULL AND source_page IS NULL) "
            "OR (source_document_id IS NOT NULL AND source_page IS NOT NULL)",
            name="ck_graph_node_document_provenance",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "graph_edge",
        sa.Column("edge_id", sa.Text(), primary_key=True),
        sa.Column(
            "from_node_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.graph_node.node_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_node_id",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.graph_node.node_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.Column("owner_canonical_tenant_id", sa.Text(), nullable=True),
        sa.Column("source_schema", sa.Text(), nullable=False),
        sa.Column("source_table", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=True),
        sa.Column("source_chunk_id", sa.Text(), nullable=True),
        sa.Column("source_clause", sa.Text(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("security_classification", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_subject", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "from_node_id",
            "to_node_id",
            "edge_type",
            "source_schema",
            "source_table",
            "source_record_id",
            name="uq_graph_edge_identity",
        ),
        sa.CheckConstraint(
            f"edge_type IN ({edge_types})", name="ck_graph_edge_type"
        ),
        sa.CheckConstraint(
            f"security_classification IN ({classifications})",
            name="ck_graph_edge_classification",
        ),
        sa.CheckConstraint(
            f"verification_status IN ({statuses})",
            name="ck_graph_edge_status",
        ),
        sa.CheckConstraint(
            "from_node_id <> to_node_id", name="ck_graph_edge_no_self_loop"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_graph_edge_validity",
        ),
        sa.CheckConstraint(
            "source_page IS NULL OR source_page > 0",
            name="ck_graph_edge_page",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_edge_confidence",
        ),
        sa.CheckConstraint(
            "(verification_status = 'candidate' AND reviewed_by_subject IS NULL) "
            "OR (verification_status <> 'candidate' AND reviewed_by_subject IS NOT NULL)",
            name="ck_graph_edge_review",
        ),
        sa.CheckConstraint(
            "(verification_status = 'candidate') OR confidence IS NULL",
            name="ck_graph_edge_confidence_candidate_only",
        ),
        sa.CheckConstraint(
            "(source_document_id IS NULL AND source_chunk_id IS NULL "
            "AND source_clause IS NULL AND source_page IS NULL) "
            "OR (source_document_id IS NOT NULL AND source_page IS NOT NULL)",
            name="ck_graph_edge_document_provenance",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_graph_node_visible",
        "graph_node",
        ["node_id", "active", "verification_status", "security_classification"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_graph_edge_traversal",
        "graph_edge",
        ["from_node_id", "to_node_id", "active", "verification_status"],
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.graph_node ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.graph_node FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.graph_edge ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.graph_edge FORCE ROW LEVEL SECURITY")

    node_scope = _classification_scope("graph_node")
    edge_scope = _classification_scope("graph_edge")
    op.execute(
        f"""
        CREATE POLICY graph_node_select ON {SCHEMA}.graph_node
        FOR SELECT USING (
          graph_node.active
          AND graph_node.verification_status = 'verified'
          AND {node_scope}
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_edge_select ON {SCHEMA}.graph_edge
        FOR SELECT USING (
          graph_edge.active
          AND graph_edge.verification_status = 'verified'
          AND {edge_scope}
          AND EXISTS (
            SELECT 1 FROM {SCHEMA}.graph_node AS from_node
            WHERE from_node.node_id = graph_edge.from_node_id
          )
          AND EXISTS (
            SELECT 1 FROM {SCHEMA}.graph_node AS to_node
            WHERE to_node.node_id = graph_edge.to_node_id
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_node_candidate_insert ON {SCHEMA}.graph_node
        FOR INSERT WITH CHECK ({_candidate_write_scope('graph_node')})
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_edge_candidate_insert ON {SCHEMA}.graph_edge
        FOR INSERT WITH CHECK ({_candidate_write_scope('graph_edge')})
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_node_reviewer_write ON {SCHEMA}.graph_node
        FOR UPDATE USING ({_reviewer_scope('graph_node')})
        WITH CHECK ({_reviewer_scope('graph_node')})
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_edge_reviewer_write ON {SCHEMA}.graph_edge
        FOR UPDATE USING ({_reviewer_scope('graph_edge')})
        WITH CHECK ({_reviewer_scope('graph_edge')})
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_node_admin_delete ON {SCHEMA}.graph_node
        FOR DELETE USING (pms_app.has_role('Administrator'))
        """
    )
    op.execute(
        f"""
        CREATE POLICY graph_edge_admin_delete ON {SCHEMA}.graph_edge
        FOR DELETE USING (pms_app.has_role('Administrator'))
        """
    )


def downgrade() -> None:
    """Remove only Phase 12 graph objects."""

    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.graph_edge")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.graph_node")
