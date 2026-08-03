from __future__ import annotations

from datetime import UTC, datetime

from pms_common.security import Classification
from pms_ingestion.models import DocumentMetadata
from pms_ingestion.parsing import (
    BlockKind,
    BoundingBox,
    ParsedBlock,
    ParsedPage,
    ParserError,
    ParserOutput,
    TransientParserError,
    VerificationEvidence,
)


def metadata() -> DocumentMetadata:
    return DocumentMetadata(
        canonical_document_id="document-1",
        version_id="version-1",
        version_number=1,
        title="Controlled parser fixture",
        original_filename="controlled.pdf",
        status="uploaded",
        checksum_sha256="a" * 64,
        size_bytes=100,
        mime_type="application/pdf",
        classification=Classification.INTERNAL,
        created_by_subject="do-1",
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
    )


def output(
    *,
    parser: str = "primary",
    text: str = "Section 4 requires ₹1,250.00 at 18% on 01/04/2026.",
    bbox: BoundingBox | None = None,
    ocr_confidence: float | None = None,
    kind: BlockKind = BlockKind.PARAGRAPH,
) -> ParserOutput:
    return ParserOutput(
        parser=parser,
        parser_version="1.0-test",
        mode="deterministic",
        page_count=1,
        pages=(
            ParsedPage(
                page_number=1,
                width=612,
                height=792,
                blocks=(
                    ParsedBlock(
                        block_id=f"{parser}-1",
                        page_number=1,
                        reading_order=0,
                        kind=kind,
                        text=text,
                        bounding_box=bbox
                        or BoundingBox(left=10, bottom=10, right=200, top=40),
                        ocr_confidence=ocr_confidence,
                    ),
                ),
            ),
        ),
        markdown=text,
        raw_payload={"parser": parser, "text": text},
    )


class FakeAdapter:
    def __init__(
        self,
        name: str,
        result: ParserOutput | None = None,
        *,
        failures: int = 0,
        permanent_error: bool = False,
    ) -> None:
        self.name = name
        self.result = result or output(parser=name)
        self.failures = failures
        self.permanent_error = permanent_error
        self.calls = 0

    def parse(self, content: bytes, filename: str) -> ParserOutput:
        del content, filename
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientParserError("temporary parser failure")
        if self.permanent_error:
            raise ParserError("permanent parser failure")
        return self.result


class FakeVerifier:
    def __init__(
        self,
        text: str = "Section 4 requires ₹1,250.00 at 18% on 01/04/2026.",
    ) -> None:
        self.text = text

    def verify(self, content: bytes, filename: str) -> VerificationEvidence:
        del content, filename
        return VerificationEvidence(
            provider="controlled-verifier",
            provider_version="1-test",
            page_count=1,
            text=self.text,
            table_count=0,
        )
