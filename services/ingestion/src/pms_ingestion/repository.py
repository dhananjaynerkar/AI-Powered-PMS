"""PostgreSQL persistence for the authoritative document registry."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, cast

from pms_common.security import (
    AuditEvent,
    AuthorizationContext,
    Classification,
    UserRole,
    apply_postgres_session_context,
    write_audit_event,
)
from sqlalchemy import Connection, text

from pms_ingestion.models import DocumentMetadata, ObjectKind, StoredObject


class DocumentRegistry(Protocol):
    """Atomic registry operations required by the storage service."""

    def find_duplicate(
        self,
        checksum_sha256: str,
        document_id: str | None,
    ) -> DocumentMetadata | None: ...

    def get_document(self, document_id: str) -> DocumentMetadata | None: ...

    def next_version(self, document_id: str) -> int: ...

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
    ) -> None: ...

    def get_original_object(self, version_id: str) -> StoredObject | None: ...

    def update_status(self, document_id: str, status: str, updated_at: datetime) -> None: ...

    def get_derived(
        self,
        version_id: str,
        object_kind: ObjectKind,
        producer: str,
        producer_version: str,
    ) -> tuple[StoredObject, str, str] | None: ...

    def save_derived(
        self,
        *,
        artifact_id: str,
        version_id: str,
        stored_object: StoredObject,
        producer: str,
        producer_version: str,
    ) -> None: ...

    def record_audit(self, event: AuditEvent) -> None: ...


def _metadata(row: dict[str, object]) -> DocumentMetadata:
    return DocumentMetadata(
        canonical_document_id=str(row["canonical_document_id"]),
        version_id=str(row["version_id"]),
        version_number=int(str(row["version_number"])),
        title=str(row["title"]),
        original_filename=str(row["original_filename"]),
        status=str(row["status"]),
        checksum_sha256=str(row["checksum_sha256"]),
        size_bytes=int(str(row["size_bytes"])),
        mime_type=str(row["mime_type"]),
        classification=Classification(str(row["classification"])),
        created_by_subject=str(row["created_by_subject"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _stored_object(row: dict[str, object]) -> StoredObject:
    return StoredObject(
        object_id=str(row["object_id"]),
        bucket_name=str(row["bucket_name"]),
        object_key=str(row["object_key"]),
        object_version=(
            str(row["object_version"]) if row["object_version"] is not None else None
        ),
        checksum_sha256=str(row["checksum_sha256"]),
        size_bytes=int(str(row["size_bytes"])),
        mime_type=str(row["mime_type"]),
        object_kind=ObjectKind(str(row["object_kind"])),
        retention_mode=str(row["retention_mode"]),
        retention_until=cast(datetime | None, row["retention_until"]),
        created_by_subject=str(row["created_by_subject"]),
        created_at=cast(datetime, row["created_at"]),
    )


_METADATA_SELECT = """
    SELECT
      d.canonical_document_id,
      v.version_id,
      v.version_number,
      d.title,
      d.original_filename,
      d.status,
      v.checksum_sha256,
      v.size_bytes,
      v.mime_type,
      acl.classification,
      v.created_by_subject,
      v.created_at
    FROM pms_doc.document_record AS d
    JOIN pms_doc.document_version AS v
      ON v.canonical_document_id = d.canonical_document_id
    JOIN pms_doc.document_acl AS acl
      ON acl.canonical_document_id = d.canonical_document_id
"""


class PostgresDocumentRegistry:
    """Parameterized registry adapter inside one authorization-scoped transaction."""

    def __init__(
        self,
        connection: Connection,
        context: AuthorizationContext,
    ) -> None:
        self._connection = connection
        apply_postgres_session_context(connection, context)

    def find_duplicate(
        self,
        checksum_sha256: str,
        document_id: str | None,
    ) -> DocumentMetadata | None:
        conditions = ["v.checksum_sha256 = :checksum_sha256"]
        parameters: dict[str, object] = {"checksum_sha256": checksum_sha256}
        if document_id is not None:
            conditions.append("d.canonical_document_id = :document_id")
            parameters["document_id"] = document_id
        row = (
            self._connection.execute(
                text(
                    _METADATA_SELECT
                    + " WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY v.created_at DESC LIMIT 1"
                ),
                parameters,
            )
            .mappings()
            .one_or_none()
        )
        return _metadata(dict(row)) if row is not None else None

    def get_document(self, document_id: str) -> DocumentMetadata | None:
        row = (
            self._connection.execute(
                text(
                    _METADATA_SELECT
                    + """
                    WHERE d.canonical_document_id = :document_id
                      AND v.version_number = d.current_version
                    """
                ),
                {"document_id": document_id},
            )
            .mappings()
            .one_or_none()
        )
        return _metadata(dict(row)) if row is not None else None

    def next_version(self, document_id: str) -> int:
        value = self._connection.execute(
            text(
                "SELECT COALESCE(max(version_number), 0) + 1 "
                "FROM pms_doc.document_version "
                "WHERE canonical_document_id = :document_id"
            ),
            {"document_id": document_id},
        ).scalar_one()
        return int(value)

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
            self._connection.execute(
                text(
                    """
                    INSERT INTO pms_doc.document_acl
                    (canonical_document_id, canonical_tenant_id, classification,
                     allowed_roles, allowed_departments)
                    VALUES
                    (:document_id, :tenant_id, :classification,
                     :allowed_roles, :allowed_departments)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                    "classification": classification.value,
                    "allowed_roles": [role.value for role in allowed_roles],
                    "allowed_departments": list(allowed_departments),
                },
            )
            self._connection.execute(
                text(
                    """
                    INSERT INTO pms_doc.document_record
                    (canonical_document_id, title, original_filename, status,
                     current_version, created_by_subject, created_at, updated_at)
                    VALUES
                    (:document_id, :title, :filename, 'uploaded', 0,
                     :created_by_subject, :created_at, :created_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "title": title,
                    "filename": filename,
                    "created_by_subject": stored_object.created_by_subject,
                    "created_at": stored_object.created_at,
                },
            )
        self._insert_stored_object(stored_object)
        self._connection.execute(
            text(
                """
                INSERT INTO pms_doc.document_version
                (version_id, canonical_document_id, version_number,
                 original_object_id, checksum_sha256, size_bytes, mime_type,
                 created_by_subject, created_at)
                VALUES
                (:version_id, :document_id, :version_number, :object_id,
                 :checksum_sha256, :size_bytes, :mime_type,
                 :created_by_subject, :created_at)
                """
            ),
            {
                "version_id": version_id,
                "document_id": document_id,
                "version_number": version_number,
                "object_id": stored_object.object_id,
                "checksum_sha256": stored_object.checksum_sha256,
                "size_bytes": stored_object.size_bytes,
                "mime_type": stored_object.mime_type,
                "created_by_subject": stored_object.created_by_subject,
                "created_at": stored_object.created_at,
            },
        )
        self._connection.execute(
            text(
                """
                UPDATE pms_doc.document_record
                SET current_version = :version_number,
                    status = 'uploaded',
                    updated_at = :updated_at
                WHERE canonical_document_id = :document_id
                """
            ),
            {
                "version_number": version_number,
                "updated_at": stored_object.created_at,
                "document_id": document_id,
            },
        )

    def _insert_stored_object(self, stored_object: StoredObject) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO pms_doc.stored_object
                (object_id, bucket_name, object_key, object_version,
                 checksum_sha256, size_bytes, mime_type, object_kind,
                 retention_mode, retention_until, created_by_subject, created_at)
                VALUES
                (:object_id, :bucket_name, :object_key, :object_version,
                 :checksum_sha256, :size_bytes, :mime_type, :object_kind,
                 :retention_mode, :retention_until, :created_by_subject, :created_at)
                """
            ),
            {
                "object_id": stored_object.object_id,
                "bucket_name": stored_object.bucket_name,
                "object_key": stored_object.object_key,
                "object_version": stored_object.object_version,
                "checksum_sha256": stored_object.checksum_sha256,
                "size_bytes": stored_object.size_bytes,
                "mime_type": stored_object.mime_type,
                "object_kind": stored_object.object_kind.value,
                "retention_mode": stored_object.retention_mode,
                "retention_until": stored_object.retention_until,
                "created_by_subject": stored_object.created_by_subject,
                "created_at": stored_object.created_at,
            },
        )

    def get_original_object(self, version_id: str) -> StoredObject | None:
        row = (
            self._connection.execute(
                text(
                    """
                    SELECT
                      o.object_id, o.bucket_name, o.object_key, o.object_version,
                      o.checksum_sha256, o.size_bytes, o.mime_type, o.object_kind,
                      o.retention_mode, o.retention_until,
                      o.created_by_subject, o.created_at
                    FROM pms_doc.document_version AS v
                    JOIN pms_doc.stored_object AS o
                      ON o.object_id = v.original_object_id
                    WHERE v.version_id = :version_id
                    """
                ),
                {"version_id": version_id},
            )
            .mappings()
            .one_or_none()
        )
        return _stored_object(dict(row)) if row is not None else None

    def update_status(self, document_id: str, status: str, updated_at: datetime) -> None:
        self._connection.execute(
            text(
                """
                UPDATE pms_doc.document_record
                SET status = :status, updated_at = :updated_at
                WHERE canonical_document_id = :document_id
                """
            ),
            {
                "status": status,
                "updated_at": updated_at,
                "document_id": document_id,
            },
        )

    def get_derived(
        self,
        version_id: str,
        object_kind: ObjectKind,
        producer: str,
        producer_version: str,
    ) -> tuple[StoredObject, str, str] | None:
        row = (
            self._connection.execute(
                text(
                    """
                    SELECT
                      o.object_id, o.bucket_name, o.object_key, o.object_version,
                      o.checksum_sha256, o.size_bytes, o.mime_type, o.object_kind,
                      o.retention_mode, o.retention_until,
                      o.created_by_subject, o.created_at,
                      a.producer, a.producer_version
                    FROM pms_doc.derived_artifact AS a
                    JOIN pms_doc.stored_object AS o
                      ON o.object_id = a.object_id
                    WHERE a.document_version_id = :version_id
                      AND a.artifact_kind = :object_kind
                      AND a.producer = :producer
                      AND a.producer_version = :producer_version
                    ORDER BY a.created_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "version_id": version_id,
                    "object_kind": object_kind.value,
                    "producer": producer,
                    "producer_version": producer_version,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        values = dict(row)
        return (
            _stored_object(values),
            str(values["producer"]),
            str(values["producer_version"]),
        )

    def save_derived(
        self,
        *,
        artifact_id: str,
        version_id: str,
        stored_object: StoredObject,
        producer: str,
        producer_version: str,
    ) -> None:
        self._insert_stored_object(stored_object)
        self._connection.execute(
            text(
                """
                INSERT INTO pms_doc.derived_artifact
                (artifact_id, document_version_id, object_id, artifact_kind,
                 producer, producer_version, created_at)
                VALUES
                (:artifact_id, :version_id, :object_id, :artifact_kind,
                 :producer, :producer_version, :created_at)
                """
            ),
            {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "object_id": stored_object.object_id,
                "artifact_kind": stored_object.object_kind.value,
                "producer": producer,
                "producer_version": producer_version,
                "created_at": stored_object.created_at,
            },
        )

    def record_audit(self, event: AuditEvent) -> None:
        write_audit_event(self._connection, event)
