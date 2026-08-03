from __future__ import annotations

import pytest
from pms_ingestion.parsing import BlockKind
from pms_retrieval.chunking import ChunkingReviewRequired, StructureAwareChunker
from pms_retrieval.models import ChunkKind

from tests.retrieval.support import WordTokenizer, block, canonical, context, settings


def _fixture():
    english = " ".join(f"lease{i}" for i in range(24))
    return canonical(
        (
            block(
                "heading",
                "Chapter I",
                order=0,
                kind=BlockKind.HEADING,
                heading_level=1,
            ),
            block("clause", f"Section 4 {english}", order=1),
            block(
                "proviso",
                "Provided that भूमि अधिकार मराठी नोंद remains part of the clause.",
                order=2,
                language_code="mul",
                languages=("und-Deva", "en"),
                script_code="Deva+Latn",
            ),
            block(
                "table",
                "Rate Amount\nAnnual 18%\nMonthly 2%",
                order=3,
                kind=BlockKind.TABLE,
            ),
        )
    )


def test_parent_child_chunks_have_stable_hashes_limits_acl_and_citations() -> None:
    chunker = StructureAwareChunker(settings(), WordTokenizer())

    first = chunker.chunk(_fixture(), context(), classification="internal")
    second = chunker.chunk(_fixture(), context(), classification="internal")

    assert first == second
    assert any(item.chunk_kind is ChunkKind.PARENT for item in first)
    assert any(item.chunk_kind is ChunkKind.CHILD for item in first)
    assert all(
        item.token_count <= settings().chunk_max_tokens
        for item in first
        if item.chunk_kind is ChunkKind.CHILD
    )
    assert all(
        item.token_count <= settings().parent_chunk_max_tokens
        for item in first
        if item.chunk_kind is ChunkKind.PARENT
    )
    assert all(item.citations for item in first)
    assert all(item.page_numbers == (1,) for item in first)
    assert all(item.heading_path == ("Chapter I",) for item in first)
    assert all(item.department_id == "estate" for item in first)
    assert all(item.port_id == "port-1" for item in first)


def test_clause_and_proviso_stay_together_with_mixed_language_metadata() -> None:
    chunks = StructureAwareChunker(settings(), WordTokenizer()).chunk(
        _fixture(),
        context(),
        classification="internal",
    )

    child = next(item for item in chunks if "Provided that" in item.text)

    assert "Section 4" in child.text
    assert child.language_code == "mixed"
    assert child.languages == ("en", "und-Deva")
    assert child.script_code == "Latn+Deva"
    assert child.section_number == "4"


def test_table_text_header_dimensions_and_citation_are_not_split() -> None:
    chunks = StructureAwareChunker(settings(), WordTokenizer()).chunk(
        _fixture(),
        context(),
        classification="internal",
    )

    table_chunk = next(item for item in chunks if "Rate Amount" in item.text)

    assert "Annual 18%" in table_chunk.text
    assert "Monthly 2%" in table_chunk.text
    assert any(citation.block_id == "table" for citation in table_chunk.citations)


def test_oversized_formula_is_routed_to_review_instead_of_split() -> None:
    formula = block(
        "formula",
        " ".join(f"variable{i}" for i in range(80)),
        order=0,
        kind=BlockKind.FORMULA,
    )

    with pytest.raises(ChunkingReviewRequired, match="formula exceeds"):
        StructureAwareChunker(settings(), WordTokenizer()).chunk(
            canonical((formula,)),
            context(),
            classification="internal",
        )


def test_quality_failed_canonical_document_is_excluded() -> None:
    source = _fixture().model_copy(
        update={
            "quality": _fixture().quality.model_copy(
                update={"passed": False, "review_required": True}
            )
        }
    )

    with pytest.raises(ChunkingReviewRequired, match="quality-passed"):
        StructureAwareChunker(settings(), WordTokenizer()).chunk(
            source,
            context(),
            classification="internal",
        )
