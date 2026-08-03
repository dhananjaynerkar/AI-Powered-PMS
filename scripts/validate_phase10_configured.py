"""Transactional live gate for Phase 10 mechanics, RLS, trace, and rollback."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from pms_common.security import (
    AuthorizationContext,
    Classification,
    UserRole,
)
from pms_common.settings import Settings
from pms_rule_engine.engine import RuleCalculationEngine
from pms_rule_engine.models import LeaseCalculationRequest
from pms_rule_engine.repository import PostgresRuleRepository
from pms_rule_engine.service import RuleCalculationService
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import IntegrityError

TARGET_REVISION = "20260730_0009"
RUNTIME_ROLE = "pms_app_runtime"


def _admin_url(settings: Settings) -> str:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_database,
    ).render_as_string(hide_password=False)


def _insert_rule(
    connection: object,
    *,
    rule_id: str,
    family: str,
    component: str,
    basis: str,
    rate: str,
    mechanism: str,
    applicability: str = "SYNTHETIC",
    proration_method: str = "actual_days_half_open",
) -> None:
    from sqlalchemy import Connection

    if not isinstance(connection, Connection):
        raise TypeError("SQLAlchemy connection is required")
    connection.execute(
        text(
            """
            INSERT INTO pms_rules.rule_definition (
              rule_id, rule_family, component_code, jurisdiction,
              applicability_key, valid_from, valid_to, calculation_basis,
              proration_method,
              rate_value, tax_mechanism, payer, invoice_inclusion,
              payment_status, rounding_method, money_scale,
              source_document_id, source_clause, source_page,
              review_status, created_by_subject
            ) VALUES (
              :rule_id, :family, :component, 'TECHNICAL_TEST_ONLY',
              :applicability, DATE '2017-01-01', DATE '2027-01-01', :basis,
              :proration_method,
              :rate, :mechanism, 'tenant', true, 'not_billed',
              'ROUND_HALF_UP', 2, 'synthetic-transactional-evidence',
              'TEST-NOT-AUTHORITATIVE', 1, 'draft', 'technical-maker'
            )
            """
        ),
        {
            "rule_id": rule_id,
            "family": family,
            "component": component,
            "basis": basis,
            "rate": Decimal(rate),
            "mechanism": mechanism,
            "applicability": applicability,
            "proration_method": proration_method,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO pms_rules.rule_approval (
              rule_id, approval_role, decision, reviewer_subject, remarks
            ) VALUES
              (:rule_id, 'finance', 'approved', 'synthetic-finance-reviewer',
               'transactional technical validation only'),
              (:rule_id, 'legal', 'approved', 'synthetic-legal-reviewer',
               'transactional technical validation only')
            """
        ),
        {"rule_id": rule_id},
    )
    connection.execute(
        text(
            """
            UPDATE pms_rules.rule_definition
            SET review_status = 'approved', approved_at = now()
            WHERE rule_id = :rule_id
            """
        ),
        {"rule_id": rule_id},
    )


def _request() -> LeaseCalculationRequest:
    return LeaseCalculationRequest(
        canonical_lease_id="synthetic-live-lease",
        canonical_tenant_id="synthetic-live-tenant",
        period_from=date(2024, 1, 1),
        period_to=date(2024, 1, 11),
        area_sqm=Decimal("10"),
        base_rent_per_day=Decimal("0"),
        jurisdiction="TECHNICAL_TEST_ONLY",
        applicability_key="SYNTHETIC",
        tenant_registered=True,
        use_code="TEST",
        agreement_version="TEST-1",
        lease_status="TEST",
        required_additional_components=("ADDITIONAL",),
        required_tax_components=("GST",),
        financial_year_boundaries=False,
        historical_bill_id="synthetic-live-bill",
        historical_billed_amount=Decimal("120.00"),
    )


def _monthly_request() -> LeaseCalculationRequest:
    return LeaseCalculationRequest(
        canonical_lease_id="synthetic-monthly-lease",
        canonical_tenant_id="synthetic-live-tenant",
        period_from=date(2017, 10, 1),
        period_to=date(2017, 11, 1),
        area_sqm=Decimal("1"),
        base_rent_per_day=Decimal("0"),
        jurisdiction="TECHNICAL_TEST_ONLY",
        applicability_key="SYNTHETIC_MONTHLY",
        tenant_registered=True,
        use_code="TEST",
        agreement_version="TEST-1",
        lease_status="TEST",
        financial_year_boundaries=False,
    )


def main() -> int:
    settings = Settings()
    engine = create_engine(_admin_url(settings), pool_pre_ping=True)
    persisted_before: tuple[int, int, int]
    with engine.connect() as connection:
        persisted_before = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM pms_rules.rule_definition),
                  (SELECT count(*) FROM pms_rules.gold_case),
                  (SELECT count(*) FROM pms_rules.calculation_result)
                """
            )
        ).one()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            revision = connection.execute(
                text("SELECT version_num FROM pms_app.alembic_version")
            ).scalar_one()
            if revision != TARGET_REVISION:
                raise RuntimeError(f"expected {TARGET_REVISION}; found {revision}")

            unique = uuid4().hex
            rent_id = f"synthetic-rent-{unique}"
            additional_id = f"synthetic-additional-{unique}"
            tax_id = f"synthetic-tax-{unique}"
            monthly_id = f"synthetic-monthly-rent-{unique}"
            _insert_rule(
                connection,
                rule_id=rent_id,
                family="rent",
                component="RENT",
                basis="fixed_per_day",
                rate="10",
                mechanism="not_applicable",
            )
            _insert_rule(
                connection,
                rule_id=additional_id,
                family="additional_rent",
                component="ADDITIONAL",
                basis="percent_of_base",
                rate="5",
                mechanism="not_applicable",
            )
            _insert_rule(
                connection,
                rule_id=tax_id,
                family="tax",
                component="GST",
                basis="percent_of_taxable",
                rate="18",
                mechanism="forward_charge",
            )
            _insert_rule(
                connection,
                rule_id=monthly_id,
                family="rent",
                component="RENT",
                basis="per_area_per_month",
                rate="851.20",
                mechanism="not_applicable",
                applicability="SYNTHETIC_MONTHLY",
                proration_method="full_calendar_months",
            )

            overlap_rejected = False
            nested = connection.begin_nested()
            try:
                _insert_rule(
                    connection,
                    rule_id=f"synthetic-overlap-{unique}",
                    family="tax",
                    component="GST",
                    basis="percent_of_taxable",
                    rate="19",
                    mechanism="forward_charge",
                )
            except IntegrityError:
                nested.rollback()
                overlap_rejected = True
            else:
                nested.rollback()
            if not overlap_rejected:
                raise RuntimeError("overlapping approved rule was not rejected")

            connection.execute(
                text(
                    """
                    INSERT INTO pms_rules.gold_case (
                      gold_case_id, title, input_payload, expected_payload,
                      finance_approved_by, finance_approved_at,
                      legal_approved_by, legal_approved_at, status
                    ) VALUES (
                      :gold_case_id, 'Synthetic transactional technical case',
                      CAST(:input_payload AS jsonb), CAST(:expected_payload AS jsonb),
                      'synthetic-finance-reviewer', now(),
                      'synthetic-legal-reviewer', now(), 'approved'
                    )
                    """
                ),
                {
                    "gold_case_id": f"synthetic-gold-{unique}",
                    "input_payload": _request().model_dump_json(),
                    "expected_payload": '{"grand_total":"123.90"}',
                },
            )

            connection.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))
            context = AuthorizationContext(
                subject="phase10-live-finance",
                roles=frozenset({UserRole.FINANCE_OFFICER}),
                tenant_id=None,
                department_id="phase10-validation",
                unit_id="phase10-validation",
                classification=Classification.RESTRICTED,
            )
            service = RuleCalculationService(
                PostgresRuleRepository(connection, context),
                context,
                RuleCalculationEngine(
                    calculation_version=settings.rule_calculation_version,
                    max_segments=settings.rule_max_segments,
                ),
            )
            first = service.calculate(_request())
            second = service.calculate(_request())
            monthly = service.calculate(_monthly_request())
            if first.grand_total != Decimal("123.90"):
                raise RuntimeError("configured Decimal result did not match expected value")
            if first.discrepancy_amount != Decimal("3.90"):
                raise RuntimeError("historical discrepancy is incorrect")
            if first.calculation_id != second.calculation_id:
                raise RuntimeError("idempotent request created a second result")
            if monthly.grand_total != Decimal("851.20"):
                raise RuntimeError("configured monthly rate result is incorrect")
            if len(first.segments) != 1 or len(first.segments[0].components) != 3:
                raise RuntimeError("calculation trace is incomplete")
            if {
                item.source_document_id
                for item in first.segments[0].components
            } != {"synthetic-transactional-evidence"}:
                raise RuntimeError("source evidence trace is incomplete")
            connection.execute(text("RESET ROLE"))
            trace_counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM pms_rules.calculation_segment
                        WHERE calculation_id = :calculation_id),
                      (SELECT count(*) FROM pms_rules.calculation_component
                        WHERE calculation_id = :calculation_id),
                      (SELECT count(*) FROM pms_audit.security_event
                        WHERE query_category = 'RULE_CALCULATION'
                          AND entity_scope ->> 'calculation_id' = :calculation_id)
                    """
                ),
                {"calculation_id": first.calculation_id},
            ).one()
            if tuple(int(value) for value in trace_counts) != (1, 3, 1):
                raise RuntimeError("persisted trace or audit count is incorrect")
            print(f"PASS configured_revision={revision}")
            print("PASS synthetic_expected_grand_total=123.90")
            print("PASS synthetic_historical_discrepancy=3.90")
            print("PASS synthetic_full_month_rate=851.20")
            print("PASS approved_overlap_rejected=true")
            print("PASS idempotent_result_reused=true")
            print("PASS persisted_segments_in_transaction=1")
            print("PASS persisted_components_in_transaction=3")
            print("PASS audit_events_in_transaction=1")
            print("PASS probabilistic_final_amounts=false")
        finally:
            transaction.rollback()
    with engine.connect() as connection:
        persisted_after = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM pms_rules.rule_definition),
                  (SELECT count(*) FROM pms_rules.gold_case),
                  (SELECT count(*) FROM pms_rules.calculation_result)
                """
            )
        ).one()
    engine.dispose()
    if persisted_after != persisted_before:
        raise RuntimeError("transactional technical fixtures were persisted")
    print("PASS transactional_test_data_persisted=false")
    print(f"PASS persistent_approved_rules={persisted_after[0]}")
    print(f"PASS persistent_approved_gold_cases={persisted_after[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
