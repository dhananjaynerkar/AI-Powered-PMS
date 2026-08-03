from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app, get_authorization_context
from pms_common.security import AuthorizationContext, AuthorizationDenied, Classification, UserRole
from pms_retrieval.models import ChunkCitation, GroundedAnswer, SourceCitation
from pms_structured.models import QueryRoute, StructuredAnswer, StructuredQuery


class StubRagService:
    def ask(self, question: str, **_: object) -> GroundedAnswer:
        assert question == "What is the approved policy?"
        return GroundedAnswer(
            answer="The approved policy is available in the cited clause.",
            sources=(
                SourceCitation(
                    source_id="source-1",
                    document_id="doc-1",
                    document_version_id="version-1",
                    document_title="Approved policy",
                    page_numbers=(4,),
                    section_number="2",
                    clause_number="2.1",
                    citations=(
                        ChunkCitation(
                            block_id="block-1",
                            page_number=4,
                            bounding_box=None,
                        ),
                    ),
                ),
            ),
            confidence="HIGH",
            review_required=False,
        )


class StubStructuredService:
    def __init__(self, denied: bool = False) -> None:
        self.denied = denied

    def ask(self, query: StructuredQuery) -> StructuredAnswer:
        if self.denied:
            raise AuthorizationDenied("scope denied")
        assert query.question == "Show my bills"
        return StructuredAnswer(
            answer="One authorized record was found.",
            route=QueryRoute.STRUCTURED,
            template_id="bills",
            confidence="HIGH",
            review_required=False,
            correlation_id="phase13-test",
        )


class StubAuditService:
    def __init__(self, denied: bool = False) -> None:
        self.denied = denied
        self.denials: list[tuple[str, str]] = []

    def list_my_queries(self, limit: int) -> tuple[object, ...]:
        del limit
        if self.denied:
            raise AuthorizationDenied("audit scope denied")
        return ()

    def record_denied(self, query_category: str, reason_code: str) -> None:
        self.denials.append((query_category, reason_code))


def _context(role: UserRole) -> AuthorizationContext:
    return AuthorizationContext(
        subject="phase13-user",
        roles=frozenset({role}),
        tenant_id="tenant-1" if role == UserRole.TENANT else None,
        department_id=None,
        classification=Classification.INTERNAL,
    )


def test_demonstrable_workflow_authorized_policy_structured_and_audit() -> None:
    context = _context(UserRole.AUDITOR)
    audit = StubAuditService()

    @contextmanager
    def rag_provider(_: AuthorizationContext) -> Iterator[StubRagService]:
        yield StubRagService()

    @contextmanager
    def structured_provider(_: AuthorizationContext) -> Iterator[StubStructuredService]:
        yield StubStructuredService()

    @contextmanager
    def audit_provider(_: AuthorizationContext) -> Iterator[StubAuditService]:
        yield audit

    def fake_context(_: Request) -> AuthorizationContext:
        return context

    app = create_app(
        rag_service_provider=rag_provider,
        structured_service_provider=structured_provider,
        audit_service_provider=audit_provider,
    )
    app.dependency_overrides[get_authorization_context] = fake_context

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health/live")).json()["status"] == "ok"
            me = await client.get(
                "/api/v1/me",
                headers={"Authorization": "Bearer token"},
            )
            policy = await client.post(
                "/api/v1/policy/query",
                headers={"Authorization": "Bearer token"},
                json={"question": "What is the approved policy?"},
            )
            structured = await client.post(
                "/api/v1/query",
                headers={"Authorization": "Bearer token"},
                json={"question": "Show my bills"},
            )
            audit_response = await client.get(
                "/api/v1/audit/my-queries",
                headers={"Authorization": "Bearer token"},
            )

        assert policy.status_code == 200
        assert me.status_code == 200
        assert policy.json()["sources"][0]["page_numbers"] == [4]
        assert structured.status_code == 200
        assert audit_response.status_code == 200

    asyncio.run(scenario())


def test_unauthorized_structured_query_is_denied_and_recorded() -> None:
    context = _context(UserRole.TENANT)
    audit = StubAuditService(denied=True)

    @contextmanager
    def structured_provider(_: AuthorizationContext) -> Iterator[StubStructuredService]:
        yield StubStructuredService(denied=True)

    @contextmanager
    def audit_provider(_: AuthorizationContext) -> Iterator[StubAuditService]:
        yield audit

    def fake_context(_: Request) -> AuthorizationContext:
        return context

    app = create_app(
        structured_service_provider=structured_provider,
        audit_service_provider=audit_provider,
    )
    app.dependency_overrides[get_authorization_context] = fake_context

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/query",
                headers={"Authorization": "Bearer token"},
                json={"question": "Show my bills"},
            )
            audit_response = await client.get(
                "/api/v1/audit/my-queries",
                headers={"Authorization": "Bearer token"},
            )
        assert response.status_code == 403
        assert audit_response.status_code == 403

    asyncio.run(scenario())
    assert audit.denials == [("STRUCTURED_QUERY", "AUTHORIZATION_DENIED")]
