from __future__ import annotations

from datetime import UTC, datetime

from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.parsing import (
    BlockKind,
    BoundingBox,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
    QualityMetrics,
    QualityReport,
)


class WordTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())

    def split(self, text: str, maximum: int, overlap: int) -> tuple[str, ...]:
        words = text.split()
        step = maximum - overlap
        return tuple(
            " ".join(words[start : start + maximum])
            for start in range(0, len(words), step)
            if words[start : start + maximum]
        )


def settings() -> Settings:
    return Settings(
        _env_file=None,
        chunk_target_tokens=40,
        chunk_max_tokens=64,
        chunk_overlap_tokens=5,
        parent_chunk_max_tokens=128,
        embedding_max_sequence_length=128,
    )


def context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="do-1",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="port-1",
        classification=Classification.RESTRICTED,
    )


def block(
    block_id: str,
    text: str,
    *,
    order: int,
    kind: BlockKind = BlockKind.PARAGRAPH,
    heading_level: int | None = None,
    language_code: str = "en",
    languages: tuple[str, ...] = ("en",),
    script_code: str = "Latn",
) -> CanonicalBlock:
    return CanonicalBlock(
        block_id=block_id,
        page_number=1,
        reading_order=order,
        kind=kind,
        text=text,
        bounding_box=BoundingBox(
            left=10,
            bottom=700 - order * 20,
            right=500,
            top=715 - order * 20,
        ),
        heading_level=heading_level,
        table_rows=2 if kind is BlockKind.TABLE else None,
        table_columns=2 if kind is BlockKind.TABLE else None,
        table_has_header=True if kind is BlockKind.TABLE else None,
        ocr_confidence=None,
        source_element_id=block_id,
        language_code=language_code,
        languages=languages,
        script_code=script_code,
    )


def canonical(blocks: tuple[CanonicalBlock, ...]) -> CanonicalDocument:
    metrics = QualityMetrics(
        page_count=1,
        page_coverage=1,
        unexpected_blank_page_ratio=0,
        bounding_box_coverage=1,
        heading_count=sum(item.kind is BlockKind.HEADING for item in blocks),
        clause_or_proviso_count=0,
        table_count=sum(item.kind is BlockKind.TABLE for item in blocks),
        invalid_table_count=0,
        reading_order_error_count=0,
        heading_hierarchy_error_count=0,
        exact_numeric_token_count=0,
        missing_numeric_token_count=0,
        clause_boundary_count=0,
        missing_clause_boundary_count=0,
        formula_symbol_count=0,
        missing_formula_symbol_count=0,
        ocr_block_count=0,
        mean_ocr_confidence=None,
        devanagari_character_count=sum(
            "\u0900" <= character <= "\u097f"
            for item in blocks
            for character in item.text
        ),
        prompt_injection_indicator_count=0,
    )
    return CanonicalDocument(
        schema_version="1.0",
        canonical_document_id="document-1",
        document_version_id="version-1",
        document_version_number=1,
        original_checksum_sha256="a" * 64,
        parser="controlled",
        parser_version="1-test",
        parser_mode="deterministic",
        parsed_at=datetime(2026, 7, 29, tzinfo=UTC),
        pages=(
            CanonicalPage(
                page_number=1,
                width=612,
                height=792,
                language_code="mixed",
                languages=("en", "und-Deva"),
                script_code="Latn+Deva",
                blocks=blocks,
            ),
        ),
        quality=QualityReport(
            passed=True,
            review_required=False,
            metrics=metrics,
            issues=(),
        ),
    )
