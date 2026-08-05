"""Authorized Phase 05 document storage orchestration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from pms_common.security import (
    CLASSIFICATION_RANK,
    AuthorizationContext,
    AuthorizationDenied,
    AuthorizationService,
    Classification,
    Permission,
    create_audit_event,
)
from pms_common.settings import Settings

from pms_ingestion.models import (
    DocumentMetadata,
    DocumentStatus,
    DocumentUploadResult,
    ObjectKind,
    RetrievedArtifact,
    RetrievedDocument,
    StoredObject,
)
from pms_ingestion.repository import DocumentRegistry
from pms_ingestion.scanner import MalwareScanner
from pms_ingestion.storage import ObjectStore
from pms_ingestion.validation import UploadValidator


class DocumentServiceError(RuntimeError):
    """Base error exposed through bounded API responses."""


class DocumentNotFound(DocumentServiceError):
    """Document is absent or hidden by authorization."""


class DocumentIntegrityError(DocumentServiceError):
    """Stored bytes no longer match authoritative registry metadata."""


class InvalidDocumentVersion(DocumentServiceError):
    """Requested version lineage is not valid for the authorized document."""


class InvalidDocumentStatus(DocumentServiceError):
    """Requested document lifecycle transition is not permitted."""


_SUFFIX_BY_MIME = {
    "application/json": ".json",
    "text/plain": ".txt",
    "application/pdf": ".pdf",
}

_STATUS_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.UPLOADED: frozenset({DocumentStatus.PARSING}),
    DocumentStatus.REJECTED: frozenset({DocumentStatus.PARSING}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.PARSING}),
    DocumentStatus.QUALITY_FAILED: frozenset({DocumentStatus.PARSING}),
    DocumentStatus.REVIEW_REQUIRED: frozenset({DocumentStatus.PARSING}),
    DocumentStatus.CANONICALIZED: frozenset(
        {
            DocumentStatus.PARSING,
            DocumentStatus.REVIEW_REQUIRED,
            DocumentStatus.CHUNK_READY,
            DocumentStatus.DEACTIVATED,
        }
    ),
    DocumentStatus.CHUNK_READY: frozenset(
        {
            DocumentStatus.PARSING,
            DocumentStatus.REVIEW_REQUIRED,
            DocumentStatus.INDEXED,
            DocumentStatus.DEACTIVATED,
        }
    ),
    DocumentStatus.INDEXED: frozenset(
        {
            DocumentStatus.PARSING,
            DocumentStatus.CHUNK_READY,
            DocumentStatus.DEACTIVATED,
        }
    ),
    DocumentStatus.PROVISIONALLY_INDEXED: frozenset(
        {
            DocumentStatus.PARSING,
            DocumentStatus.CHUNK_READY,
            DocumentStatus.DEACTIVATED,
        }
    ),
    DocumentStatus.DEACTIVATED: frozenset(
        {
            DocumentStatus.PARSING,
        }
    ),
    DocumentStatus.PARSING: frozenset(
        {
            DocumentStatus.PARSED,
            DocumentStatus.QUALITY_FAILED,
            DocumentStatus.REVIEW_REQUIRED,
            DocumentStatus.FAILED,
        }
    ),
    DocumentStatus.PARSED: frozenset(
        {DocumentStatus.CANONICALIZED, DocumentStatus.REVIEW_REQUIRED}
    ),
}


class DocumentService:
    """Keep authorization, validation, storage and registry responsibilities ordered."""

    def __init__(
        self,
        registry: DocumentRegistry,
        object_store: ObjectStore,
        context: AuthorizationContext,
        settings: Settings,
        validator: UploadValidator,
        malware_scanner: MalwareScanner,
    ) -> None:
        self._registry = registry
        self._object_store = object_store
        self._context = context
        self._settings = settings
        self._validator = validator
        self._malware_scanner = malware_scanner
        self._authorization = AuthorizationService()

    def upload(
        self,
        *,
        title: str,
        filename: str,
        mime_type: str,
        content: bytes,
        classification: Classification,
        document_id: str | None = None,
    ) -> DocumentUploadResult:
        self._authorization.require_permission(self._context, Permission.DATA_WRITE)
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("document title is required")
        if (
            CLASSIFICATION_RANK[classification]
            > CLASSIFICATION_RANK[self._context.classification]
        ):
            raise AuthorizationDenied("classification exceeds uploader clearance")
        validated = self._validator.validate(
            filename=filename,
            mime_type=mime_type,
            content=content,
        )
        self._malware_scanner.scan(content)

        duplicate = self._registry.find_duplicate(
            validated.checksum_sha256,
            document_id,
        )
        if duplicate is not None:
            self._audit("DOCUMENT_UPLOAD_DUPLICATE", duplicate, "ALLOWED")
            return DocumentUploadResult(document=duplicate, duplicate=True)

        is_new_document = document_id is None
        canonical_document_id = document_id or str(uuid4())
        if not is_new_document:
            current = self._registry.get_document(canonical_document_id)
            if current is None:
                raise DocumentNotFound("document not found")
            version_number = self._registry.next_version(canonical_document_id)
            clean_title = current.title
        else:
            version_number = 1

        version_id = str(uuid4())
        object_id = str(uuid4())
        object_key = (
            f"original/{canonical_document_id}/v{version_number}/"
            f"{validated.checksum_sha256}{validated.extension}"
        )
        write = self._object_store.put_immutable(
            bucket_name=self._settings.minio_bucket_raw,
            object_key=object_key,
            content=content,
            mime_type=validated.mime_type,
            checksum_sha256=validated.checksum_sha256,
        )
        now = datetime.now(UTC)
        stored_object = StoredObject(
            object_id=object_id,
            bucket_name=write.bucket_name,
            object_key=write.object_key,
            object_version=write.object_version,
            checksum_sha256=validated.checksum_sha256,
            size_bytes=validated.size_bytes,
            mime_type=validated.mime_type,
            object_kind=ObjectKind.ORIGINAL,
            retention_mode="versioned",
            retention_until=None,
            created_by_subject=self._context.subject,
            created_at=now,
        )
        departments = (
            (self._context.department_id,) if self._context.department_id is not None else ()
        )
        self._registry.save_original(
            document_id=canonical_document_id,
            title=clean_title,
            filename=validated.filename,
            version_id=version_id,
            version_number=version_number,
            stored_object=stored_object,
            classification=classification,
            tenant_id=self._context.tenant_id,
            allowed_roles=(),
            allowed_departments=departments,
            is_new_document=is_new_document,
        )
        document = self._registry.get_document(canonical_document_id)
        if document is None:
            raise DocumentServiceError("document registry write was not visible")
        self._audit("DOCUMENT_UPLOAD", document, "ALLOWED")
        return DocumentUploadResult(document=document, duplicate=False)

    def metadata(self, document_id: str) -> DocumentMetadata:
        self._authorization.require_permission(
            self._context,
            Permission.DOCUMENT_SEARCH,
        )
        document = self._registry.get_document(document_id)
        if document is None:
            raise DocumentNotFound("document not found")
        self._audit("DOCUMENT_METADATA_READ", document, "ALLOWED")
        return document

    def retrieve(self, document_id: str) -> RetrievedDocument:
        document = self.metadata(document_id)
        stored_object = self._registry.get_original_object(document.version_id)
        if stored_object is None:
            raise DocumentIntegrityError("registered original object is missing")
        content = self._object_store.get(
            bucket_name=stored_object.bucket_name,
            object_key=stored_object.object_key,
            object_version=stored_object.object_version,
        )
        if hashlib.sha256(content).hexdigest() != document.checksum_sha256:
            raise DocumentIntegrityError("stored object checksum mismatch")
        self._audit("DOCUMENT_CONTENT_READ", document, "ALLOWED")
        return RetrievedDocument(document=document, content=content)

    def store_derived(
        self,
        *,
        document_id: str,
        content: bytes,
        mime_type: str,
        object_kind: ObjectKind,
        producer: str,
        producer_version: str,
    ) -> StoredObject:
        self._authorization.require_permission(self._context, Permission.DATA_WRITE)
        if object_kind is ObjectKind.ORIGINAL:
            raise InvalidDocumentVersion("derived object kind cannot be original")
        document = self._registry.get_document(document_id)
        if document is None:
            raise DocumentNotFound("document not found")
        if not content:
            raise ValueError("derived object cannot be empty")
        if len(content) > self._validator.max_bytes:
            raise ValueError("derived object exceeds the configured size limit")
        checksum = hashlib.sha256(content).hexdigest()
        suffix = _SUFFIX_BY_MIME.get(mime_type, ".bin")
        bucket = (
            self._settings.minio_bucket_canonical
            if object_kind is ObjectKind.CANONICAL_JSON
            else self._settings.minio_bucket_derived
        )
        object_key = (
            f"{object_kind.value}/{document_id}/v{document.version_number}/"
            f"{checksum}{suffix}"
        )
        write = self._object_store.put_immutable(
            bucket_name=bucket,
            object_key=object_key,
            content=content,
            mime_type=mime_type,
            checksum_sha256=checksum,
        )
        now = datetime.now(UTC)
        stored_object = StoredObject(
            object_id=str(uuid4()),
            bucket_name=write.bucket_name,
            object_key=write.object_key,
            object_version=write.object_version,
            checksum_sha256=checksum,
            size_bytes=len(content),
            mime_type=mime_type,
            object_kind=object_kind,
            retention_mode="versioned",
            retention_until=None,
            created_by_subject=self._context.subject,
            created_at=now,
        )
        self._registry.save_derived(
            artifact_id=str(uuid4()),
            version_id=document.version_id,
            stored_object=stored_object,
            producer=producer,
            producer_version=producer_version,
        )
        self._audit("DOCUMENT_DERIVED_OBJECT_WRITE", document, "ALLOWED")
        return stored_object

    def retrieve_derived(
        self,
        *,
        document_id: str,
        object_kind: ObjectKind,
        producer: str,
        producer_version: str,
    ) -> RetrievedArtifact | None:
        """Return one authorized immutable parser artifact with checksum validation."""

        self._authorization.require_permission(
            self._context,
            Permission.DOCUMENT_SEARCH,
        )
        document = self._registry.get_document(document_id)
        if document is None:
            raise DocumentNotFound("document not found")
        found = self._registry.get_derived(
            document.version_id,
            object_kind,
            producer,
            producer_version,
        )
        if found is None:
            return None
        stored_object, stored_producer, stored_version = found
        content = self._object_store.get(
            bucket_name=stored_object.bucket_name,
            object_key=stored_object.object_key,
            object_version=stored_object.object_version,
        )
        if hashlib.sha256(content).hexdigest() != stored_object.checksum_sha256:
            raise DocumentIntegrityError("derived object checksum mismatch")
        self._audit("DOCUMENT_DERIVED_OBJECT_READ", document, "ALLOWED")
        return RetrievedArtifact(
            document=document,
            artifact=stored_object,
            producer=stored_producer,
            producer_version=stored_version,
            content=content,
        )

    def transition_status(
        self,
        document_id: str,
        target: DocumentStatus,
    ) -> DocumentMetadata:
        """Apply an explicit Phase 06 state transition and audit it."""

        self._authorization.require_permission(self._context, Permission.DATA_WRITE)
        current = self._registry.get_document(document_id)
        if current is None:
            raise DocumentNotFound("document not found")
        current_status = DocumentStatus(current.status)
        if current_status is target:
            return current
        if target not in _STATUS_TRANSITIONS.get(current_status, frozenset()):
            raise InvalidDocumentStatus(
                f"document status cannot transition from {current_status} to {target}"
            )
        self._registry.update_status(document_id, target.value, datetime.now(UTC))
        updated = self._registry.get_document(document_id)
        if updated is None:
            raise DocumentServiceError("document status update was not visible")
        self._audit(f"DOCUMENT_STATUS_{target.value.upper()}", updated, "ALLOWED")
        return updated

    def _audit(
        self,
        category: str,
        document: DocumentMetadata,
        status: str,
    ) -> None:
        self._registry.record_audit(
            create_audit_event(
                self._context,
                query_category=category,
                entity_scope={"document_id": document.canonical_document_id},
                source_ids=(document.version_id,),
                result_status=status,
            )
        )
