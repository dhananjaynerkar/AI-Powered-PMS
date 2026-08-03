"""Create the Phase 09 semantic catalog, identity map, and governed views.

Revision ID: 20260730_0007
Revises: 20260729_0006
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260729_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATALOG_SCHEMA = "pms_catalog"
GOVERNED_VIEWS = (
    "tenant_360",
    "tenancy_360",
    "plot_360",
    "agreement_360",
    "bill_360",
    "payment_360",
    "outstanding_360",
    "inspection_360",
    "legal_case_360",
)


def _port_wide_scope() -> str:
    return """
      pms_app.has_role('Nodal/Regional Officer')
      OR pms_app.has_role('Finance Officer')
      OR pms_app.has_role('Estate Officer')
      OR pms_app.has_role('HOD')
      OR pms_app.has_role('Auditor')
      OR pms_app.has_role('Administrator')
    """


def _identity_scope(alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return f"""
      (
        {prefix}owner_canonical_tenant_id =
          NULLIF(current_setting('pms.tenant_id', true), '')
        OR ({_port_wide_scope()})
      )
    """


def upgrade() -> None:
    """Create only application-owned Phase 09 objects."""

    op.execute("CREATE SCHEMA IF NOT EXISTS pms_catalog")
    op.execute(
        """
        CREATE TABLE pms_catalog.semantic_table (
          catalog_table_id text PRIMARY KEY,
          source_schema text NOT NULL,
          source_table text NOT NULL,
          table_kind text NOT NULL,
          business_description text NOT NULL,
          row_count bigint,
          freshness_at timestamptz,
          search_text text NOT NULL,
          embedding_model text,
          embedding_revision text,
          embedding pms_vector.vector(1024),
          approved_for_query boolean NOT NULL DEFAULT false,
          profiled_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_semantic_table_source UNIQUE (source_schema, source_table),
          CONSTRAINT ck_semantic_table_kind
            CHECK (table_kind IN ('extracted_table', 'governed_view')),
          CONSTRAINT ck_semantic_table_row_count CHECK (row_count IS NULL OR row_count >= 0),
          CONSTRAINT ck_semantic_table_embedding_metadata CHECK (
            (embedding IS NULL AND embedding_model IS NULL AND embedding_revision IS NULL)
            OR
            (embedding IS NOT NULL AND embedding_model IS NOT NULL
              AND embedding_revision IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_catalog.semantic_column (
          catalog_column_id text PRIMARY KEY,
          catalog_table_id text NOT NULL
            REFERENCES pms_catalog.semantic_table(catalog_table_id)
            ON DELETE CASCADE,
          source_schema text NOT NULL,
          source_table text NOT NULL,
          source_column text NOT NULL,
          ordinal_position integer NOT NULL,
          data_type text NOT NULL,
          semantic_class text NOT NULL,
          sensitive boolean NOT NULL,
          embedding_eligible boolean NOT NULL,
          approved_for_query boolean NOT NULL,
          business_description text NOT NULL,
          CONSTRAINT uq_semantic_column_source
            UNIQUE (source_schema, source_table, source_column),
          CONSTRAINT ck_semantic_column_ordinal CHECK (ordinal_position > 0),
          CONSTRAINT ck_semantic_column_class CHECK (
            semantic_class IN (
              'identifier', 'measure', 'date', 'category',
              'name', 'narrative', 'sensitive'
            )
          ),
          CONSTRAINT ck_semantic_column_embedding CHECK (
            NOT embedding_eligible OR (
              semantic_class = 'narrative' AND NOT sensitive
            )
          ),
          CONSTRAINT ck_semantic_column_approval CHECK (
            NOT sensitive OR NOT approved_for_query
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_catalog.join_path (
          join_path_id text PRIMARY KEY,
          left_schema text NOT NULL,
          left_table text NOT NULL,
          left_column text NOT NULL,
          right_schema text NOT NULL,
          right_table text NOT NULL,
          right_column text NOT NULL,
          relationship text NOT NULL,
          measured_left_rows bigint NOT NULL,
          measured_matched_rows bigint NOT NULL,
          match_ratio numeric(12, 9) NOT NULL,
          approved boolean NOT NULL,
          review_reason text NOT NULL,
          reviewed_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_join_path_rows CHECK (
            measured_left_rows >= 0
            AND measured_matched_rows >= 0
            AND measured_matched_rows <= measured_left_rows
          ),
          CONSTRAINT ck_join_path_ratio CHECK (match_ratio >= 0 AND match_ratio <= 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_catalog.entity_identity_map (
          identity_map_id text PRIMARY KEY,
          entity_type text NOT NULL,
          canonical_entity_id text NOT NULL,
          owner_canonical_tenant_id text,
          source_schema text NOT NULL,
          source_table text NOT NULL,
          source_record_id text NOT NULL,
          source_refreshed_at timestamptz,
          mapping_basis text NOT NULL,
          reviewed_by_subject text NOT NULL,
          reviewed_at timestamptz NOT NULL DEFAULT now(),
          valid_from timestamptz NOT NULL DEFAULT now(),
          valid_to timestamptz,
          active boolean NOT NULL DEFAULT true,
          CONSTRAINT uq_entity_identity_source UNIQUE (
            entity_type, source_schema, source_table, source_record_id
          ),
          CONSTRAINT uq_entity_identity_canonical UNIQUE (
            entity_type, canonical_entity_id, source_schema, source_table
          ),
          CONSTRAINT ck_entity_identity_type CHECK (
            entity_type IN (
              'tenant', 'tenancy', 'plot', 'agreement', 'bill',
              'payment', 'outstanding', 'inspection', 'legal_case'
            )
          ),
          CONSTRAINT ck_entity_identity_validity
            CHECK (valid_to IS NULL OR valid_to > valid_from)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_semantic_table_fts
        ON pms_catalog.semantic_table
        USING gin (to_tsvector('simple', search_text))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_semantic_column_lookup
        ON pms_catalog.semantic_column
        (source_schema, source_table, semantic_class, approved_for_query)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_entity_identity_owner
        ON pms_catalog.entity_identity_map
        (owner_canonical_tenant_id, entity_type, active)
        """
    )
    op.execute(
        """
        ALTER TABLE pms_catalog.entity_identity_map ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pms_catalog.entity_identity_map FORCE ROW LEVEL SECURITY
        """
    )
    scope = _identity_scope()
    op.execute(
        f"""
        CREATE POLICY identity_map_select
        ON pms_catalog.entity_identity_map
        FOR SELECT USING (active AND {scope})
        """
    )
    op.execute(
        """
        CREATE POLICY identity_map_admin_write
        ON pms_catalog.entity_identity_map
        FOR ALL USING (pms_app.has_role('Administrator'))
        WITH CHECK (pms_app.has_role('Administrator'))
        """
    )
    op.execute(
        """
        CREATE VIEW pms_catalog.approved_semantic_table
        WITH (security_barrier = true) AS
        SELECT catalog_table_id, source_schema, source_table, table_kind,
               business_description, row_count, freshness_at, search_text,
               embedding_model, embedding_revision, embedding, profiled_at
        FROM pms_catalog.semantic_table
        WHERE approved_for_query
        """
    )
    op.execute(
        """
        CREATE VIEW pms_catalog.approved_semantic_column
        WITH (security_barrier = true) AS
        SELECT catalog_column_id, catalog_table_id, source_schema, source_table,
               source_column, ordinal_position, data_type, semantic_class,
               embedding_eligible, business_description
        FROM pms_catalog.semantic_column
        WHERE approved_for_query AND NOT sensitive
        """
    )
    op.execute(
        """
        CREATE VIEW pms_catalog.approved_join_path
        WITH (security_barrier = true) AS
        SELECT join_path_id, left_schema, left_table, left_column,
               right_schema, right_table, right_column, relationship,
               measured_left_rows, measured_matched_rows, match_ratio,
               review_reason, reviewed_at
        FROM pms_catalog.join_path
        WHERE approved
        """
    )

    _create_governed_views()


def _create_governed_views() -> None:
    scope = _identity_scope("identity_map")
    view_definitions = {
        "tenant_360": f"""
          SELECT identity_map.canonical_entity_id,
                 applicant.ind_org_name AS tenant_name,
                 applicant.customer_code,
                 applicant.registration_timestamp,
                 applicant.registration_type,
                 applicant.org_type_id AS organization_type_id,
                 applicant.status,
                 identity_map.source_schema,
                 identity_map.source_table,
                 identity_map.source_record_id,
                 identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.bridge_applicant_tenancy AS bridge
            ON identity_map.source_table = 'bridge_applicant_tenancy'
           AND identity_map.source_record_id = bridge.app_tenant_map_id::text
          JOIN pms_extract_2010_2023.dim_applicant_safe AS applicant
            ON applicant.applicant_id = bridge.applicant_id
          WHERE identity_map.entity_type = 'tenant'
            AND identity_map.active AND {scope}
        """,
        "tenancy_360": f"""
          SELECT identity_map.canonical_entity_id,
                 bridge.tenancy_id, bridge.customer_code, bridge.agreement_number,
                 bridge.billable, bridge.billable_as, bridge.from_date AS valid_from,
                 bridge.to_date AS valid_to, bridge.status,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.bridge_applicant_tenancy AS bridge
            ON identity_map.source_table = 'bridge_applicant_tenancy'
           AND identity_map.source_record_id = bridge.app_tenant_map_id::text
          WHERE identity_map.entity_type = 'tenancy'
            AND identity_map.active AND {scope}
        """,
        "plot_360": f"""
          SELECT identity_map.canonical_entity_id,
                 plot.plot_code, plot.rr_no, plot.location, plot.area,
                 plot.plot_desc AS plot_description, plot.status,
                 plot.is_verified, plot.is_active, plot.is_vacant,
                 plot.owner, plot.dept_name AS department_name, plot.remarks,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.dim_plot AS plot
            ON identity_map.source_table = 'dim_plot'
           AND identity_map.source_record_id = plot.plot_id::text
          WHERE identity_map.entity_type = 'plot'
            AND identity_map.active AND {scope}
        """,
        "agreement_360": f"""
          SELECT identity_map.canonical_entity_id,
                 lease.tenancy_id, lease.customer_code, lease.agreement_number,
                 lease.tenancy_type, lease.tenant_type, lease.description,
                 lease.purpose, lease.allotment_basis, lease.bill_periodicity,
                 lease.rate, lease.percent_rate_revision, lease.amount_rate_revision,
                 lease.security_deposit_amt AS security_deposit_amount,
                 lease.total_security_deposit,
                 lease.date_of_agreement AS agreement_date,
                 lease.duration_from, lease.duration_to, lease.renewal_date,
                 lease.is_renewable, lease.status, lease.remarks,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.dim_property_lease AS lease
            ON identity_map.source_table = 'dim_property_lease'
           AND identity_map.source_record_id = lease.tenant_id::text
          WHERE identity_map.entity_type = 'agreement'
            AND identity_map.active AND {scope}
        """,
        "bill_360": f"""
          SELECT identity_map.canonical_entity_id,
                 bill.bill_code, bill.bill_creation_date, bill.total_head_amount,
                 bill.total_tax_amount, bill.final_amount, bill.bill_status,
                 bill.tenancy_id, bill.bill_year, bill.bill_month, bill.bill_date,
                 bill.due_date, bill.amount,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.fact_monthly_bills AS bill
            ON identity_map.source_table = 'fact_monthly_bills'
           AND identity_map.source_record_id = bill.bill_id::text
          WHERE identity_map.entity_type = 'bill'
            AND identity_map.active AND {scope}
        """,
        "payment_360": f"""
          SELECT identity_map.canonical_entity_id,
                 payment.bill_code, payment.amount, payment.transaction_date,
                 payment.payment_date, payment.interest_amount, payment.head_amount,
                 payment.head_balance_amount, payment.settlement_type,
                 payment.is_final, payment.tds_amt AS tds_amount,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.fact_payment_current AS payment
            ON identity_map.source_table = 'fact_payment_current'
           AND identity_map.source_record_id = payment.payment_history_id::text
          WHERE identity_map.entity_type = 'payment'
            AND identity_map.active AND {scope}
        """,
        "outstanding_360": f"""
          SELECT identity_map.canonical_entity_id,
                 snapshot.cust_code AS customer_code,
                 snapshot.plot_no_rr_no_current AS current_plot_number,
                 snapshot.plot_area_sqm_currrent AS current_plot_area_sqm,
                 snapshot.contractual_rent_current AS current_contractual_rent,
                 snapshot.billed_arrears_current,
                 snapshot.outstanding_arrears_current,
                 snapshot.penalties_current, snapshot.taxes_current,
                 snapshot.amount_to_be_recovered_current, snapshot.status,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.dim_lease_particulars_snapshot AS snapshot
            ON identity_map.source_table = 'dim_lease_particulars_snapshot'
           AND identity_map.source_record_id = snapshot.lease_particulars_id::text
          WHERE identity_map.entity_type = 'outstanding'
            AND identity_map.active AND {scope}
        """,
        "inspection_360": f"""
          SELECT identity_map.canonical_entity_id,
                 inspection.inspection_date, inspection.status,
                 inspection.is_verified, inspection.observation_date,
                 inspection.customer_code, inspection.tenancy_type,
                 inspection.is_vacant_plot, inspection.plot_id,
                 inspection.tenure, inspection.renewal_clause,
                 inspection.desc_of_struct AS structure_description,
                 inspection.bltup_area AS built_up_area,
                 inspection.rmk_major_1 AS major_remark_1,
                 inspection.rmk_minor_1 AS minor_remark_1,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.fact_inspection AS inspection
            ON identity_map.source_table = 'fact_inspection'
           AND identity_map.source_record_id = inspection.inspection_rpt_id::text
          WHERE identity_map.entity_type = 'inspection'
            AND identity_map.active AND {scope}
        """,
        "legal_case_360": f"""
          SELECT identity_map.canonical_entity_id,
                 legal.suit_case_no AS suit_case_number,
                 legal.suit_no AS suit_number, legal.plot_no AS plot_number,
                 legal.suit_year, legal.suit_date, legal.court_id, legal.remarks,
                 legal.tenancy_id, legal.withdrawn,
                 legal.suit_ref_no AS suit_reference_number,
                 legal.suit_filed_by AS filed_by, legal.status,
                 legal.current_suit_stage AS current_stage,
                 legal.litigation_ground, legal.customer_code,
                 legal.next_hearing_date, legal.previous_hearing_date,
                 identity_map.source_schema, identity_map.source_table,
                 identity_map.source_record_id, identity_map.source_refreshed_at
          FROM pms_catalog.entity_identity_map AS identity_map
          JOIN pms_extract_2010_2023.fact_legal_suit AS legal
            ON identity_map.source_table = 'fact_legal_suit'
           AND identity_map.source_record_id = legal.suit_case_id::text
          WHERE identity_map.entity_type = 'legal_case'
            AND identity_map.active AND {scope}
        """,
    }
    for view_name, query in view_definitions.items():
        op.execute(
            f"""
            CREATE VIEW pms_app.{view_name}
            WITH (security_barrier = true) AS
            {query}
            """
        )


def downgrade() -> None:
    """Remove only Phase 09 objects."""

    for view_name in reversed(GOVERNED_VIEWS):
        op.execute(f"DROP VIEW IF EXISTS pms_app.{view_name}")
    op.execute("DROP SCHEMA IF EXISTS pms_catalog CASCADE")
