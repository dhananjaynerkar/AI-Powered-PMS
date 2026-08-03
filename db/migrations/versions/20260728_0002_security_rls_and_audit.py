"""Create Phase 04 identity mappings, ACLs, audit events, and RLS.

Revision ID: 20260728_0002
Revises: 20260728_0001
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0002"
down_revision: str | None = "20260728_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


def _role_check(column: str) -> sa.CheckConstraint:
    quoted_roles = ", ".join(f"'{role}'" for role in ROLES)
    return sa.CheckConstraint(
        f"{column} IN ({quoted_roles})",
        name=f"ck_{column}_approved",
    )


def _classification_check(column: str) -> sa.CheckConstraint:
    values = ", ".join(f"'{value}'" for value in CLASSIFICATIONS)
    return sa.CheckConstraint(
        f"{column} IN ({values})",
        name=f"ck_{column}_approved",
    )


def _role_array_check(column: str, table: str) -> sa.CheckConstraint:
    values = ", ".join(f"'{role}'" for role in ROLES)
    return sa.CheckConstraint(
        f"{column} <@ ARRAY[{values}]::text[]",
        name=f"ck_{table}_{column}_approved",
    )


def upgrade() -> None:
    """Create only application-owned security objects."""

    op.create_table(
        "user_tenant_mapping",
        sa.Column("keycloak_subject", sa.Text(), nullable=False),
        sa.Column("canonical_tenant_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("department_id", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=False, server_default="internal"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _role_check("role"),
        _classification_check("classification"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_user_tenant_mapping_valid_period",
        ),
        sa.PrimaryKeyConstraint(
            "keycloak_subject",
            "canonical_tenant_id",
            "role",
            name="pk_user_tenant_mapping",
        ),
        schema="pms_app",
    )
    op.create_index(
        "ix_user_tenant_mapping_tenant_active",
        "user_tenant_mapping",
        ["canonical_tenant_id", "active"],
        schema="pms_app",
    )

    op.create_table(
        "document_acl",
        sa.Column("canonical_document_id", sa.Text(), primary_key=True),
        sa.Column("canonical_tenant_id", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=False, server_default="internal"),
        sa.Column(
            "allowed_roles",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "allowed_departments",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        _classification_check("classification"),
        _role_array_check("allowed_roles", "document_acl"),
        schema="pms_doc",
    )
    op.create_index(
        "ix_document_acl_tenant",
        "document_acl",
        ["canonical_tenant_id"],
        schema="pms_doc",
    )

    op.create_table(
        "chunk_acl",
        sa.Column("chunk_id", sa.Text(), primary_key=True),
        sa.Column("canonical_document_id", sa.Text(), nullable=False),
        sa.Column("canonical_tenant_id", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=False, server_default="internal"),
        sa.Column(
            "allowed_roles",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "allowed_departments",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        _classification_check("classification"),
        _role_array_check("allowed_roles", "chunk_acl"),
        schema="pms_vector",
    )
    op.create_index(
        "ix_chunk_acl_tenant_document",
        "chunk_acl",
        ["canonical_tenant_id", "canonical_document_id"],
        schema="pms_vector",
    )

    op.create_table(
        "security_event",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("canonical_tenant_id", sa.Text(), nullable=True),
        sa.Column("query_category", sa.Text(), nullable=False),
        sa.Column("entity_scope", postgresql.JSONB(), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), nullable=False),
        sa.Column("prediction_version", sa.Text(), nullable=True),
        sa.Column("rule_version", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("result_status", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "result_status IN ('ALLOWED', 'DENIED', 'ERROR', 'REVIEW_REQUIRED')",
            name="ck_security_event_result_status",
        ),
        schema="pms_audit",
    )
    op.create_index(
        "ix_security_event_subject_time",
        "security_event",
        ["subject", "occurred_at"],
        schema="pms_audit",
    )
    op.create_index(
        "ix_security_event_tenant_time",
        "security_event",
        ["canonical_tenant_id", "occurred_at"],
        schema="pms_audit",
    )

    op.execute(
        """
        CREATE FUNCTION pms_app.has_role(required_role text)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        AS $$
          SELECT required_role = ANY(
            string_to_array(
              COALESCE(current_setting('pms.roles', true), ''),
              ','
            )
          )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION pms_app.classification_rank(value text)
        RETURNS integer
        LANGUAGE sql
        IMMUTABLE
        AS $$
          SELECT CASE value
            WHEN 'public' THEN 0
            WHEN 'internal' THEN 1
            WHEN 'confidential' THEN 2
            WHEN 'restricted' THEN 3
            ELSE -1
          END
        $$
        """
    )

    for qualified_table in (
        "pms_app.user_tenant_mapping",
        "pms_doc.document_acl",
        "pms_vector.chunk_acl",
        "pms_audit.security_event",
    ):
        op.execute(f"ALTER TABLE {qualified_table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified_table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY user_mapping_select ON pms_app.user_tenant_mapping
        FOR SELECT USING (
          keycloak_subject = current_setting('pms.subject', true)
          OR pms_app.has_role('Auditor')
          OR pms_app.has_role('Administrator')
        )
        """
    )
    op.execute(
        """
        CREATE POLICY user_mapping_admin_write ON pms_app.user_tenant_mapping
        FOR ALL USING (pms_app.has_role('Administrator'))
        WITH CHECK (pms_app.has_role('Administrator'))
        """
    )

    acl_predicate = """
        (
          canonical_tenant_id IS NULL
          OR canonical_tenant_id = NULLIF(current_setting('pms.tenant_id', true), '')
          OR pms_app.has_role('Nodal/Regional Officer')
          OR pms_app.has_role('Finance Officer')
          OR pms_app.has_role('Estate Officer')
          OR pms_app.has_role('HOD')
          OR pms_app.has_role('Auditor')
          OR pms_app.has_role('Administrator')
        )
        AND (
          cardinality(allowed_roles) = 0
          OR allowed_roles && string_to_array(
            COALESCE(current_setting('pms.roles', true), ''),
            ','
          )
        )
        AND (
          cardinality(allowed_departments) = 0
          OR NULLIF(current_setting('pms.department_id', true), '') =
             ANY(allowed_departments)
          OR pms_app.has_role('Auditor')
          OR pms_app.has_role('Administrator')
        )
        AND pms_app.classification_rank(
          COALESCE(current_setting('pms.classification', true), 'public')
        ) >= pms_app.classification_rank(classification)
    """
    op.execute(
        f"""
        CREATE POLICY document_acl_select ON pms_doc.document_acl
        FOR SELECT USING ({acl_predicate})
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunk_acl_select ON pms_vector.chunk_acl
        FOR SELECT USING ({acl_predicate})
        """
    )
    for qualified_table, policy_prefix in (
        ("pms_doc.document_acl", "document_acl"),
        ("pms_vector.chunk_acl", "chunk_acl"),
    ):
        write_predicate = """
            pms_app.has_role('Administrator')
            OR (
              pms_app.has_role('Data Entry Operator')
              AND canonical_tenant_id = NULLIF(
                current_setting('pms.tenant_id', true),
                ''
              )
            )
        """
        op.execute(
            f"""
            CREATE POLICY {policy_prefix}_insert ON {qualified_table}
            FOR INSERT WITH CHECK ({write_predicate})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {policy_prefix}_update ON {qualified_table}
            FOR UPDATE USING ({write_predicate})
            WITH CHECK ({write_predicate})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {policy_prefix}_delete ON {qualified_table}
            FOR DELETE USING ({write_predicate})
            """
        )

    op.execute(
        """
        CREATE POLICY security_event_insert ON pms_audit.security_event
        FOR INSERT WITH CHECK (
          subject = current_setting('pms.subject', true)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY security_event_select ON pms_audit.security_event
        FOR SELECT USING (
          pms_app.has_role('Auditor')
          OR pms_app.has_role('Administrator')
        )
        """
    )


def downgrade() -> None:
    """Remove only Phase 04 application-owned security objects."""

    for qualified_table, policy_names in (
        (
            "pms_audit.security_event",
            ("security_event_select", "security_event_insert"),
        ),
        (
            "pms_vector.chunk_acl",
            (
                "chunk_acl_delete",
                "chunk_acl_update",
                "chunk_acl_insert",
                "chunk_acl_select",
            ),
        ),
        (
            "pms_doc.document_acl",
            (
                "document_acl_delete",
                "document_acl_update",
                "document_acl_insert",
                "document_acl_select",
            ),
        ),
        (
            "pms_app.user_tenant_mapping",
            ("user_mapping_admin_write", "user_mapping_select"),
        ),
    ):
        for policy_name in policy_names:
            op.execute(f"DROP POLICY {policy_name} ON {qualified_table}")

    op.drop_table("security_event", schema="pms_audit")
    op.drop_table("chunk_acl", schema="pms_vector")
    op.drop_table("document_acl", schema="pms_doc")
    op.drop_table("user_tenant_mapping", schema="pms_app")
    op.execute("DROP FUNCTION pms_app.classification_rank(text)")
    op.execute("DROP FUNCTION pms_app.has_role(text)")
