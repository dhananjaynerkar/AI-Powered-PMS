"""External-state fakes for Phase 05 service and API tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

from pms_common.security import (
    CLASSIFICATION_RANK,
    AuditEvent,
    AuthorizationContext,
    Classification,
    UserRole,
)
from pms_ingestion.models import (
    DocumentMetadata,
    ObjectKind,
    ObjectWrite,
    StoredObject,
)


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str | None], bytes] = {}
        self.put_calls = 0
        self.get_calls = 0

    def ensure_buckets(self, bucket_names: Iterable[str]) -> tuple[str, ...]:
        return tuple(bucket_names)

    def put_immutable(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content: bytes,
        mime_type: str,
        checksum_sha256: str,
    ) -> ObjectWrite:
        del mime_type, checksum_sha256
        self.put_calls += 1
        version = f"version-{self.put_calls}"
        self.objects[(bucket_name, object_key, version)] = content
        return ObjectWrite(bucket_name, object_key, version, f"etag-{self.put_calls}")

    def get(
        self,
        *,
        bucket_name: str,
        object_key: str,
        object_version: str | None,
    ) -> bytes:
        self.get_calls += 1
        return self.objects[(bucket_name, object_key, object_version)]


class MemoryRegistryBackend:
    def __init__(self) -> None:
        self.documents: dict[str, DocumentMetadata] = {}
        self.versions: dict[str, list[DocumentMetadata]] = {}
        self.objects: dict[str, StoredObject] = {}
        self.version_objects: dict[str, str] = {}
        self.derived: list[tuple[str, str, str, str, str, ObjectKind]] = []
        self.acl: dict[
            str,
            tuple[str | None, Classification, frozenset[UserRole], frozenset[str]],
        ] = {}
        self.audits: list[AuditEvent] = []


class MemoryDocumentRegistry:
    def __init__(
        self,
        backend: MemoryRegistryBackend,
        context: AuthorizationContext,
    ) -> None:
        self.backend = backend
        self.context = context

    def _visible(self, document_id: str) -> bool:
        acl = self.backend.acl.get(document_id)
        if acl is None:
            return False
        tenant_id, classification, roles, departments = acl
        if UserRole.ADMINISTRATOR in self.context.roles or UserRole.AUDITOR in self.context.roles:
            return True
        if (
            tenant_id is not None
            and UserRole.TENANT in self.context.roles
            and tenant_id != self.context.tenant_id
        ):
            return False
        if CLASSIFICATION_RANK[self.context.classification] < CLASSIFICATION_RANK[
            classification
        ]:
            return False
        if roles and not roles.intersection(self.context.roles):
            return False
        return not departments or self.context.department_id in departments

    def find_duplicate(
        self,
        checksum_sha256: str,
        document_id: str | None,
    ) -> DocumentMetadata | None:
        candidates = (
            [document_id] if document_id is not None else list(self.backend.documents)
        )
        for candidate in candidates:
            if candidate is None or not self._visible(candidate):
                continue
            for version in self.backend.versions[candidate]:
                if version.checksum_sha256 == checksum_sha256:
                    return version
        return None

    def get_document(self, document_id: str) -> DocumentMetadata | None:
        if not self._visible(document_id):
            return None
        return self.backend.documents.get(document_id)

    def next_version(self, document_id: str) -> int:
        return len(self.backend.versions[document_id]) + 1

    def save_original(
        self,
        *,
        document_id: str,
        title: str,
        filename: str,
        version_id: str,
        version_number: int,
        stored_object: StoredObject,
        classification: Classification,
        tenant_id: str | None,
        allowed_roles: Iterable[UserRole],
        allowed_departments: Iterable[str],
        is_new_document: bool,
    ) -> None:
        if is_new_document:
            self.backend.acl[document_id] = (
                tenant_id,
                classification,
                frozenset(allowed_roles),
                frozenset(allowed_departments),
            )
            self.backend.versions[document_id] = []
        metadata = DocumentMetadata(
            canonical_document_id=document_id,
            version_id=version_id,
            version_number=version_number,
            title=title,
            original_filename=filename,
            status="uploaded",
            checksum_sha256=stored_object.checksum_sha256,
            size_bytes=stored_object.size_bytes,
            mime_type=stored_object.mime_type,
            classification=classification,
            created_by_subject=stored_object.created_by_subject,
            created_at=stored_object.created_at,
        )
        self.backend.documents[document_id] = metadata
        self.backend.versions[document_id].append(metadata)
        self.backend.objects[stored_object.object_id] = stored_object
        self.backend.version_objects[version_id] = stored_object.object_id

    def get_original_object(self, version_id: str) -> StoredObject | None:
        object_id = self.backend.version_objects.get(version_id)
        return self.backend.objects.get(object_id) if object_id is not None else None

    def update_status(self, document_id: str, status: str, updated_at: datetime) -> None:
        del updated_at
        current = self.backend.documents[document_id]
        updated = replace(current, status=status)
        self.backend.documents[document_id] = updated
        self.backend.versions[document_id] = [
            updated if item.version_id == current.version_id else item
            for item in self.backend.versions[document_id]
        ]

    def get_derived(
        self,
        version_id: str,
        object_kind: ObjectKind,
        producer: str,
        producer_version: str,
    ) -> tuple[StoredObject, str, str] | None:
        for (
            _artifact_id,
            stored_version_id,
            stored_producer,
            stored_producer_version,
            object_id,
            stored_kind,
        ) in reversed(self.backend.derived):
            if (
                stored_version_id == version_id
                and stored_kind is object_kind
                and stored_producer == producer
                and stored_producer_version == producer_version
            ):
                return (
                    self.backend.objects[object_id],
                    stored_producer,
                    stored_producer_version,
                )
        return None

    def save_derived(
        self,
        *,
        artifact_id: str,
        version_id: str,
        stored_object: StoredObject,
        producer: str,
        producer_version: str,
    ) -> None:
        self.backend.objects[stored_object.object_id] = stored_object
        self.backend.derived.append(
            (
                artifact_id,
                version_id,
                producer,
                producer_version,
                stored_object.object_id,
                stored_object.object_kind,
            )
        )

    def record_audit(self, event: AuditEvent) -> None:
        self.backend.audits.append(event)


def context(
    subject: str,
    role: UserRole,
    *,
    department: str = "estate",
    classification: Classification = Classification.RESTRICTED,
) -> AuthorizationContext:
    return AuthorizationContext(
        subject=subject,
        roles=frozenset({role}),
        tenant_id=None,
        department_id=department,
        unit_id="land",
        classification=classification,
    )
