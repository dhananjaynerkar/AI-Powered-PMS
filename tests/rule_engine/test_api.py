from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import cast

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app, get_authorization_context
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_rule_engine.models import (
    CalculationMode,
    CalculationResult,
    CalculationStatus,
    LeaseCalculationRequest,
)
from pms_rule_engine.service import RuleCalculationService


class _RuleService:
    def calculate(self, request: LeaseCalculationRequest) -> CalculationResult:
        assert request.canonical_lease_id == "lease-1"
        return CalculationResult(
            calculation_id="technical-result",
            request_hash="a" * 64,
            status=CalculationStatus.REVIEW_REQUIRED,
            mode=CalculationMode.CURRENT_APPROVED_INTERPRETATION,
            rent_total=Decimal("0"),
            additional_total=Decimal("0"),
            tax_total=Decimal("0"),
            grand_total=Decimal("0"),
            warnings=("no approved rule",),
            review_required=True,
            calculation_version="1.0",
            input_snapshot_id="technical-input",
        )


def test_lease_calculation_api_is_typed_and_fail_closed() -> None:
    context = AuthorizationContext(
        subject="finance",
        roles=frozenset({UserRole.FINANCE_OFFICER}),
        tenant_id=None,
        department_id="finance",
        classification=Classification.RESTRICTED,
    )

    @contextmanager
    def provider(
        trusted_context: AuthorizationContext,
    ) -> Iterator[RuleCalculationService]:
        assert trusted_context == context
        yield cast(RuleCalculationService, _RuleService())

    def fake_context(request: Request) -> AuthorizationContext:
        assert request.headers["authorization"] == "Bearer token"
        return context

    app = create_app(rule_service_provider=provider)
    app.dependency_overrides[get_authorization_context] = fake_context

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/calculations/lease",
                headers={"Authorization": "Bearer token"},
                json={
                    "canonical_lease_id": "lease-1",
                    "canonical_tenant_id": "tenant-1",
                    "period_from": "2024-01-01",
                    "period_to": "2024-01-02",
                    "area_sqm": "1",
                    "base_rent_per_day": "0",
                    "jurisdiction": "TEST",
                    "applicability_key": "TEST",
                    "tenant_registered": True,
                    "use_code": "TEST",
                    "agreement_version": "TEST",
                    "lease_status": "TEST",
                },
            )
        assert response.status_code == 200
        assert response.json()["review_required"] is True
        assert response.json()["warnings"] == ["no approved rule"]

    asyncio.run(scenario())


def test_openapi_exposes_versioned_lease_calculation_contract() -> None:
    schema = create_app().openapi()

    assert "/api/v1/calculations/lease" in schema["paths"]
    assert (
        "/api/v1/calculations/lease/{calculation_id}/replay"
        in schema["paths"]
    )
