"""Synthetic technical tests; these are not approved legal or Finance gold cases."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pms_rule_engine.engine import RuleCalculationEngine
from pms_rule_engine.models import (
    CalculationBasis,
    CalculationMode,
    CalculationStatus,
    LeaseCalculationRequest,
    LeaseFactChange,
    Payer,
    ProrationMethod,
    ReplayCalculationRequest,
    RuleDefinition,
    RuleFamily,
    TaxMechanism,
)

SYSTEM_START = datetime(2020, 1, 1, tzinfo=UTC)


def _rule(
    rule_id: str,
    family: RuleFamily,
    component: str,
    rate: str,
    *,
    basis: CalculationBasis,
    valid_from: date = date(2024, 1, 1),
    valid_to: date = date(2027, 1, 1),
    system_from: datetime = SYSTEM_START,
    system_to: datetime | None = None,
    mechanism: TaxMechanism = TaxMechanism.NOT_APPLICABLE,
    payer: Payer = Payer.TENANT,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        rule_family=family,
        component_code=component,
        jurisdiction="TECHNICAL_TEST_ONLY",
        applicability_key="SYNTHETIC",
        valid_from=valid_from,
        valid_to=valid_to,
        system_from=system_from,
        system_to=system_to,
        calculation_basis=basis,
        proration_method=(
            ProrationMethod.FULL_CALENDAR_MONTHS
            if basis
            in {
                CalculationBasis.FIXED_PER_MONTH,
                CalculationBasis.PER_AREA_PER_MONTH,
            }
            else ProrationMethod.ACTUAL_DAYS_HALF_OPEN
        ),
        rate_value=Decimal(rate),
        tax_mechanism=mechanism,
        payer=payer,
        invoice_inclusion=True,
        source_document_id="synthetic-test-evidence",
        source_clause="TEST-NOT-AUTHORITATIVE",
        source_page=1,
        review_status="approved",
    )


def _request(**updates: object) -> LeaseCalculationRequest:
    values: dict[str, object] = {
        "canonical_lease_id": "synthetic-lease",
        "canonical_tenant_id": "synthetic-tenant",
        "period_from": date(2024, 1, 1),
        "period_to": date(2024, 1, 11),
        "area_sqm": Decimal("10"),
        "base_rent_per_day": Decimal("0"),
        "jurisdiction": "TECHNICAL_TEST_ONLY",
        "applicability_key": "SYNTHETIC",
        "tenant_registered": True,
        "use_code": "TEST",
        "agreement_version": "TEST-1",
        "lease_status": "TEST",
        "required_additional_components": ("ADDITIONAL",),
        "required_tax_components": ("GST",),
        "financial_year_boundaries": False,
    }
    values.update(updates)
    return LeaseCalculationRequest.model_validate(values)


def _rules() -> tuple[RuleDefinition, ...]:
    return (
        _rule(
            "rent",
            RuleFamily.RENT,
            "RENT",
            "10",
            basis=CalculationBasis.FIXED_PER_DAY,
        ),
        _rule(
            "additional",
            RuleFamily.ADDITIONAL_RENT,
            "ADDITIONAL",
            "5",
            basis=CalculationBasis.PERCENT_OF_BASE,
        ),
        _rule(
            "gst",
            RuleFamily.TAX,
            "GST",
            "18",
            basis=CalculationBasis.PERCENT_OF_TAXABLE,
            mechanism=TaxMechanism.FORWARD_CHARGE,
        ),
    )


def test_exact_decimal_components_and_source_trace() -> None:
    result = RuleCalculationEngine().calculate(_request(), _rules())

    assert result.status == CalculationStatus.COMPLETED
    assert result.rent_total == Decimal("100.00")
    assert result.additional_total == Decimal("5.00")
    assert result.tax_total == Decimal("18.90")
    assert result.grand_total == Decimal("123.90")
    assert result.segments[0].day_count == 10
    assert [item.rule_id for item in result.segments[0].components] == [
        "rent",
        "additional",
        "gst",
    ]
    assert all(
        item.source_clause == "TEST-NOT-AUTHORITATIVE"
        for item in result.segments[0].components
    )


def test_half_open_leap_day_and_financial_year_boundaries() -> None:
    request = _request(
        period_from=date(2024, 2, 28),
        period_to=date(2024, 4, 2),
        required_additional_components=(),
        required_tax_components=(),
        financial_year_boundaries=True,
    )
    result = RuleCalculationEngine().calculate(request, (_rules()[0],))

    assert [segment.day_count for segment in result.segments] == [33, 1]
    assert result.rent_total == Decimal("340.00")


def test_mid_period_rule_change_creates_non_overlapping_segments() -> None:
    first = _rule(
        "rent-old",
        RuleFamily.RENT,
        "RENT",
        "10",
        basis=CalculationBasis.FIXED_PER_DAY,
        valid_to=date(2024, 1, 6),
    )
    second = _rule(
        "rent-new",
        RuleFamily.RENT,
        "RENT",
        "20",
        basis=CalculationBasis.FIXED_PER_DAY,
        valid_from=date(2024, 1, 6),
    )
    request = _request(
        required_additional_components=(),
        required_tax_components=(),
    )

    result = RuleCalculationEngine().calculate(request, (first, second))

    assert [item.rent_rule_id for item in result.segments] == ["rent-old", "rent-new"]
    assert result.rent_total == Decimal("150.00")
    assert result.segments[0].period_to == result.segments[1].period_from


def test_area_and_exemption_changes_are_effective_dated() -> None:
    rent = _rule(
        "rent-area",
        RuleFamily.RENT,
        "RENT",
        "2",
        basis=CalculationBasis.PER_AREA_PER_DAY,
    )
    tax = _rules()[2]
    request = _request(
        period_to=date(2024, 1, 5),
        required_additional_components=(),
        changes=(
            LeaseFactChange(
                effective_from=date(2024, 1, 3),
                area_sqm=Decimal("20"),
                exempt_tax_components=("GST",),
            ),
        ),
    )

    result = RuleCalculationEngine().calculate(request, (rent, tax))

    assert result.rent_total == Decimal("120.00")
    assert result.tax_total == Decimal("7.20")
    assert result.segments[1].components[1].calculated_amount == Decimal("0.00")


def test_missing_or_ambiguous_rule_returns_review_required() -> None:
    missing = RuleCalculationEngine().calculate(_request(), ())
    ambiguous = RuleCalculationEngine().calculate(
        _request(required_additional_components=(), required_tax_components=()),
        (_rules()[0], _rules()[0].model_copy(update={"rule_id": "duplicate"})),
    )

    assert missing.status == CalculationStatus.REVIEW_REQUIRED
    assert ambiguous.status == CalculationStatus.REVIEW_REQUIRED
    assert "resolved 0" in missing.warnings[0]
    assert "resolved 2" in ambiguous.warnings[0]


def test_original_and_current_modes_resolve_system_time() -> None:
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    old = _rule(
        "old-recorded",
        RuleFamily.RENT,
        "RENT",
        "10",
        basis=CalculationBasis.FIXED_PER_DAY,
        system_to=cutoff,
    )
    current = _rule(
        "current",
        RuleFamily.RENT,
        "RENT",
        "12",
        basis=CalculationBasis.FIXED_PER_DAY,
        system_from=cutoff,
    )
    common = {
        "required_additional_components": (),
        "required_tax_components": (),
    }
    original = _request(
        **common,
        mode=CalculationMode.ORIGINAL_AS_RECORDED,
        as_recorded_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    current_request = _request(**common)

    original_result = RuleCalculationEngine().calculate(original, (old, current))
    current_result = RuleCalculationEngine().calculate(current_request, (old, current))

    assert original_result.grand_total == Decimal("100.00")
    assert current_result.grand_total == Decimal("120.00")


def test_decimal_round_half_up_and_historical_discrepancy() -> None:
    rent = _rule(
        "fractional",
        RuleFamily.RENT,
        "RENT",
        "1.005",
        basis=CalculationBasis.FIXED_PER_DAY,
    )
    request = _request(
        period_to=date(2024, 1, 2),
        required_additional_components=(),
        required_tax_components=(),
        historical_bill_id="synthetic-bill",
        historical_billed_amount=Decimal("1.00"),
    )

    result = RuleCalculationEngine().calculate(request, (rent,))

    assert result.grand_total == Decimal("1.01")
    assert result.discrepancy_amount == Decimal("0.01")


def test_published_per_area_month_basis_is_exact_and_partial_month_fails_closed() -> None:
    rent = _rule(
        "sor-monthly-technical",
        RuleFamily.RENT,
        "RENT",
        "851.20",
        basis=CalculationBasis.PER_AREA_PER_MONTH,
        valid_from=date(2017, 10, 1),
        valid_to=date(2022, 10, 1),
    )
    full_month = _request(
        period_from=date(2017, 10, 1),
        period_to=date(2017, 11, 1),
        area_sqm=Decimal("1"),
        required_additional_components=(),
        required_tax_components=(),
    )
    partial_month = full_month.model_copy(
        update={"period_to": date(2017, 10, 16)}
    )

    exact = RuleCalculationEngine().calculate(full_month, (rent,))
    unresolved = RuleCalculationEngine().calculate(partial_month, (rent,))

    assert exact.grand_total == Decimal("851.20")
    assert exact.segments[0].proration_method == "full_calendar_months"
    assert unresolved.status == CalculationStatus.REVIEW_REQUIRED
    assert "partial-month proration is not approved" in unresolved.warnings[0]


def test_invalid_change_dates_and_original_mode_are_rejected() -> None:
    with pytest.raises(ValueError, match="inside the requested period"):
        _request(changes=(LeaseFactChange(effective_from=date(2024, 1, 1)),))
    with pytest.raises(ValueError, match="requires as_recorded_at"):
        _request(mode=CalculationMode.ORIGINAL_AS_RECORDED)
    with pytest.raises(ValueError, match="requires as_recorded_at"):
        ReplayCalculationRequest(mode=CalculationMode.ORIGINAL_AS_RECORDED)
    with pytest.raises(ValueError, match="must be unique"):
        _request(required_tax_components=("GST", "GST"))
