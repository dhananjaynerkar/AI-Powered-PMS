"""Phase 05 storage commands and Phase 06 parsing without chunking or indexing."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from pms_common.database import create_database_engine
from pms_common.security import (
    AuthenticationError,
    AuthorizationContext,
    AuthorizationDenied,
    Classification,
    JwtValidator,
)
from pms_common.settings import Settings

from pms_ingestion.factory import create_document_service
from pms_ingestion.parsing import ParserError
from pms_ingestion.parsing_service import (
    DocumentParsingCoordinator,
    ParsingServiceError,
)
from pms_ingestion.scanner import MalwareDetected, MalwareScannerError
from pms_ingestion.service import DocumentServiceError
from pms_ingestion.storage import (
    MinioObjectStore,
    ObjectStorageError,
    configured_bucket_names,
)
from pms_ingestion.validation import UploadValidationError

_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pms_ingestion.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    storage = commands.add_parser("storage")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("check")

    upload = commands.add_parser("upload")
    upload.add_argument("--file", type=Path, required=True)
    upload.add_argument("--title", required=True)
    upload.add_argument("--document-id")
    upload.add_argument(
        "--classification",
        choices=[value.value for value in Classification],
        default=Classification.INTERNAL.value,
    )

    retrieve = commands.add_parser("get")
    retrieve.add_argument("--document-id", required=True)
    retrieve.add_argument("--output", type=Path, required=True)

    parse = commands.add_parser("parse")
    parse.add_argument("--file", type=Path, required=True)
    parse.add_argument("--title")
    parse.add_argument("--document-id")
    parse.add_argument(
        "--classification",
        choices=[value.value for value in Classification],
        default=Classification.INTERNAL.value,
    )
    parse.add_argument("--force", action="store_true")
    parse.add_argument("--explain", action="store_true")
    return parser


def _error(message: str) -> NoReturn:
    raise SystemExit(message)


def _trusted_context(settings: Settings) -> AuthorizationContext:
    token = os.environ.get("PMS_ACCESS_TOKEN", "").strip()
    if not token:
        _error("PMS_ACCESS_TOKEN is required for document commands")
    try:
        return JwtValidator(settings).validate(token)
    except AuthenticationError:
        _error("PMS_ACCESS_TOKEN is invalid")


def _storage_check(settings: Settings) -> int:
    store = MinioObjectStore(settings)
    buckets = store.ensure_buckets(configured_bucket_names(settings))
    print(
        json.dumps(
            {
                "status": "PASS",
                "endpoint": settings.minio_endpoint,
                "secure": settings.minio_secure,
                "versioning": "enabled",
                "buckets": buckets,
            },
            sort_keys=True,
        )
    )
    return 0


def _upload(settings: Settings, arguments: argparse.Namespace) -> int:
    file_path: Path = arguments.file
    if not file_path.is_file():
        _error("upload file does not exist")
    mime_type = _MIME_BY_EXTENSION.get(file_path.suffix.lower())
    if mime_type is None:
        _error("upload extension is not supported")
    context = _trusted_context(settings)
    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            service = create_document_service(connection, context, settings)
            result = service.upload(
                title=arguments.title,
                filename=file_path.name,
                mime_type=mime_type,
                content=file_path.read_bytes(),
                classification=Classification(arguments.classification),
                document_id=arguments.document_id,
            )
        print(
            json.dumps(
                {
                    "canonical_document_id": result.document.canonical_document_id,
                    "version": result.document.version_number,
                    "checksum_sha256": result.document.checksum_sha256,
                    "duplicate": result.duplicate,
                    "status": result.document.status,
                    "next_command": (
                        "python -m pms_ingestion.cli get "
                        f"--document-id {result.document.canonical_document_id} "
                        "--output <PATH>"
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        engine.dispose()


def _retrieve(settings: Settings, arguments: argparse.Namespace) -> int:
    context = _trusted_context(settings)
    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            service = create_document_service(connection, context, settings)
            retrieved = service.retrieve(arguments.document_id)
        output: Path = arguments.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(retrieved.content)
        print(
            json.dumps(
                {
                    "canonical_document_id": retrieved.document.canonical_document_id,
                    "checksum_sha256": retrieved.document.checksum_sha256,
                    "output": str(output.resolve()),
                    "status": "PASS",
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        engine.dispose()


def _parse_document(settings: Settings, arguments: argparse.Namespace) -> int:
    file_path: Path = arguments.file
    if not file_path.is_file():
        _error("parse file does not exist")
    if file_path.suffix.lower() != ".pdf":
        _error("Phase 06 parse accepts PDF files only")
    context = _trusted_context(settings)
    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            service = create_document_service(connection, context, settings)
            uploaded = service.upload(
                title=(arguments.title or file_path.stem),
                filename=file_path.name,
                mime_type="application/pdf",
                content=file_path.read_bytes(),
                classification=Classification(arguments.classification),
                document_id=arguments.document_id,
            )
        result = DocumentParsingCoordinator(
            engine,
            context,
            settings,
        ).parse(
            uploaded.document.canonical_document_id,
            force=arguments.force,
        )
        payload: dict[str, object] = {
            "canonical_document_id": result.canonical_document_id,
            "parser": result.parser,
            "parser_mode": result.parser_mode,
            "page_count": result.page_count,
            "quality_gate_passed": result.quality_passed,
            "review_required": result.review_required,
            "fallback_used": result.fallback_used,
            "idempotent": result.idempotent,
            "raw_parser_object_keys": result.raw_object_keys,
            "canonical_json_object_key": result.canonical_object_key,
            "issues": result.issue_codes,
            "status": result.final_status,
            "next_command": (
                "review extraction issues"
                if result.review_required
                else "Phase 07 is not implemented; stop after Phase 06 validation"
            ),
        }
        if arguments.explain:
            payload["pipeline"] = (
                "authorized original -> deterministic parser -> quality gate -> "
                "bounded fallback -> immutable raw/canonical artifacts"
            )
        print(json.dumps(payload, sort_keys=True))
        return 0 if result.quality_passed else 3
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    settings = Settings()
    try:
        if arguments.command == "storage":
            return _storage_check(settings)
        if arguments.command == "upload":
            return _upload(settings, arguments)
        if arguments.command == "get":
            return _retrieve(settings, arguments)
        return _parse_document(settings, arguments)
    except (
        DocumentServiceError,
        MalwareDetected,
        MalwareScannerError,
        ObjectStorageError,
        UploadValidationError,
        AuthorizationDenied,
        ParserError,
        ParsingServiceError,
        ValueError,
    ) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
