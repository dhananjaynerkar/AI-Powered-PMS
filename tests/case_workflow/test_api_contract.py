"""FastAPI contract checks for the required Phase 04A routes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app, get_authorization_context
from pms_api.staff_directory import StaffRecipient
from pms_case_workflow.service import CaseWorkflowService
from pms_common.security import AuthorizationContext, Classification, UserRole

from tests.case_workflow.support import MemoryBackend, MemoryCaseStore


def _context(subject: str, role: UserRole) -> AuthorizationContext:
    return AuthorizationContext(
        subject=subject,
        roles=frozenset({role}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def test_required_case_api_flow_and_guessed_id_denial() -> None:
    backend = MemoryBackend()
    identities = {
        "do-token": _context("do-1", UserRole.DATA_ENTRY_OPERATOR),
        "no-token": _context("no-1", UserRole.NODAL_REGIONAL_OFFICER),
        "stranger-token": _context("no-2", UserRole.NODAL_REGIONAL_OFFICER),
    }

    @contextmanager
    def provider(context: AuthorizationContext) -> Iterator[CaseWorkflowService]:
        yield CaseWorkflowService(MemoryCaseStore(backend, context), context)

    def fake_context(request: Request) -> AuthorizationContext:
        token = request.headers["authorization"].removeprefix("Bearer ")
        return identities[token]

    class Directory:
        def recipients(
            self,
            context: AuthorizationContext,
            *,
            role: UserRole,
        ) -> tuple[StaffRecipient, ...]:
            del context
            if role is UserRole.NODAL_REGIONAL_OFFICER:
                return (StaffRecipient("no-1", "NO One", "no-1", None, role),)
            return ()

        def require_recipient(
            self,
            context: AuthorizationContext,
            *,
            role: UserRole,
            subject: str,
        ) -> StaffRecipient:
            if subject != "no-1":
                raise ValueError("unknown test recipient")
            return self.recipients(context, role=role)[0]

    app = create_app(provider, staff_directory=Directory())
    app.dependency_overrides[get_authorization_context] = fake_context

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            created = await client.post(
                "/api/v1/cases",
                headers={"Authorization": "Bearer do-token"},
                json={
                    "title": "Renewal",
                    "objective": "Review renewal",
                    "initial_message": "Initial facts",
                    "unit_id": "land",
                },
            )
            assert created.status_code == 201
            case_id = created.json()["case_id"]
            thread_id = created.json()["thread_id"]

            submitted = await client.post(
                f"/api/v1/cases/{case_id}/submit-to-no",
                headers={"Authorization": "Bearer do-token"},
                json={"assigned_subject": "no-1", "remarks": "Verify"},
            )
            timeline = await client.get(
                f"/api/v1/cases/{case_id}/timeline",
                headers={"Authorization": "Bearer no-token"},
            )
            guessed = await client.get(
                f"/api/v1/cases/{case_id}",
                headers={"Authorization": "Bearer stranger-token"},
            )

            assert submitted.status_code == 200
            assert submitted.json()["thread_id"] == thread_id
            assert timeline.status_code == 200
            assert timeline.json()["messages"][0]["sequence_number"] == 1
            assert guessed.status_code == 404

    asyncio.run(scenario())


def test_openapi_contains_every_required_phase04a_route() -> None:
    paths = create_app().openapi()["paths"]
    required = {
        "/api/v1/cases",
        "/api/v1/cases/{case_id}",
        "/api/v1/cases/{case_id}/timeline",
        "/api/v1/cases/{case_id}/messages",
        "/api/v1/cases/{case_id}/submit-to-no",
        "/api/v1/cases/{case_id}/return-to-do",
        "/api/v1/cases/{case_id}/verify",
        "/api/v1/cases/{case_id}/submit-to-hod",
        "/api/v1/cases/{case_id}/return-to-no",
        "/api/v1/cases/{case_id}/approve",
        "/api/v1/cases/{case_id}/reject",
        "/api/v1/cases/{case_id}/escalate",
    }

    assert required.issubset(paths)
