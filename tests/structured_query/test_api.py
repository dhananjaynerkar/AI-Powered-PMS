from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app, get_authorization_context
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_structured.models import QueryRoute, StructuredAnswer, StructuredQuery
from pms_structured.service import StructuredQueryService


class StubStructuredService:
    def ask(self, query: StructuredQuery) -> StructuredAnswer:
        assert query.question == "Show bill"
        return StructuredAnswer(
            answer="Retrieved 0 authorized records from an approved query.",
            route=QueryRoute.STRUCTURED,
            template_id="bills",
            confidence="HIGH",
            warnings=("No authorized matching record was found.",),
            review_required=False,
            correlation_id="phase09-test",
        )


def test_query_api_contract_has_no_raw_sql_input() -> None:
    context = AuthorizationContext(
        subject="tenant-subject",
        roles=frozenset({UserRole.TENANT}),
        tenant_id="tenant-1",
        department_id=None,
        classification=Classification.INTERNAL,
    )

    @contextmanager
    def provider(
        trusted_context: AuthorizationContext,
    ) -> Iterator[StructuredQueryService]:
        assert trusted_context == context
        yield cast(StructuredQueryService, StubStructuredService())

    def fake_context(request: Request) -> AuthorizationContext:
        assert request.headers["authorization"] == "Bearer token"
        return context

    app = create_app(structured_service_provider=provider)
    app.dependency_overrides[get_authorization_context] = fake_context

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            accepted = await client.post(
                "/api/v1/query",
                headers={"Authorization": "Bearer token"},
                json={"question": "Show bill"},
            )
            rejected = await client.post(
                "/api/v1/query",
                headers={"Authorization": "Bearer token"},
                json={"question": "Show bill", "sql": "SELECT 1"},
            )

        assert accepted.status_code == 200
        assert accepted.json()["template_id"] == "bills"
        assert rejected.status_code == 422

    asyncio.run(scenario())


def test_openapi_exposes_query_without_a_sql_property() -> None:
    schema = create_app().openapi()

    assert "/api/v1/query" in schema["paths"]
    query_schema = schema["components"]["schemas"]["StructuredQuery"]
    assert "question" in query_schema["properties"]
    assert "sql" not in query_schema["properties"]
