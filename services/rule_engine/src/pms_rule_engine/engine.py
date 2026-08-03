"""Pure Decimal calculation engine with legal-time and system-time resolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from pms_rule_engine.models import (
    CalculationBasis,
    CalculationComponent,
    CalculationMode,
    CalculationResult,
    CalculationSegment,
    CalculationStatus,
    LeaseCalculationRequest,
    LeaseFactChange,
    RuleDefinition,
    RuleFamily,
)


class RuleResolutionError(ValueError):
    """Raised when a segment cannot resolve exactly one approved rule."""


@dataclass(frozen=True, slots=True)
class _FactState:
    area_sqm: Decimal
    base_rent_per_day: Decimal
    jurisdiction: str
    applicability_key: str
    tenant_registered: bool
    use_code: str
    agreement_version: str
    lease_status: str
    exempt_tax_components: tuple[str, ...]


class RuleCalculationEngine:
    """Calculate exact amounts without LLMs, vectors, floats or hidden defaults."""

    def __init__(self, *, calculation_version: str = "1.0", max_segments: int = 500) -> None:
        self._version = calculation_version
        self._max_segments = max_segments

    def calculate(
        self,
        request: LeaseCalculationRequest,
        rules: tuple[RuleDefinition, ...],
        *,
        replay_of_calculation_id: str | None = None,
    ) -> CalculationResult:
        request_hash = self._request_hash(request)
        input_snapshot_id = f"input-{request_hash}"
        try:
            segments = self._segments(request, rules)
        except RuleResolutionError as error:
            return CalculationResult(
                calculation_id=str(uuid4()),
                request_hash=request_hash,
                status=CalculationStatus.REVIEW_REQUIRED,
                mode=request.mode,
                rent_total=Decimal("0.00"),
                additional_total=Decimal("0.00"),
                tax_total=Decimal("0.00"),
                grand_total=Decimal("0.00"),
                warnings=(str(error),),
                review_required=True,
                calculation_version=self._version,
                input_snapshot_id=input_snapshot_id,
                replay_of_calculation_id=replay_of_calculation_id,
                historical_bill_id=request.historical_bill_id,
                historical_billed_amount=request.historical_billed_amount,
            )
        rent_total = sum((item.base_rent for item in segments), Decimal("0.00"))
        additional_total = sum(
            (item.additional_charges for item in segments),
            Decimal("0.00"),
        )
        tax_total = sum((item.tax_amount for item in segments), Decimal("0.00"))
        grand_total = self._money(rent_total + additional_total + tax_total)
        discrepancy = (
            self._money(grand_total - request.historical_billed_amount)
            if request.historical_billed_amount is not None
            else None
        )
        return CalculationResult(
            calculation_id=str(uuid4()),
            request_hash=request_hash,
            status=CalculationStatus.COMPLETED,
            mode=request.mode,
            segments=segments,
            rent_total=self._money(rent_total),
            additional_total=self._money(additional_total),
            tax_total=self._money(tax_total),
            grand_total=grand_total,
            review_required=False,
            calculation_version=self._version,
            input_snapshot_id=input_snapshot_id,
            replay_of_calculation_id=replay_of_calculation_id,
            historical_bill_id=request.historical_bill_id,
            historical_billed_amount=request.historical_billed_amount,
            discrepancy_amount=discrepancy,
        )

    def _segments(
        self,
        request: LeaseCalculationRequest,
        rules: tuple[RuleDefinition, ...],
    ) -> tuple[CalculationSegment, ...]:
        boundaries = {request.period_from, request.period_to}
        boundaries.update(change.effective_from for change in request.changes)
        for rule in rules:
            if request.period_from < rule.valid_from < request.period_to:
                boundaries.add(rule.valid_from)
            if request.period_from < rule.valid_to < request.period_to:
                boundaries.add(rule.valid_to)
        if request.financial_year_boundaries:
            for year in range(request.period_from.year, request.period_to.year + 1):
                boundary = date(year, 4, 1)
                if request.period_from < boundary < request.period_to:
                    boundaries.add(boundary)
        ordered = sorted(boundaries)
        if len(ordered) - 1 > self._max_segments:
            raise RuleResolutionError("segment count exceeds configured maximum")
        state = _FactState(
            area_sqm=request.area_sqm,
            base_rent_per_day=request.base_rent_per_day,
            jurisdiction=request.jurisdiction,
            applicability_key=request.applicability_key,
            tenant_registered=request.tenant_registered,
            use_code=request.use_code,
            agreement_version=request.agreement_version,
            lease_status=request.lease_status,
            exempt_tax_components=request.exempt_tax_components,
        )
        changes = {change.effective_from: change for change in request.changes}
        segments: list[CalculationSegment] = []
        for index, (period_from, period_to) in enumerate(
            zip(ordered, ordered[1:], strict=False),
            start=1,
        ):
            if period_from in changes:
                state = self._apply_change(state, changes[period_from])
            segments.append(
                self._calculate_segment(
                    request,
                    rules,
                    state,
                    index,
                    period_from,
                    period_to,
                )
            )
        return tuple(segments)

    @staticmethod
    def _apply_change(state: _FactState, change: LeaseFactChange) -> _FactState:
        updates = {
            name: value
            for name, value in change.model_dump(exclude={"effective_from"}).items()
            if value is not None
        }
        return replace(state, **updates)

    def _calculate_segment(
        self,
        request: LeaseCalculationRequest,
        rules: tuple[RuleDefinition, ...],
        state: _FactState,
        segment_number: int,
        period_from: date,
        period_to: date,
    ) -> CalculationSegment:
        day_count = (period_to - period_from).days
        rent_rule = self._resolve(
            rules,
            RuleFamily.RENT,
            "RENT",
            state,
            period_from,
            period_to,
            request,
        )
        base_seed = state.base_rent_per_day * Decimal(day_count)
        rent_amount = self._amount(
            rent_rule,
            period_from=period_from,
            period_to=period_to,
            days=day_count,
            area=state.area_sqm,
            base=base_seed,
            taxable=base_seed,
        )
        components = [
            self._component(rent_rule, rent_amount, base_seed)
        ]
        additional_amount = Decimal("0.00")
        for code in request.required_additional_components:
            rule = self._resolve(
                rules,
                RuleFamily.ADDITIONAL_RENT,
                code,
                state,
                period_from,
                period_to,
                request,
            )
            amount = self._amount(
                rule,
                period_from=period_from,
                period_to=period_to,
                days=day_count,
                area=state.area_sqm,
                base=rent_amount,
                taxable=rent_amount,
            )
            additional_amount += amount
            components.append(self._component(rule, amount, rent_amount))
        taxable = self._money(rent_amount + additional_amount)
        tax_amount = Decimal("0.00")
        tax_rule_ids: list[str] = []
        for code in request.required_tax_components:
            rule = self._resolve(
                rules,
                RuleFamily.TAX,
                code,
                state,
                period_from,
                period_to,
                request,
            )
            amount = (
                Decimal("0.00")
                if code in state.exempt_tax_components
                else self._amount(
                    rule,
                    period_from=period_from,
                    period_to=period_to,
                    days=day_count,
                    area=state.area_sqm,
                    base=rent_amount,
                    taxable=taxable,
                )
            )
            tax_amount += amount
            tax_rule_ids.append(rule.rule_id)
            components.append(self._component(rule, amount, taxable))
        total = self._money(taxable + tax_amount)
        return CalculationSegment(
            segment_number=segment_number,
            period_from=period_from,
            period_to=period_to,
            day_count=day_count,
            proration_method=rent_rule.proration_method.value,
            area_sqm=state.area_sqm,
            base_rent_per_day=state.base_rent_per_day,
            jurisdiction=state.jurisdiction,
            applicability_key=state.applicability_key,
            tenant_registered=state.tenant_registered,
            use_code=state.use_code,
            agreement_version=state.agreement_version,
            lease_status=state.lease_status,
            base_rent=self._money(rent_amount),
            additional_charges=self._money(additional_amount),
            taxable_value=taxable,
            tax_amount=self._money(tax_amount),
            total_amount=total,
            rent_rule_id=rent_rule.rule_id,
            tax_rule_ids=tuple(tax_rule_ids),
            components=tuple(components),
            rounding_method="ROUND_HALF_UP",
            calculation_version=self._version,
            review_status="approved_rules",
        )

    @staticmethod
    def _resolve(
        rules: tuple[RuleDefinition, ...],
        family: RuleFamily,
        component_code: str,
        state: _FactState,
        period_from: date,
        period_to: date,
        request: LeaseCalculationRequest,
    ) -> RuleDefinition:
        matches = [
            rule
            for rule in rules
            if rule.rule_family == family
            and rule.component_code == component_code
            and rule.jurisdiction == state.jurisdiction
            and rule.applicability_key == state.applicability_key
            and rule.valid_from <= period_from
            and rule.valid_to >= period_to
            and (
                (
                    request.mode == CalculationMode.CURRENT_APPROVED_INTERPRETATION
                    and rule.system_to is None
                )
                or (
                    request.mode == CalculationMode.ORIGINAL_AS_RECORDED
                    and request.as_recorded_at is not None
                    and rule.system_from <= request.as_recorded_at
                    and (
                        rule.system_to is None
                        or rule.system_to > request.as_recorded_at
                    )
                )
            )
        ]
        if len(matches) != 1:
            raise RuleResolutionError(
                f"{family.value}/{component_code} resolved {len(matches)} "
                f"approved rules for {period_from.isoformat()}..{period_to.isoformat()}"
            )
        return matches[0]

    def _amount(
        self,
        rule: RuleDefinition,
        *,
        period_from: date,
        period_to: date,
        days: int,
        area: Decimal,
        base: Decimal,
        taxable: Decimal,
    ) -> Decimal:
        if rule.calculation_basis == CalculationBasis.FIXED_PER_DAY:
            raw = rule.rate_value * Decimal(days)
        elif rule.calculation_basis == CalculationBasis.PER_AREA_PER_DAY:
            raw = rule.rate_value * area * Decimal(days)
        elif rule.calculation_basis in {
            CalculationBasis.FIXED_PER_MONTH,
            CalculationBasis.PER_AREA_PER_MONTH,
        }:
            months = self._full_calendar_months(period_from, period_to)
            raw = rule.rate_value * Decimal(months)
            if rule.calculation_basis == CalculationBasis.PER_AREA_PER_MONTH:
                raw *= area
        elif rule.calculation_basis == CalculationBasis.PERCENT_OF_BASE:
            raw = base * rule.rate_value / Decimal("100")
        elif rule.calculation_basis == CalculationBasis.PERCENT_OF_TAXABLE:
            raw = taxable * rule.rate_value / Decimal("100")
        else:  # pragma: no cover - exhaustive StrEnum boundary
            raise RuleResolutionError("unsupported calculation basis")
        return self._money(raw, scale=rule.money_scale)

    @staticmethod
    def _full_calendar_months(period_from: date, period_to: date) -> int:
        if period_from.day != 1 or period_to.day != 1:
            raise RuleResolutionError(
                "monthly rule requires full calendar-month boundaries; "
                "partial-month proration is not approved"
            )
        months = (
            (period_to.year - period_from.year) * 12
            + period_to.month
            - period_from.month
        )
        if months < 1:
            raise RuleResolutionError("monthly rule resolved an empty period")
        return months

    @staticmethod
    def _component(
        rule: RuleDefinition,
        amount: Decimal,
        taxable_value: Decimal,
    ) -> CalculationComponent:
        return CalculationComponent(
            component_code=rule.component_code,
            rule_family=rule.rule_family,
            rule_id=rule.rule_id,
            calculation_basis=rule.calculation_basis,
            proration_method=rule.proration_method,
            rate_value=rule.rate_value,
            taxable_value=taxable_value,
            calculated_amount=amount,
            tax_mechanism=rule.tax_mechanism,
            payer=rule.payer,
            invoice_inclusion=rule.invoice_inclusion,
            payment_status=rule.payment_status,
            source_document_id=rule.source_document_id,
            source_clause=rule.source_clause,
            source_page=rule.source_page,
        )

    @staticmethod
    def _money(value: Decimal, *, scale: int = 2) -> Decimal:
        quantum = Decimal(1).scaleb(-scale)
        return value.quantize(quantum, rounding=ROUND_HALF_UP)

    @staticmethod
    def _request_hash(request: LeaseCalculationRequest) -> str:
        payload = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
