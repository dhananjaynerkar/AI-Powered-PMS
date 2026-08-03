"""PostgreSQL boundary for approved rules and immutable calculation traces."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pms_common.security import (
    AuthorizationContext,
    AuthorizationService,
    Permission,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from sqlalchemy import Connection, text

from pms_rule_engine.models import (
    CalculationResult,
    LeaseCalculationRequest,
    RuleDefinition,
)


class RuleRepositoryError(RuntimeError):
    """Raised when persisted rule evidence is incomplete or inconsistent."""


class PostgresRuleRepository:
    """Resolve reviewed rules and preserve replayable calculation evidence."""

    def __init__(
        self,
        connection: Connection,
        context: AuthorizationContext,
    ) -> None:
        self._connection = connection
        self._context = context
        self._authorization = AuthorizationService()
        apply_postgres_session_context(connection, context)

    def load_approved_rules(
        self,
        request: LeaseCalculationRequest,
    ) -> tuple[RuleDefinition, ...]:
        self._authorization.require_permission(
            self._context,
            Permission.RULE_CALCULATION,
        )
        recorded_at = request.as_recorded_at
        system_clause = (
            "AND system_to IS NULL"
            if recorded_at is None
            else """
                 AND system_from <= :recorded_at
                 AND (system_to IS NULL OR system_to > :recorded_at)
            """
        )
        rows = self._connection.execute(
            text(
                f"""
                SELECT rule_id, rule_family, component_code, jurisdiction,
                       applicability_key, valid_from, valid_to, system_from,
                       system_to, calculation_basis, rate_value, tax_mechanism,
                       proration_method,
                       payer, invoice_inclusion, payment_status, rounding_method,
                       money_scale, source_document_id, source_clause, source_page,
                       review_status
                FROM pms_rules.rule_definition
                WHERE review_status = 'approved'
                  AND jurisdiction = :jurisdiction
                  AND applicability_key = :applicability_key
                  AND valid_from < :period_to
                  AND valid_to > :period_from
                  {system_clause}
                ORDER BY valid_from, rule_family, component_code, rule_id
                """
            ),
            {
                "jurisdiction": request.jurisdiction,
                "applicability_key": request.applicability_key,
                "period_from": request.period_from,
                "period_to": request.period_to,
                **({"recorded_at": recorded_at} if recorded_at is not None else {}),
            },
        ).mappings()
        return tuple(RuleDefinition.model_validate(dict(row)) for row in rows)

    def existing(self, request_hash: str, mode: str) -> CalculationResult | None:
        row = self._connection.execute(
            text(
                """
                SELECT result.result_payload
                FROM pms_rules.calculation_result AS result
                JOIN pms_rules.calculation_input AS input
                  ON input.input_snapshot_id = result.input_snapshot_id
                WHERE input.request_hash = :request_hash
                  AND input.mode = :mode
                """
            ),
            {"request_hash": request_hash, "mode": mode},
        ).scalar_one_or_none()
        return CalculationResult.model_validate(row) if row is not None else None

    def save(
        self,
        request: LeaseCalculationRequest,
        result: CalculationResult,
    ) -> CalculationResult:
        self._authorization.require_permission(
            self._context,
            Permission.RULE_CALCULATION,
        )
        previous = self.existing(result.request_hash, result.mode.value)
        if previous is not None:
            return previous
        request_payload = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        result_payload = json.dumps(
            result.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        result_hash = hashlib.sha256(result_payload.encode("utf-8")).hexdigest()
        self._connection.execute(
            text(
                """
                INSERT INTO pms_rules.calculation_input (
                  input_snapshot_id, request_hash, canonical_lease_id,
                  canonical_tenant_id, period_from, period_to, mode,
                  as_recorded_at, input_payload, historical_bill_id,
                  historical_billed_amount, requested_by_subject
                ) VALUES (
                  :input_snapshot_id, :request_hash, :canonical_lease_id,
                  :canonical_tenant_id, :period_from, :period_to, :mode,
                  :as_recorded_at, CAST(:input_payload AS jsonb),
                  :historical_bill_id, :historical_billed_amount,
                  :requested_by_subject
                )
                """
            ),
            {
                "input_snapshot_id": result.input_snapshot_id,
                "request_hash": result.request_hash,
                "canonical_lease_id": request.canonical_lease_id,
                "canonical_tenant_id": request.canonical_tenant_id,
                "period_from": request.period_from,
                "period_to": request.period_to,
                "mode": result.mode.value,
                "as_recorded_at": request.as_recorded_at,
                "input_payload": request_payload,
                "historical_bill_id": request.historical_bill_id,
                "historical_billed_amount": request.historical_billed_amount,
                "requested_by_subject": self._context.subject,
            },
        )
        self._connection.execute(
            text(
                """
                INSERT INTO pms_rules.calculation_result (
                  calculation_id, input_snapshot_id, canonical_tenant_id,
                  status, mode, rent_total, additional_total, tax_total,
                  grand_total, discrepancy_amount, warnings,
                  calculation_version, replay_of_calculation_id, result_hash,
                  result_payload
                ) VALUES (
                  :calculation_id, :input_snapshot_id, :canonical_tenant_id,
                  :status, :mode, :rent_total, :additional_total, :tax_total,
                  :grand_total, :discrepancy_amount, CAST(:warnings AS jsonb),
                  :calculation_version, :replay_of_calculation_id, :result_hash,
                  CAST(:result_payload AS jsonb)
                )
                """
            ),
            {
                "calculation_id": result.calculation_id,
                "input_snapshot_id": result.input_snapshot_id,
                "canonical_tenant_id": request.canonical_tenant_id,
                "status": result.status.value,
                "mode": result.mode.value,
                "rent_total": result.rent_total,
                "additional_total": result.additional_total,
                "tax_total": result.tax_total,
                "grand_total": result.grand_total,
                "discrepancy_amount": result.discrepancy_amount,
                "warnings": json.dumps(result.warnings),
                "calculation_version": result.calculation_version,
                "replay_of_calculation_id": result.replay_of_calculation_id,
                "result_hash": result_hash,
                "result_payload": result_payload,
            },
        )
        for segment in result.segments:
            self._save_segment(result.calculation_id, segment.model_dump())
        self.audit(result)
        return result

    def load_input(self, calculation_id: str) -> LeaseCalculationRequest:
        self._authorization.require_permission(
            self._context,
            Permission.RULE_CALCULATION,
        )
        payload = self._connection.execute(
            text(
                """
                SELECT input.input_payload
                FROM pms_rules.calculation_input AS input
                JOIN pms_rules.calculation_result AS result
                  ON result.input_snapshot_id = input.input_snapshot_id
                WHERE result.calculation_id = :calculation_id
                """
            ),
            {"calculation_id": calculation_id},
        ).scalar_one_or_none()
        if payload is None:
            raise RuleRepositoryError("calculation input snapshot was not found")
        return LeaseCalculationRequest.model_validate(payload)

    def audit(self, result: CalculationResult) -> None:
        rule_ids = {
            component.rule_id
            for segment in result.segments
            for component in segment.components
        }
        write_audit_event(
            self._connection,
            create_audit_event(
                self._context,
                query_category="RULE_CALCULATION",
                entity_scope={"calculation_id": result.calculation_id},
                source_ids=sorted(rule_ids),
                rule_version=result.calculation_version,
                result_status=(
                    "REVIEW_REQUIRED" if result.review_required else "ALLOWED"
                ),
            ),
        )

    def _save_segment(self, calculation_id: str, segment: dict[str, Any]) -> None:
        components = segment.pop("components")
        segment["tax_rule_ids"] = list(segment["tax_rule_ids"])
        self._connection.execute(
            text(
                """
                INSERT INTO pms_rules.calculation_segment (
                  calculation_id, segment_number, period_from, period_to,
                  day_count, proration_method, area_sqm, base_rent_per_day,
                  jurisdiction, applicability_key, tenant_registered, use_code,
                  agreement_version, lease_status, base_rent,
                  additional_charges, taxable_value, tax_amount, total_amount,
                  rent_rule_id, tax_rule_ids, rounding_method,
                  calculation_version, review_status
                ) VALUES (
                  :calculation_id, :segment_number, :period_from, :period_to,
                  :day_count, :proration_method, :area_sqm, :base_rent_per_day,
                  :jurisdiction, :applicability_key, :tenant_registered, :use_code,
                  :agreement_version, :lease_status, :base_rent,
                  :additional_charges, :taxable_value, :tax_amount, :total_amount,
                  :rent_rule_id, :tax_rule_ids, :rounding_method,
                  :calculation_version, :review_status
                )
                """
            ),
            {"calculation_id": calculation_id, **segment},
        )
        for index, component in enumerate(components, start=1):
            self._connection.execute(
                text(
                    """
                    INSERT INTO pms_rules.calculation_component (
                      calculation_id, segment_number, component_number,
                      component_code, rule_family, rule_id, calculation_basis,
                      proration_method, rate_value, taxable_value, calculated_amount,
                      tax_mechanism, payer, invoice_inclusion, payment_status,
                      source_document_id, source_clause, source_page
                    ) VALUES (
                      :calculation_id, :segment_number, :component_number,
                      :component_code, :rule_family, :rule_id, :calculation_basis,
                      :proration_method, :rate_value, :taxable_value, :calculated_amount,
                      :tax_mechanism, :payer, :invoice_inclusion, :payment_status,
                      :source_document_id, :source_clause, :source_page
                    )
                    """
                ),
                {
                    "calculation_id": calculation_id,
                    "segment_number": segment["segment_number"],
                    "component_number": index,
                    **component,
                },
            )
