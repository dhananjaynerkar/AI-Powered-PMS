"""Vendor-neutral Phase 06 parsing, quality and canonical JSON contracts."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol

from pms_common.settings import Settings
from pydantic import BaseModel, ConfigDict, Field

from pms_ingestion.models import DocumentMetadata


class ParserError(RuntimeError):
    """Base error for a bounded parser attempt."""


class ParserUnavailable(ParserError):
    """Raised when an optional local parser runtime is not installed or configured."""


class TransientParserError(ParserError):
    """Raised for retryable local process or service failures."""


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    CAPTION = "caption"
    IMAGE = "image"
    FORMULA = "formula"
    OTHER = "other"


class IssueSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: float
    bottom: float
    right: float
    top: float

    @property
    def valid(self) -> bool:
        return self.right >= self.left and self.top >= self.bottom


class ParsedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    kind: BlockKind
    text: str = ""
    bounding_box: BoundingBox | None = None
    heading_level: int | None = Field(default=None, ge=1, le=6)
    table_rows: int | None = Field(default=None, ge=0)
    table_columns: int | None = Field(default=None, ge=0)
    table_has_header: bool | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    source_element_id: str | None = None


class ParsedPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    blocks: tuple[ParsedBlock, ...] = ()


class ParserOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parser: str
    parser_version: str
    mode: str
    page_count: int = Field(ge=0)
    pages: tuple[ParsedPage, ...]
    markdown: str = ""
    raw_payload: dict[str, object]

    @property
    def text(self) -> str:
        return "\n".join(
            block.text
            for page in self.pages
            for block in page.blocks
            if block.text.strip()
        )


class VerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    provider_version: str
    page_count: int = Field(ge=0)
    text: str
    table_count: int = Field(default=0, ge=0)


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: IssueSeverity
    message: str
    page_number: int | None = Field(default=None, ge=1)


class QualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_count: int = Field(ge=0)
    page_coverage: float = Field(ge=0, le=1)
    unexpected_blank_page_ratio: float = Field(ge=0, le=1)
    bounding_box_coverage: float = Field(ge=0, le=1)
    heading_count: int = Field(ge=0)
    clause_or_proviso_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    invalid_table_count: int = Field(ge=0)
    reading_order_error_count: int = Field(ge=0)
    heading_hierarchy_error_count: int = Field(ge=0)
    exact_numeric_token_count: int = Field(ge=0)
    missing_numeric_token_count: int = Field(ge=0)
    clause_boundary_count: int = Field(ge=0)
    missing_clause_boundary_count: int = Field(ge=0)
    formula_symbol_count: int = Field(ge=0)
    missing_formula_symbol_count: int = Field(ge=0)
    ocr_block_count: int = Field(ge=0)
    mean_ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    devanagari_character_count: int = Field(ge=0)
    prompt_injection_indicator_count: int = Field(ge=0)


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    review_required: bool
    metrics: QualityMetrics
    issues: tuple[QualityIssue, ...]

    @property
    def decision(self) -> Literal["PASS", "CONDITIONAL_PASS", "HARD_FAIL"]:
        """Expose the three-way gate without weakening the existing booleans."""

        if any(
            issue.severity in {IssueSeverity.ERROR, IssueSeverity.CRITICAL}
            for issue in self.issues
        ):
            return "HARD_FAIL"
        if self.review_required or self.issues:
            return "CONDITIONAL_PASS"
        return "PASS"


class CanonicalBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    page_number: int
    reading_order: int
    kind: BlockKind
    text: str
    bounding_box: BoundingBox | None
    heading_level: int | None
    table_rows: int | None
    table_columns: int | None
    table_has_header: bool | None
    ocr_confidence: float | None
    language_code: str
    languages: tuple[str, ...]
    script_code: str
    source_element_id: str | None


class CanonicalPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int
    width: float | None
    height: float | None
    language_code: str
    languages: tuple[str, ...]
    script_code: str
    blocks: tuple[CanonicalBlock, ...]


class CanonicalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    canonical_document_id: str
    document_version_id: str
    document_version_number: int
    original_checksum_sha256: str
    parser: str
    parser_version: str
    parser_mode: str
    parsed_at: datetime
    pages: tuple[CanonicalPage, ...]
    quality: QualityReport


class ParserAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parser: str
    attempt: int = Field(ge=1)
    result: str
    detail: str | None = None


class ParsingOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_parser: str | None
    fallback_used: bool
    raw_outputs: tuple[ParserOutput, ...]
    quality: QualityReport | None
    canonical: CanonicalDocument | None
    attempts: tuple[ParserAttempt, ...]
    final_status: str


class ParserAdapter(Protocol):
    name: str

    def parse(self, content: bytes, filename: str) -> ParserOutput: ...


class ParserVerifier(Protocol):
    def verify(self, content: bytes, filename: str) -> VerificationEvidence: ...


_NUMERIC_TOKEN = re.compile(
    r"(?:₹\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?:\b\d+(?:\.\d+)?\s?%)"
    r"|(?:\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b)"
    r"|(?:\b(?:section|clause)\s+\d+(?:\.\d+)*\b)",
    re.IGNORECASE,
)
_CLAUSE_OR_PROVISO = re.compile(r"\b(?:clause|section|proviso|provided that)\b", re.I)
_FORMULA_SYMBOL = re.compile(r"[=×÷±≤≥∑√]")
_PROMPT_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous\s+instructions)"
    r"|(?:reveal\s+(?:the\s+)?system\s+prompt)"
    r"|(?:developer\s+message)"
    r"|(?:execute\s+(?:this\s+)?command)",
    re.IGNORECASE,
)
_DEVANAGARI = re.compile(r"[\u0900-\u097f]")
_LATIN = re.compile(r"[A-Za-z]")


def _language_metadata(text: str) -> tuple[str, tuple[str, ...], str]:
    """Return script-safe language metadata without conflating Hindi and Marathi."""

    has_devanagari = bool(_DEVANAGARI.search(text))
    has_latin = bool(_LATIN.search(text))
    if has_devanagari and has_latin:
        return "mul", ("und-Deva", "en"), "Deva+Latn"
    if has_devanagari:
        return "und-Deva", ("und-Deva",), "Deva"
    if has_latin:
        return "en", ("en",), "Latn"
    return "und", ("und",), "Zyyy"


class ExtractionQualityGate:
    """Evaluate structural completeness and exact-token preservation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def evaluate(
        self,
        output: ParserOutput,
        verification: VerificationEvidence | None,
    ) -> QualityReport:
        issues: list[QualityIssue] = []
        pages = output.pages
        nonblank_pages = sum(
            1 for page in pages if any(block.text.strip() for block in page.blocks)
        )
        page_count = output.page_count
        coverage = nonblank_pages / page_count if page_count else 0.0
        blank_ratio = 1 - coverage if page_count else 1.0
        blocks = [block for page in pages for block in page.blocks]
        bbox_coverage = (
            sum(1 for block in blocks if block.bounding_box and block.bounding_box.valid)
            / len(blocks)
            if blocks
            else 0.0
        )

        if page_count == 0:
            issues.append(
                QualityIssue(
                    code="NO_PAGES",
                    severity=IssueSeverity.CRITICAL,
                    message="parser returned zero pages",
                )
            )
        if len(pages) != page_count:
            issues.append(
                QualityIssue(
                    code="PAGE_COUNT_MISMATCH",
                    severity=IssueSeverity.CRITICAL,
                    message="declared page count differs from parsed pages",
                )
            )
        expected_page_numbers = list(range(1, page_count + 1))
        actual_page_numbers = [page.page_number for page in pages]
        if actual_page_numbers != expected_page_numbers:
            issues.append(
                QualityIssue(
                    code="PAGE_ORDER_INVALID",
                    severity=IssueSeverity.CRITICAL,
                    message="parsed pages are missing, duplicated or out of order",
                )
            )
        if verification is not None and verification.page_count != page_count:
            issues.append(
                QualityIssue(
                    code="VERIFIED_PAGE_COUNT_MISMATCH",
                    severity=IssueSeverity.CRITICAL,
                    message="parser and independent verifier page counts differ",
                )
            )
        if blank_ratio > self._settings.pdf_max_unexpected_blank_page_ratio:
            issues.append(
                QualityIssue(
                    code="UNEXPECTED_BLANK_PAGES",
                    severity=IssueSeverity.ERROR,
                    message="unexpected blank-page ratio exceeds the configured limit",
                )
            )
        if self._settings.pdf_citation_bbox_required and bbox_coverage < 1:
            issues.append(
                QualityIssue(
                    code="MISSING_CITATION_BBOX",
                    severity=IssueSeverity.CRITICAL,
                    message="one or more extracted blocks lack citation coordinates",
                )
            )

        invalid_tables = [
            block
            for block in blocks
            if block.kind is BlockKind.TABLE
            and (
                not block.table_rows
                or not block.table_columns
                or block.table_has_header is False
            )
        ]
        if invalid_tables:
            issues.append(
                QualityIssue(
                    code="INVALID_TABLE_STRUCTURE",
                    severity=IssueSeverity.ERROR,
                    message="one or more tables lack valid dimensions or headers",
                )
            )
        parsed_table_count = sum(block.kind is BlockKind.TABLE for block in blocks)
        if (
            verification is not None
            and verification.table_count > 0
            and verification.table_count != parsed_table_count
        ):
            issues.append(
                QualityIssue(
                    code="TABLE_COUNT_MISMATCH",
                    severity=IssueSeverity.ERROR,
                    message="parser and independent verifier table counts differ",
                )
            )

        reading_order_errors = 0
        for page in pages:
            orders = [block.reading_order for block in page.blocks]
            if orders != list(range(len(orders))):
                reading_order_errors += 1
        if reading_order_errors:
            issues.append(
                QualityIssue(
                    code="READING_ORDER_INVALID",
                    severity=IssueSeverity.ERROR,
                    message="one or more pages have discontinuous or duplicated reading order",
                )
            )

        heading_levels = [
            block.heading_level for block in blocks if block.kind is BlockKind.HEADING
        ]
        heading_hierarchy_errors = sum(
            level is None
            or (
                previous is not None
                and level is not None
                and level > previous + 1
            )
            for previous, level in zip(
                [None, *heading_levels],
                heading_levels,
                strict=False,
            )
        )
        if heading_hierarchy_errors:
            issues.append(
                QualityIssue(
                    code="HEADING_HIERARCHY_INVALID",
                    severity=IssueSeverity.ERROR,
                    message="heading levels are missing or skip a hierarchy level",
                )
            )

        parsed_tokens = set(_NUMERIC_TOKEN.findall(output.text))
        reference_tokens = (
            set(_NUMERIC_TOKEN.findall(verification.text)) if verification else set()
        )
        missing_tokens = reference_tokens - parsed_tokens
        if (
            self._settings.pdf_numeric_token_exact_match_required
            and verification is None
        ):
            issues.append(
                QualityIssue(
                    code="VERIFICATION_UNAVAILABLE",
                    severity=IssueSeverity.WARNING,
                    message="exact-token verifier is unavailable",
                )
            )
        elif missing_tokens:
            issues.append(
                QualityIssue(
                    code="NUMERIC_TOKEN_MISMATCH",
                    severity=IssueSeverity.CRITICAL,
                    message="parser output omitted one or more exact numeric/legal tokens",
                )
            )

        clause_boundary_count = len(_CLAUSE_OR_PROVISO.findall(output.text))
        reference_clause_boundary_count = (
            len(_CLAUSE_OR_PROVISO.findall(verification.text)) if verification else 0
        )
        missing_clause_boundaries = max(
            0,
            reference_clause_boundary_count - clause_boundary_count,
        )
        if missing_clause_boundaries:
            issues.append(
                QualityIssue(
                    code="CLAUSE_BOUNDARY_MISMATCH",
                    severity=IssueSeverity.CRITICAL,
                    message="parser output omitted one or more clause or proviso boundaries",
                )
            )

        formula_symbols = set(_FORMULA_SYMBOL.findall(output.text))
        reference_formula_symbols = (
            set(_FORMULA_SYMBOL.findall(verification.text)) if verification else set()
        )
        missing_formula_symbols = reference_formula_symbols - formula_symbols
        if missing_formula_symbols:
            issues.append(
                QualityIssue(
                    code="FORMULA_SYMBOL_MISMATCH",
                    severity=IssueSeverity.CRITICAL,
                    message="parser output omitted one or more exact formula symbols",
                )
            )

        ocr_confidences = [
            block.ocr_confidence
            for block in blocks
            if block.ocr_confidence is not None
        ]
        mean_ocr = (
            sum(ocr_confidences) / len(ocr_confidences) if ocr_confidences else None
        )
        if (
            mean_ocr is not None
            and mean_ocr < self._settings.ocr_confidence_review_threshold
        ):
            issues.append(
                QualityIssue(
                    code="LOW_OCR_CONFIDENCE",
                    severity=IssueSeverity.ERROR,
                    message="mean OCR confidence is below the review threshold",
                )
            )

        devanagari_count = len(_DEVANAGARI.findall(output.text))
        if devanagari_count and "\ufffd" in output.text:
            issues.append(
                QualityIssue(
                    code="DEVANAGARI_INTEGRITY_FAILURE",
                    severity=IssueSeverity.CRITICAL,
                    message="replacement characters are present in Devanagari text",
                )
            )

        injection_count = len(_PROMPT_INJECTION.findall(output.text))
        if injection_count:
            issues.append(
                QualityIssue(
                    code="PROMPT_INJECTION_INDICATOR",
                    severity=IssueSeverity.WARNING,
                    message="untrusted document text contains instruction-like content",
                )
            )

        metrics = QualityMetrics(
            page_count=page_count,
            page_coverage=coverage,
            unexpected_blank_page_ratio=blank_ratio,
            bounding_box_coverage=bbox_coverage,
            heading_count=sum(block.kind is BlockKind.HEADING for block in blocks),
            clause_or_proviso_count=clause_boundary_count,
            table_count=parsed_table_count,
            invalid_table_count=len(invalid_tables),
            reading_order_error_count=reading_order_errors,
            heading_hierarchy_error_count=heading_hierarchy_errors,
            exact_numeric_token_count=len(parsed_tokens),
            missing_numeric_token_count=len(missing_tokens),
            clause_boundary_count=clause_boundary_count,
            missing_clause_boundary_count=missing_clause_boundaries,
            formula_symbol_count=len(formula_symbols),
            missing_formula_symbol_count=len(missing_formula_symbols),
            ocr_block_count=len(ocr_confidences),
            mean_ocr_confidence=mean_ocr,
            devanagari_character_count=devanagari_count,
            prompt_injection_indicator_count=injection_count,
        )
        passed = not any(
            issue.severity in {IssueSeverity.ERROR, IssueSeverity.CRITICAL}
            for issue in issues
        )
        review_required = any(
            issue.severity is IssueSeverity.CRITICAL
            or issue.code in {"LOW_OCR_CONFIDENCE", "PROMPT_INJECTION_INDICATOR"}
            for issue in issues
        )
        return QualityReport(
            passed=passed,
            review_required=review_required,
            metrics=metrics,
            issues=tuple(issues),
        )


def build_canonical_document(
    metadata: DocumentMetadata,
    output: ParserOutput,
    quality: QualityReport,
    settings: Settings,
    *,
    parsed_at: datetime | None = None,
) -> CanonicalDocument:
    """Project vendor output into the stable canonical schema."""

    canonical_pages: list[CanonicalPage] = []
    for page in output.pages:
        canonical_blocks: list[CanonicalBlock] = []
        for block in sorted(page.blocks, key=lambda item: item.reading_order):
            language_code, languages, script_code = _language_metadata(block.text)
            canonical_blocks.append(
                CanonicalBlock(
                    **block.model_dump(),
                    language_code=language_code,
                    languages=languages,
                    script_code=script_code,
                )
            )
        page_text = "\n".join(block.text for block in canonical_blocks)
        language_code, languages, script_code = _language_metadata(page_text)
        canonical_pages.append(
            CanonicalPage(
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                language_code=language_code,
                languages=languages,
                script_code=script_code,
                blocks=tuple(canonical_blocks),
            )
        )
    return CanonicalDocument(
        schema_version=settings.canonical_schema_version,
        canonical_document_id=metadata.canonical_document_id,
        document_version_id=metadata.version_id,
        document_version_number=metadata.version_number,
        original_checksum_sha256=metadata.checksum_sha256,
        parser=output.parser,
        parser_version=output.parser_version,
        parser_mode=output.mode,
        parsed_at=parsed_at or datetime.now(UTC),
        pages=tuple(canonical_pages),
        quality=quality,
    )


class ParsingEngine:
    """Run a bounded local parser chain and stop at the first accepted result."""

    def __init__(
        self,
        settings: Settings,
        adapters: Sequence[ParserAdapter],
        verifier: ParserVerifier | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not adapters:
            raise ValueError("at least one parser adapter is required")
        self._settings = settings
        self._adapters = tuple(adapters)
        self._verifier = verifier
        self._quality = ExtractionQualityGate(settings)
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self,
        metadata: DocumentMetadata,
        content: bytes,
    ) -> ParsingOutcome:
        verification: VerificationEvidence | None = None
        attempts: list[ParserAttempt] = []
        if self._verifier is not None:
            try:
                verification = self._verifier.verify(content, metadata.original_filename)
            except ParserError as error:
                attempts.append(
                    ParserAttempt(
                        parser="verification",
                        attempt=1,
                        result="unavailable",
                        detail=str(error),
                    )
                )

        raw_outputs: list[ParserOutput] = []
        last_quality: QualityReport | None = None
        for adapter_index, adapter in enumerate(self._adapters):
            output: ParserOutput | None = None
            for attempt_number in range(1, self._settings.pdf_max_retries + 2):
                try:
                    output = adapter.parse(content, metadata.original_filename)
                    attempts.append(
                        ParserAttempt(
                            parser=adapter.name,
                            attempt=attempt_number,
                            result="parsed",
                        )
                    )
                    break
                except ParserUnavailable as error:
                    attempts.append(
                        ParserAttempt(
                            parser=adapter.name,
                            attempt=attempt_number,
                            result="unavailable",
                            detail=str(error),
                        )
                    )
                    break
                except TransientParserError as error:
                    attempts.append(
                        ParserAttempt(
                            parser=adapter.name,
                            attempt=attempt_number,
                            result="transient_error",
                            detail=str(error),
                        )
                    )
                    if attempt_number > self._settings.pdf_max_retries:
                        break
                except ParserError as error:
                    attempts.append(
                        ParserAttempt(
                            parser=adapter.name,
                            attempt=attempt_number,
                            result="failed",
                            detail=str(error),
                        )
                    )
                    break
            if output is None:
                continue
            raw_outputs.append(output)
            quality = self._quality.evaluate(output, verification)
            last_quality = quality
            attempts.append(
                ParserAttempt(
                    parser=adapter.name,
                    attempt=1,
                    result="quality_passed" if quality.passed else "quality_failed",
                )
            )
            if not quality.passed:
                continue
            canonical = build_canonical_document(
                metadata,
                output,
                quality,
                self._settings,
                parsed_at=self._clock(),
            )
            return ParsingOutcome(
                selected_parser=adapter.name,
                fallback_used=adapter_index > 0,
                raw_outputs=tuple(raw_outputs),
                quality=quality,
                canonical=canonical,
                attempts=tuple(attempts),
                final_status=(
                    "review_required" if quality.review_required else "canonicalized"
                ),
            )

        return ParsingOutcome(
            selected_parser=None,
            fallback_used=len(self._adapters) > 1,
            raw_outputs=tuple(raw_outputs),
            quality=last_quality,
            canonical=None,
            attempts=tuple(attempts),
            final_status=(
                "review_required"
                if self._settings.pdf_human_review_on_critical_failure
                else "quality_failed"
            ),
        )
