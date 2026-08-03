"""Document deduplication, versioning, retrieval and ACL tests."""

from __future__ import annotations

import pytest
from pms_common.security import Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.models import ObjectKind
from pms_ingestion.scanner import DisabledMalwareScanner
from pms_ingestion.service import DocumentIntegrityError, DocumentNotFound, DocumentService
from pms_ingestion.validation import UploadValidator

from tests.document_registry.support import (
    MemoryDocumentRegistry,
    MemoryObjectStore,
    MemoryRegistryBackend,
    context,
)


def _service(
    backend: MemoryRegistryBackend,
    objects: MemoryObjectStore,
    subject: str,
    role: UserRole,
    *,
    department: str = "estate",
) -> DocumentService:
    settings = Settings(upload_max_mb=1)
    authorization = context(subject, role, department=department)
    return DocumentService(
        MemoryDocumentRegistry(backend, authorization),
        objects,
        authorization,
        settings,
        UploadValidator(settings),
        DisabledMalwareScanner(),
    )


def test_upload_duplicate_retrieval_and_new_version() -> None:
    backend = MemoryRegistryBackend()
    objects = MemoryObjectStore()
    uploader = _service(backend, objects, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    original = b"%PDF-1.7\noriginal\n%%EOF"

    first = uploader.upload(
        title="Lease agreement",
        filename="lease.pdf",
        mime_type="application/pdf",
        content=original,
        classification=Classification.INTERNAL,
    )
    duplicate = uploader.upload(
        title="Lease agreement",
        filename="lease.pdf",
        mime_type="application/pdf",
        content=original,
        classification=Classification.INTERNAL,
    )
    second = uploader.upload(
        title="Ignored replacement title",
        filename="lease-v2.pdf",
        mime_type="application/pdf",
        content=b"%PDF-1.7\nversion two\n%%EOF",
        classification=Classification.INTERNAL,
        document_id=first.document.canonical_document_id,
    )
    retrieved = uploader.retrieve(first.document.canonical_document_id)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.document.version_id == first.document.version_id
    assert second.document.version_number == 2
    assert second.document.title == "Lease agreement"
    assert retrieved.content == b"%PDF-1.7\nversion two\n%%EOF"
    assert objects.put_calls == 2
    assert len(backend.versions[first.document.canonical_document_id]) == 2
    assert {event.query_category for event in backend.audits} >= {
        "DOCUMENT_UPLOAD",
        "DOCUMENT_UPLOAD_DUPLICATE",
        "DOCUMENT_CONTENT_READ",
    }


def test_unauthorized_document_is_hidden_before_object_access() -> None:
    backend = MemoryRegistryBackend()
    objects = MemoryObjectStore()
    uploader = _service(backend, objects, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    created = uploader.upload(
        title="Estate-only document",
        filename="estate.pdf",
        mime_type="application/pdf",
        content=b"%PDF-1.7\nestate\n%%EOF",
        classification=Classification.INTERNAL,
    )
    stranger = _service(
        backend,
        objects,
        "legal-1",
        UserRole.LEGAL_OFFICER,
        department="legal",
    )

    with pytest.raises(DocumentNotFound):
        stranger.retrieve(created.document.canonical_document_id)

    assert objects.get_calls == 0


def test_checksum_mismatch_is_rejected() -> None:
    backend = MemoryRegistryBackend()
    objects = MemoryObjectStore()
    uploader = _service(backend, objects, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    created = uploader.upload(
        title="Integrity document",
        filename="integrity.pdf",
        mime_type="application/pdf",
        content=b"%PDF-1.7\noriginal\n%%EOF",
        classification=Classification.INTERNAL,
    )
    stored_object = next(iter(backend.objects.values()))
    key = (
        stored_object.bucket_name,
        stored_object.object_key,
        stored_object.object_version,
    )
    objects.objects[key] = b"%PDF-1.7\ntampered\n%%EOF"

    with pytest.raises(DocumentIntegrityError):
        uploader.retrieve(created.document.canonical_document_id)


def test_derived_artifact_has_separate_immutable_lineage() -> None:
    backend = MemoryRegistryBackend()
    objects = MemoryObjectStore()
    uploader = _service(backend, objects, "do-1", UserRole.DATA_ENTRY_OPERATOR)
    created = uploader.upload(
        title="Parser source",
        filename="source.pdf",
        mime_type="application/pdf",
        content=b"%PDF-1.7\nsource\n%%EOF",
        classification=Classification.INTERNAL,
    )

    derived = uploader.store_derived(
        document_id=created.document.canonical_document_id,
        content=b'{"parser": "future-adapter", "pages": []}',
        mime_type="application/json",
        object_kind=ObjectKind.RAW_PARSER,
        producer="future-adapter",
        producer_version="0-test",
    )

    assert derived.bucket_name == "pms-derived-artifacts"
    assert derived.object_kind is ObjectKind.RAW_PARSER
    assert backend.derived[0][1] == created.document.version_id
    assert derived.object_key.startswith("raw_parser/")
    assert objects.put_calls == 2
