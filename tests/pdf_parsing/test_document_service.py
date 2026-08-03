from __future__ import annotations

import pytest
from pms_common.security import Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.models import DocumentStatus, ObjectKind
from pms_ingestion.scanner import DisabledMalwareScanner
from pms_ingestion.service import DocumentService, InvalidDocumentStatus
from pms_ingestion.validation import UploadValidator

from tests.document_registry.support import (
    MemoryDocumentRegistry,
    MemoryObjectStore,
    MemoryRegistryBackend,
    context,
)


def _service() -> tuple[DocumentService, MemoryRegistryBackend]:
    settings = Settings(_env_file=None, upload_max_mb=1)
    backend = MemoryRegistryBackend()
    authorization = context("do-1", UserRole.DATA_ENTRY_OPERATOR)
    return (
        DocumentService(
            MemoryDocumentRegistry(backend, authorization),
            MemoryObjectStore(),
            authorization,
            settings,
            UploadValidator(settings),
            DisabledMalwareScanner(),
        ),
        backend,
    )


def test_status_machine_and_derived_artifact_round_trip() -> None:
    service, _backend = _service()
    created = service.upload(
        title="Controlled parser source",
        filename="controlled.pdf",
        mime_type="application/pdf",
        content=b"%PDF-1.7\ncontrolled\n%%EOF",
        classification=Classification.INTERNAL,
    )

    parsing = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.PARSING,
    )
    parsed = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.PARSED,
    )
    stored = service.store_derived(
        document_id=created.document.canonical_document_id,
        content=b'{"schema_version":"1.0"}',
        mime_type="application/json",
        object_kind=ObjectKind.CANONICAL_JSON,
        producer="pms-parser-pipeline",
        producer_version="1.0",
    )
    canonicalized = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.CANONICALIZED,
    )
    review_required = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.REVIEW_REQUIRED,
    )
    reparsing = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.PARSING,
    )
    reparsed = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.PARSED,
    )
    recanonicalized = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.CANONICALIZED,
    )
    chunk_ready = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.CHUNK_READY,
    )
    indexed = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.INDEXED,
    )
    deactivated = service.transition_status(
        created.document.canonical_document_id,
        DocumentStatus.DEACTIVATED,
    )
    retrieved = service.retrieve_derived(
        document_id=created.document.canonical_document_id,
        object_kind=ObjectKind.CANONICAL_JSON,
        producer="pms-parser-pipeline",
        producer_version="1.0",
    )

    assert parsing.status == "parsing"
    assert parsed.status == "parsed"
    assert canonicalized.status == "canonicalized"
    assert review_required.status == "review_required"
    assert reparsing.status == "parsing"
    assert reparsed.status == "parsed"
    assert recanonicalized.status == "canonicalized"
    assert chunk_ready.status == "chunk_ready"
    assert indexed.status == "indexed"
    assert deactivated.status == "deactivated"
    assert retrieved is not None
    assert retrieved.content == b'{"schema_version":"1.0"}'
    assert retrieved.artifact.object_id == stored.object_id


def test_invalid_status_transition_is_rejected() -> None:
    service, _backend = _service()
    created = service.upload(
        title="Controlled parser source",
        filename="controlled.pdf",
        mime_type="application/pdf",
        content=b"%PDF-1.7\ncontrolled\n%%EOF",
        classification=Classification.INTERNAL,
    )

    with pytest.raises(InvalidDocumentStatus):
        service.transition_status(
            created.document.canonical_document_id,
            DocumentStatus.CANONICALIZED,
        )
