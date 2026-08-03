"""Typed legal-time, system-time, input and calculation trace contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RuleFamily(StrEnum):
    RENT = "rent"
    ADDITIONAL_RENT = "additional_rent"
    TAX = "tax"
    INTEREST = "interest"
    PENALTY = "penalty"


class CalculationBasis(StrEnum):
    FIXED_PER_DAY = "fixed_per_day"
    PER_AREA_PER_DAY = "per_area_per_day"
    FIXED_PER_MONTH = "fixed_per_month"
    PER_AREA_PER_MONTH = "per_area_per_month"
    PERCENT_OF_BASE = "percent_of_base"
    PERCENT_OF_TAXABLE = "percent_of_taxable"


class ProrationMethod(StrEnum):
    ACTUAL_DAYS_HALF_OPEN = "actual_days_half_open"
    FULL_CALENDAR_MONTHS = "full_calendar_months"


class TaxMechanism(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    FORWARD_CHARGE = "forward_charge"
    REVERSE_CHARGE = "reverse_charge"


class Payer(StrEnum):
    TENANT = "tenant"
    PORT = "port"
    LANDLORD = "landlord"
    OTHER = "other"


class PaymentStatus(StrEnum):
    NOT_BILLED = "not_billed"
    BILLED_UNPAID = "billed_unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    NOT_APPLICABLE = "not_applicable"


class CalculationMode(StrEnum):
    ORIGINAL_AS_RECORDED = "ORIGINAL_AS_RECORDED"
    CURRENT_APPROVED_INTERPRETATION = "CURRENT_APPROVED_INTERPRETATION"


class CalculationStatus(StrEnum):
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"


class RuleDefinition(BaseModel):
    """One approved, source-linked rule version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1, max_length=200)
    rule_family: RuleFamily
    component_code: str = Field(min_length=1, max_length=80)
    jurisdiction: str = Field(min_length=1, max_length=120)
    applicability_key: str = Field(min_length=1, max_length=300)
    valid_from: date
    valid_to: date
    system_from: datetime
    system_to: datetime | None = None
    calculation_basis: CalculationBasis
    proration_method: ProrationMethod
    rate_value: Decimal = Field(ge=0)
    tax_mechanism: TaxMechanism = TaxMechanism.NOT_APPLICABLE
    payer: Payer = Payer.TENANT
    invoice_inclusion: bool = True
    payment_status: PaymentStatus = PaymentStatus.NOT_BILLED
    rounding_method: str = "ROUND_HALF_UP"
    money_scale: int = Field(default=2, ge=0, le=6)
    source_document_id: str = Field(min_length=1, max_length=200)
    source_clause: str = Field(min_length=1, max_length=200)
    source_page: int = Field(ge=1)
    review_status: str = Field(pattern="^approved$")

    @field_validator("rate_value")
    @classmethod
    def finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("rate_value must be finite")
        return value

    @model_validator(mode="after")
    def valid_periods(self) -> RuleDefinition:
        if self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        if self.system_to is not None and self.system_to <= self.system_from:
            raise ValueError("system_to must be after system_from")
        monthly = self.calculation_basis in {
            CalculationBasis.FIXED_PER_MONTH,
            CalculationBasis.PER_AREA_PER_MONTH,
        }
        if monthly != (self.proration_method == ProrationMethod.FULL_CALENDAR_MONTHS):
            raise ValueError("calculation basis and proration method are inconsistent")
        return self


class LeaseFactChange(BaseModel):
    """Effective-dated fact snapshot beginning at ``effective_from``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_from: date
    area_sqm: Decimal | None = Field(default=None, gt=0)
    base_rent_per_day: Decimal | None = Field(default=None, ge=0)
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=120)
    applicability_key: str | None = Field(default=None, min_length=1, max_length=300)
    tenant_registered: bool | None = None
    use_code: str | None = Field(default=None, min_length=1, max_length=100)
    agreement_version: str | None = Field(default=None, min_length=1, max_length=100)
    lease_status: str | None = Field(default=None, min_length=1, max_length=80)
    exempt_tax_components: tuple[str, ...] | None = None


class LeaseCalculationRequest(BaseModel):
    """Complete authoritative input snapshot for a half-open calculation period."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_lease_id: str = Field(min_length=1, max_length=200)
    canonical_tenant_id: str = Field(min_length=1, max_length=200)
    period_from: date
    period_to: date
    area_sqm: Decimal = Field(gt=0)
    base_rent_per_day: Decimal = Field(ge=0)
    jurisdiction: str = Field(min_length=1, max_length=120)
    applicability_key: str = Field(min_length=1, max_length=300)
    tenant_registered: bool
    use_code: str = Field(min_length=1, max_length=100)
    agreement_version: str = Field(min_length=1, max_length=100)
    lease_status: str = Field(min_length=1, max_length=80)
    exempt_tax_components: tuple[str, ...] = ()
    changes: tuple[LeaseFactChange, ...] = ()
    required_tax_components: tuple[str, ...] = ()
    required_additional_components: tuple[str, ...] = ()
    financial_year_boundaries: bool = True
    mode: CalculationMode = CalculationMode.CURRENT_APPROVED_INTERPRETATION
    as_recorded_at: datetime | None = None
    historical_bill_id: str | None = Field(default=None, max_length=200)
    historical_billed_amount: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_request(self) -> LeaseCalculationRequest:
        if self.period_to <= self.period_from:
            raise ValueError("period_to must be after period_from")
        dates = [change.effective_from for change in self.changes]
        if dates != sorted(set(dates)):
            raise ValueError("changes must have unique ascending effective_from dates")
        if any(not self.period_from < value < self.period_to for value in dates):
            raise ValueError("changes must fall inside the requested period")
        if (
            self.mode == CalculationMode.ORIGINAL_AS_RECORDED
            and self.as_recorded_at is None
        ):
            raise ValueError("ORIGINAL_AS_RECORDED requires as_recorded_at")
        if (self.historical_bill_id is None) != (
            self.historical_billed_amount is None
        ):
            raise ValueError("historical bill ID and amount must be provided together")
        if len(set(self.required_tax_components)) != len(
            self.required_tax_components
        ):
            raise ValueError("required_tax_components must be unique")
        if len(set(self.required_additional_components)) != len(
            self.required_additional_components
        ):
            raise ValueError("required_additional_components must be unique")
        return self


class ReplayCalculationRequest(BaseModel):
    """Select the governed interpretation used for replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: CalculationMode
    as_recorded_at: datetime | None = None

    @model_validator(mode="after")
    def validate_replay(self) -> ReplayCalculationRequest:
        if (
            self.mode == CalculationMode.ORIGINAL_AS_RECORDED
            and self.as_recorded_at is None
        ):
            raise ValueError("ORIGINAL_AS_RECORDED requires as_recorded_at")
        if (
            self.mode == CalculationMode.CURRENT_APPROVED_INTERPRETATION
            and self.as_recorded_at is not None
        ):
            raise ValueError("current interpretation cannot use as_recorded_at")
        return self


class CalculationComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_code: str
    rule_family: RuleFamily
    rule_id: str
    calculation_basis: CalculationBasis
    proration_method: ProrationMethod
    rate_value: Decimal
    taxable_value: Decimal
    calculated_amount: Decimal
    tax_mechanism: TaxMechanism
    payer: Payer
    invoice_inclusion: bool
    payment_status: PaymentStatus
    source_document_id: str
    source_clause: str
    source_page: int


class CalculationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_number: int = Field(ge=1)
    period_from: date
    period_to: date
    day_count: int = Field(ge=1)
    proration_method: str
    area_sqm: Decimal
    base_rent_per_day: Decimal
    jurisdiction: str
    applicability_key: str
    tenant_registered: bool
    use_code: str
    agreement_version: str
    lease_status: str
    base_rent: Decimal
    additional_charges: Decimal
    taxable_value: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    rent_rule_id: str
    tax_rule_ids: tuple[str, ...]
    components: tuple[CalculationComponent, ...]
    rounding_method: str
    calculation_version: str
    review_status: str


class CalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calculation_id: str
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: CalculationStatus
    mode: CalculationMode
    segments: tuple[CalculationSegment, ...] = ()
    rent_total: Decimal
    additional_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    warnings: tuple[str, ...] = ()
    review_required: bool
    calculation_version: str
    input_snapshot_id: str
    replay_of_calculation_id: str | None = None
    historical_bill_id: str | None = None
    historical_billed_amount: Decimal | None = None
    discrepancy_amount: Decimal | None = None
