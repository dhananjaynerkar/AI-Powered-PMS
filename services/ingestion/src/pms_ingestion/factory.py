"""Composition helpers shared by the API and CLI."""

from __future__ import annotations

from pms_common.security import AuthorizationContext
from pms_common.settings import Settings
from sqlalchemy import Connection

from pms_ingestion.repository import PostgresDocumentRegistry
from pms_ingestion.scanner import create_malware_scanner
from pms_ingestion.service import DocumentService
from pms_ingestion.storage import MinioObjectStore, ObjectStore
from pms_ingestion.validation import UploadValidator


def create_document_service(
    connection: Connection,
    context: AuthorizationContext,
    settings: Settings,
    *,
    object_store: ObjectStore | None = None,
) -> DocumentService:
    """Create one transaction-scoped document service."""

    return DocumentService(
        PostgresDocumentRegistry(connection, context),
        object_store or MinioObjectStore(settings),
        context,
        settings,
        UploadValidator(settings),
        create_malware_scanner(settings),
    )
