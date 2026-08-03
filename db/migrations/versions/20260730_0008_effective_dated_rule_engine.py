"""Create effective-dated reviewed rules and immutable calculations.

Revision ID: 20260730_0008
Revises: 20260730_0007
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0008"
down_revision: str | None = "20260730_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rule_reader() -> str:
    return """
      pms_app.has_role('Finance Officer')
      OR pms_app.has_role('Legal Officer')
      OR pms_app.has_role('Estate Officer')
      OR pms_app.has_role('HOD')
      OR pms_app.has_role('Auditor')
      OR pms_app.has_role('Administrator')
    """


def _calculation_scope() -> str:
    return f"""
      (
        canonical_tenant_id =
          NULLIF(current_setting('pms.tenant_id', true), '')
        OR ({_rule_reader()})
      )
    """


def upgrade() -> None:
    """Create only application-owned Phase 10 objects."""

    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA pms_rules")
    op.execute(
        """
        CREATE TABLE pms_rules.rule_candidate (
          candidate_id text PRIMARY KEY,
          candidate_family text NOT NULL,
          source_schema text NOT NULL,
          source_table text NOT NULL,
          source_record_id text NOT NULL,
          proposed_valid_from date,
          proposed_valid_to date,
          raw_payload jsonb NOT NULL,
          source_document_id text,
          source_clause text,
          source_page integer,
          evidence_status text NOT NULL DEFAULT 'missing',
          candidate_status text NOT NULL DEFAULT 'unapproved',
          imported_by_subject text NOT NULL,
          imported_at timestamptz NOT NULL DEFAULT now(),
          promoted_rule_id text,
          CONSTRAINT uq_rule_candidate_source
            UNIQUE (source_schema, source_table, source_record_id),
          CONSTRAINT ck_rule_candidate_family CHECK (
            candidate_family IN (
              'rent', 'additional_rent', 'tax', 'interest', 'penalty'
            )
          ),
          CONSTRAINT ck_rule_candidate_status CHECK (
            candidate_status IN ('unapproved', 'rejected', 'promoted')
          ),
          CONSTRAINT ck_rule_candidate_evidence CHECK (
            evidence_status IN ('missing', 'linked', 'verified')
          ),
          CONSTRAINT ck_rule_candidate_page
            CHECK (source_page IS NULL OR source_page > 0),
          CONSTRAINT ck_rule_candidate_dates CHECK (
            proposed_valid_to IS NULL
            OR proposed_valid_from IS NULL
            OR proposed_valid_to > proposed_valid_from
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.rule_definition (
          rule_id text PRIMARY KEY,
          candidate_id text
            REFERENCES pms_rules.rule_candidate(candidate_id) ON DELETE RESTRICT,
          rule_family text NOT NULL,
          component_code text NOT NULL,
          jurisdiction text NOT NULL,
          applicability_key text NOT NULL,
          valid_from date NOT NULL,
          valid_to date NOT NULL,
          valid_period daterange GENERATED ALWAYS AS (
            daterange(valid_from, valid_to, '[)')
          ) STORED,
          system_from timestamptz NOT NULL DEFAULT now(),
          system_to timestamptz,
          system_period tstzrange GENERATED ALWAYS AS (
            tstzrange(system_from, system_to, '[)')
          ) STORED,
          calculation_basis text NOT NULL,
          rate_value numeric(24, 10) NOT NULL,
          tax_mechanism text NOT NULL,
          payer text NOT NULL,
          invoice_inclusion boolean NOT NULL,
          payment_status text NOT NULL,
          rounding_method text NOT NULL,
          money_scale integer NOT NULL,
          source_document_id text NOT NULL,
          source_clause text NOT NULL,
          source_page integer NOT NULL,
          review_status text NOT NULL DEFAULT 'draft',
          created_by_subject text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          approved_at timestamptz,
          CONSTRAINT ck_rule_definition_family CHECK (
            rule_family IN (
              'rent', 'additional_rent', 'tax', 'interest', 'penalty'
            )
          ),
          CONSTRAINT ck_rule_definition_basis CHECK (
            calculation_basis IN (
              'fixed_per_day', 'per_area_per_day',
              'percent_of_base', 'percent_of_taxable'
            )
          ),
          CONSTRAINT ck_rule_definition_mechanism CHECK (
            tax_mechanism IN (
              'not_applicable', 'forward_charge', 'reverse_charge'
            )
          ),
          CONSTRAINT ck_rule_definition_payer CHECK (
            payer IN ('tenant', 'port', 'landlord', 'other')
          ),
          CONSTRAINT ck_rule_definition_payment CHECK (
            payment_status IN (
              'not_billed', 'billed_unpaid', 'partially_paid',
              'paid', 'not_applicable'
            )
          ),
          CONSTRAINT ck_rule_definition_review CHECK (
            review_status IN ('draft', 'approved', 'rejected', 'retired')
          ),
          CONSTRAINT ck_rule_definition_dates CHECK (valid_to > valid_from),
          CONSTRAINT ck_rule_definition_system CHECK (
            system_to IS NULL OR system_to > system_from
          ),
          CONSTRAINT ck_rule_definition_rate CHECK (rate_value >= 0),
          CONSTRAINT ck_rule_definition_rounding
            CHECK (rounding_method = 'ROUND_HALF_UP'),
          CONSTRAINT ck_rule_definition_scale CHECK (money_scale BETWEEN 0 AND 6),
          CONSTRAINT ck_rule_definition_page CHECK (source_page > 0),
          CONSTRAINT ck_rule_definition_approval_time CHECK (
            (review_status = 'approved' AND approved_at IS NOT NULL)
            OR review_status <> 'approved'
          ),
          CONSTRAINT ex_rule_definition_approved_overlap
            EXCLUDE USING gist (
              rule_family WITH =,
              component_code WITH =,
              jurisdiction WITH =,
              applicability_key WITH =,
              valid_period WITH &&,
              system_period WITH &&
            ) WHERE (review_status = 'approved')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.rule_approval (
          rule_id text NOT NULL
            REFERENCES pms_rules.rule_definition(rule_id) ON DELETE RESTRICT,
          approval_role text NOT NULL,
          decision text NOT NULL,
          reviewer_subject text NOT NULL,
          remarks text NOT NULL,
          reviewed_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (rule_id, approval_role),
          CONSTRAINT ck_rule_approval_role
            CHECK (approval_role IN ('finance', 'legal')),
          CONSTRAINT ck_rule_approval_decision
            CHECK (decision IN ('approved', 'rejected'))
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION pms_rules.enforce_rule_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          approval_count integer;
          self_approval_count integer;
        BEGIN
          IF NEW.review_status = 'approved'
             AND OLD.review_status <> 'approved' THEN
            IF NEW.source_document_id = ''
               OR NEW.source_clause = ''
               OR NEW.source_page < 1 THEN
              RAISE EXCEPTION 'approved rule requires document evidence';
            END IF;
            SELECT count(*),
                   count(*) FILTER (
                     WHERE reviewer_subject = NEW.created_by_subject
                   )
            INTO approval_count, self_approval_count
            FROM pms_rules.rule_approval
            WHERE rule_id = NEW.rule_id
              AND decision = 'approved'
              AND approval_role IN ('finance', 'legal');
            IF approval_count <> 2 OR self_approval_count <> 0 THEN
              RAISE EXCEPTION
                'approved rule requires independent Finance and Legal approvals';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_rule_definition_approval
        BEFORE UPDATE OF review_status ON pms_rules.rule_definition
        FOR EACH ROW EXECUTE FUNCTION pms_rules.enforce_rule_approval()
        """
    )
    op.execute(
        """
        CREATE FUNCTION pms_rules.prevent_approval_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'rule approvals are immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_rule_approval_immutable
        BEFORE UPDATE OR DELETE ON pms_rules.rule_approval
        FOR EACH ROW EXECUTE FUNCTION pms_rules.prevent_approval_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION pms_rules.prevent_calculation_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'completed calculation evidence is immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.calculation_input (
          input_snapshot_id text PRIMARY KEY,
          request_hash varchar(64) NOT NULL,
          canonical_lease_id text NOT NULL,
          canonical_tenant_id text NOT NULL,
          period_from date NOT NULL,
          period_to date NOT NULL,
          mode text NOT NULL,
          as_recorded_at timestamptz,
          input_payload jsonb NOT NULL,
          historical_bill_id text,
          historical_billed_amount numeric(24, 6),
          requested_by_subject text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_calculation_input_hash UNIQUE (request_hash, mode),
          CONSTRAINT ck_calculation_input_hash
            CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_calculation_input_dates CHECK (period_to > period_from),
          CONSTRAINT ck_calculation_input_mode CHECK (
            mode IN (
              'ORIGINAL_AS_RECORDED',
              'CURRENT_APPROVED_INTERPRETATION'
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.calculation_result (
          calculation_id text PRIMARY KEY,
          input_snapshot_id text NOT NULL
            REFERENCES pms_rules.calculation_input(input_snapshot_id)
            ON DELETE RESTRICT,
          canonical_tenant_id text NOT NULL,
          status text NOT NULL,
          mode text NOT NULL,
          rent_total numeric(24, 6) NOT NULL,
          additional_total numeric(24, 6) NOT NULL,
          tax_total numeric(24, 6) NOT NULL,
          grand_total numeric(24, 6) NOT NULL,
          discrepancy_amount numeric(24, 6),
          warnings jsonb NOT NULL,
          calculation_version text NOT NULL,
          replay_of_calculation_id text
            REFERENCES pms_rules.calculation_result(calculation_id)
            ON DELETE RESTRICT,
          result_hash varchar(64) NOT NULL,
          result_payload jsonb NOT NULL,
          completed_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_calculation_result_input UNIQUE (input_snapshot_id),
          CONSTRAINT ck_calculation_result_status
            CHECK (status IN ('completed', 'review_required')),
          CONSTRAINT ck_calculation_result_hash
            CHECK (result_hash ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.calculation_segment (
          calculation_id text NOT NULL
            REFERENCES pms_rules.calculation_result(calculation_id)
            ON DELETE RESTRICT,
          segment_number integer NOT NULL,
          period_from date NOT NULL,
          period_to date NOT NULL,
          day_count integer NOT NULL,
          proration_method text NOT NULL,
          area_sqm numeric(24, 8) NOT NULL,
          base_rent_per_day numeric(24, 10) NOT NULL,
          jurisdiction text NOT NULL,
          applicability_key text NOT NULL,
          tenant_registered boolean NOT NULL,
          use_code text NOT NULL,
          agreement_version text NOT NULL,
          lease_status text NOT NULL,
          base_rent numeric(24, 6) NOT NULL,
          additional_charges numeric(24, 6) NOT NULL,
          taxable_value numeric(24, 6) NOT NULL,
          tax_amount numeric(24, 6) NOT NULL,
          total_amount numeric(24, 6) NOT NULL,
          rent_rule_id text NOT NULL
            REFERENCES pms_rules.rule_definition(rule_id) ON DELETE RESTRICT,
          tax_rule_ids text[] NOT NULL,
          rounding_method text NOT NULL,
          calculation_version text NOT NULL,
          review_status text NOT NULL,
          PRIMARY KEY (calculation_id, segment_number),
          CONSTRAINT ck_calculation_segment_dates CHECK (period_to > period_from),
          CONSTRAINT ck_calculation_segment_days CHECK (day_count > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.calculation_component (
          calculation_id text NOT NULL,
          segment_number integer NOT NULL,
          component_number integer NOT NULL,
          component_code text NOT NULL,
          rule_family text NOT NULL,
          rule_id text NOT NULL
            REFERENCES pms_rules.rule_definition(rule_id) ON DELETE RESTRICT,
          calculation_basis text NOT NULL,
          rate_value numeric(24, 10) NOT NULL,
          taxable_value numeric(24, 6) NOT NULL,
          calculated_amount numeric(24, 6) NOT NULL,
          tax_mechanism text NOT NULL,
          payer text NOT NULL,
          invoice_inclusion boolean NOT NULL,
          payment_status text NOT NULL,
          source_document_id text NOT NULL,
          source_clause text NOT NULL,
          source_page integer NOT NULL,
          PRIMARY KEY (calculation_id, segment_number, component_number),
          FOREIGN KEY (calculation_id, segment_number)
            REFERENCES pms_rules.calculation_segment(
              calculation_id, segment_number
            ) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pms_rules.gold_case (
          gold_case_id text PRIMARY KEY,
          title text NOT NULL,
          input_payload jsonb NOT NULL,
          expected_payload jsonb NOT NULL,
          finance_approved_by text,
          finance_approved_at timestamptz,
          legal_approved_by text,
          legal_approved_at timestamptz,
          status text NOT NULL DEFAULT 'draft',
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_gold_case_status CHECK (
            status IN ('draft', 'approved', 'retired')
          ),
          CONSTRAINT ck_gold_case_approved CHECK (
            status <> 'approved'
            OR (
              finance_approved_by IS NOT NULL
              AND finance_approved_at IS NOT NULL
              AND legal_approved_by IS NOT NULL
              AND legal_approved_at IS NOT NULL
              AND finance_approved_by <> legal_approved_by
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE VIEW pms_rules.calculation_discrepancy
        WITH (security_barrier = true) AS
        SELECT result.calculation_id, input.historical_bill_id,
               input.historical_billed_amount, result.grand_total,
               result.discrepancy_amount, result.status, result.mode,
               result.calculation_version, result.completed_at,
               result.canonical_tenant_id
        FROM pms_rules.calculation_result AS result
        JOIN pms_rules.calculation_input AS input
          ON input.input_snapshot_id = result.input_snapshot_id
        WHERE input.historical_bill_id IS NOT NULL
        """
    )
    for table in (
        "calculation_input",
        "calculation_result",
        "calculation_segment",
        "calculation_component",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON pms_rules.{table}
            FOR EACH ROW EXECUTE FUNCTION pms_rules.prevent_calculation_mutation()
            """
        )

    for table in (
        "rule_candidate",
        "rule_definition",
        "rule_approval",
        "calculation_input",
        "calculation_result",
        "calculation_segment",
        "calculation_component",
        "gold_case",
    ):
        op.execute(f"ALTER TABLE pms_rules.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE pms_rules.{table} FORCE ROW LEVEL SECURITY")

    reader = _rule_reader()
    for table in ("rule_candidate", "rule_definition", "rule_approval", "gold_case"):
        op.execute(
            f"""
            CREATE POLICY {table}_select ON pms_rules.{table}
            FOR SELECT USING ({reader})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_write ON pms_rules.{table}
            FOR ALL USING (
              pms_app.has_role('Finance Officer')
              OR pms_app.has_role('Legal Officer')
              OR pms_app.has_role('Administrator')
            )
            WITH CHECK (
              pms_app.has_role('Finance Officer')
              OR pms_app.has_role('Legal Officer')
              OR pms_app.has_role('Administrator')
            )
            """
        )

    calculation_scope = _calculation_scope()
    for table in ("calculation_input", "calculation_result"):
        op.execute(
            f"""
            CREATE POLICY {table}_select ON pms_rules.{table}
            FOR SELECT USING ({calculation_scope})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_insert ON pms_rules.{table}
            FOR INSERT WITH CHECK ({calculation_scope})
            """
        )
    for table in ("calculation_segment", "calculation_component"):
        op.execute(
            f"""
            CREATE POLICY {table}_select ON pms_rules.{table}
            FOR SELECT USING (
              EXISTS (
                SELECT 1 FROM pms_rules.calculation_result AS result
                WHERE result.calculation_id = {table}.calculation_id
              )
            )
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_insert ON pms_rules.{table}
            FOR INSERT WITH CHECK (
              EXISTS (
                SELECT 1 FROM pms_rules.calculation_result AS result
                WHERE result.calculation_id = {table}.calculation_id
              )
            )
            """
        )


def downgrade() -> None:
    """Remove only Phase 10 objects."""

    op.execute("DROP VIEW IF EXISTS pms_rules.calculation_discrepancy")
    for table in (
        "gold_case",
        "calculation_component",
        "calculation_segment",
        "calculation_result",
        "calculation_input",
        "rule_approval",
        "rule_definition",
        "rule_candidate",
    ):
        op.execute(f"DROP TABLE IF EXISTS pms_rules.{table}")
    op.execute("DROP FUNCTION IF EXISTS pms_rules.prevent_approval_mutation()")
    op.execute("DROP FUNCTION IF EXISTS pms_rules.prevent_calculation_mutation()")
    op.execute("DROP FUNCTION IF EXISTS pms_rules.enforce_rule_approval()")
