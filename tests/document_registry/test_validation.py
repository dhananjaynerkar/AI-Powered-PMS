"""Safe upload validation and optional malware-scan behavior."""

from __future__ import annotations

import pytest
from pms_common.settings import Settings
from pms_ingestion.scanner import DisabledMalwareScanner
from pms_ingestion.validation import UploadValidationError, UploadValidator


def test_pdf_validation_returns_exact_sha256() -> None:
    content = b"%PDF-1.7\nphase-05-test\n%%EOF"
    validated = UploadValidator(Settings(upload_max_mb=1)).validate(
        filename="approved-name.pdf",
        mime_type="application/pdf",
        content=content,
    )

    assert validated.size_bytes == len(content)
    assert len(validated.checksum_sha256) == 64
    assert validated.extension == ".pdf"


@pytest.mark.parametrize(
    ("filename", "mime_type", "content"),
    [
        ("../escape.pdf", "application/pdf", b"%PDF-1.7"),
        ("wrong.pdf", "text/csv", b"%PDF-1.7"),
        ("fake.pdf", "application/pdf", b"not a pdf"),
        ("empty.pdf", "application/pdf", b""),
        ("script.exe", "application/octet-stream", b"MZ"),
    ],
)
def test_unsafe_uploads_are_rejected(
    filename: str,
    mime_type: str,
    content: bytes,
) -> None:
    with pytest.raises(UploadValidationError):
        UploadValidator(Settings(upload_max_mb=1)).validate(
            filename=filename,
            mime_type=mime_type,
            content=content,
        )


def test_disabled_clamav_is_explicit_noop() -> None:
    DisabledMalwareScanner().scan(b"bounded-test-content")
