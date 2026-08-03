"""Authenticated Phase 05 upload and retrieval API contract."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app, get_authorization_context
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.scanner import DisabledMalwareScanner
from pms_ingestion.service import DocumentService
from pms_ingestion.validation import UploadValidator

from tests.document_registry.support import (
    MemoryDocumentRegistry,
    MemoryObjectStore,
    MemoryRegistryBackend,
    context,
)


def test_document_api_upload_duplicate_retrieve_and_denial() -> None:
    backend = MemoryRegistryBackend()
    objects = MemoryObjectStore()
    identities = {
        "do-token": context("do-1", UserRole.DATA_ENTRY_OPERATOR),
        "stranger-token": context(
            "legal-1",
            UserRole.LEGAL_OFFICER,
            department="legal",
        ),
    }
    settings = Settings(upload_max_mb=1)

    @contextmanager
    def provider(authorization: AuthorizationContext) -> Iterator[DocumentService]:
        yield DocumentService(
            MemoryDocumentRegistry(backend, authorization),
            objects,
            authorization,
            settings,
            UploadValidator(settings),
            DisabledMalwareScanner(),
        )

    def fake_context(request: Request) -> AuthorizationContext:
        token = request.headers["authorization"].removeprefix("Bearer ")
        return identities[token]

    app = create_app(
        document_service_provider=provider,
        upload_max_bytes=1024 * 1024,
    )
    app.dependency_overrides[get_authorization_context] = fake_context

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            files = {"file": ("lease.pdf", b"%PDF-1.7\napi\n%%EOF", "application/pdf")}
            form = {"title": "Lease", "classification": Classification.INTERNAL.value}
            created = await client.post(
                "/api/v1/documents",
                headers={"Authorization": "Bearer do-token"},
                files=files,
                data=form,
            )
            duplicate = await client.post(
                "/api/v1/documents",
                headers={"Authorization": "Bearer do-token"},
                files=files,
                data=form,
            )
            document_id = created.json()["document"]["canonical_document_id"]
            content = await client.get(
                f"/api/v1/documents/{document_id}/content",
                headers={"Authorization": "Bearer do-token"},
            )
            denied = await client.get(
                f"/api/v1/documents/{document_id}/content",
                headers={"Authorization": "Bearer stranger-token"},
            )

            assert created.status_code == 201
            assert "object_key" not in created.text
            assert duplicate.status_code == 201
            assert duplicate.json()["duplicate"] is True
            assert content.status_code == 200
            assert content.content == b"%PDF-1.7\napi\n%%EOF"
            assert len(content.headers["x-content-sha256"]) == 64
            assert denied.status_code == 404

    asyncio.run(scenario())


def test_openapi_contains_phase05_document_routes() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/documents" in paths
    assert "/api/v1/documents/{document_id}" in paths
    assert "/api/v1/documents/{document_id}/content" in paths
