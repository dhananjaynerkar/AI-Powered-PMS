"""Bounded upload validation before any object is persisted."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePath

from pms_common.settings import Settings


class UploadValidationError(ValueError):
    """Raised when an upload violates the configured safety contract."""


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    extension: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str


_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class UploadValidator:
    """Validate name, type, signature, size and checksum deterministically."""

    def __init__(self, settings: Settings) -> None:
        self._extensions = frozenset(
            value.strip().lower()
            for value in settings.upload_extension_allowlist.split(",")
            if value.strip()
        )
        self._mime_types = frozenset(
            value.strip().lower()
            for value in settings.upload_mime_allowlist.split(",")
            if value.strip()
        )
        self.max_bytes = settings.upload_max_mb * 1024 * 1024

    def validate(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> ValidatedUpload:
        clean_name = filename.strip()
        if (
            not clean_name
            or "\x00" in clean_name
            or "/" in clean_name
            or "\\" in clean_name
            or PurePath(clean_name).name != clean_name
        ):
            raise UploadValidationError("filename must be a plain basename")
        if not content:
            raise UploadValidationError("empty uploads are not allowed")
        if len(content) > self.max_bytes:
            raise UploadValidationError("upload exceeds the configured size limit")

        extension = PurePath(clean_name).suffix.lower()
        normalized_mime = mime_type.partition(";")[0].strip().lower()
        if extension not in self._extensions:
            raise UploadValidationError("file extension is not allowed")
        if normalized_mime not in self._mime_types:
            raise UploadValidationError("MIME type is not allowed")
        if _MIME_BY_EXTENSION.get(extension) != normalized_mime:
            raise UploadValidationError("file extension and MIME type do not match")

        self._validate_signature(extension, content)
        return ValidatedUpload(
            filename=clean_name,
            extension=extension,
            mime_type=normalized_mime,
            size_bytes=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
        )

    @staticmethod
    def _validate_signature(extension: str, content: bytes) -> None:
        if extension == ".pdf" and not content.startswith(b"%PDF-"):
            raise UploadValidationError("PDF signature is invalid")
        if extension == ".xlsx" and not content.startswith(b"PK\x03\x04"):
            raise UploadValidationError("XLSX signature is invalid")
        if extension == ".json":
            try:
                json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise UploadValidationError("JSON content is invalid") from error
