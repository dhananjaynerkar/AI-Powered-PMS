from __future__ import annotations

from pathlib import Path

import pytest
from pms_common.migration_safety import validate_migration_source
from pms_common.settings import Settings
from pms_retrieval.repository import _vector_literal

ROOT = Path(__file__).parents[2]
MIGRATION = (
    ROOT
    / "db"
    / "migrations"
    / "versions"
    / "20260729_0006_secure_chunks_and_vectors.py"
)
REPOSITORY = (
    ROOT / "services" / "rag" / "src" / "pms_retrieval" / "repository.py"
)


def test_phase07_migration_is_exact_vector_only_and_application_scoped() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    validate_migration_source(source)
    assert 'down_revision: str | None = "20260729_0005"' in source
    assert "vector(1024)" in source
    assert "WITH SCHEMA pms_vector" in source
    assert "TSVECTOR" in source
    assert 'postgresql_using="gin"' in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "chunk_acl" in source
    assert "HNSW" not in source.upper()
    assert "pms_extract_2010_2023" not in source
    assert "public." not in source


def test_retrieval_repository_is_acl_first_exact_and_non_destructive() -> None:
    source = REPOSITORY.read_text(encoding="utf-8")

    assert "JOIN pms_vector.chunk_acl AS acl" in source
    assert "chunk.effective_from <= :as_of_date" in source
    assert "chunk.effective_to > :as_of_date" in source
    assert "chunk.review_status = 'accepted'" in source
    assert "record.status = 'indexed'" in source
    assert "embedding.embedding OPERATOR(pms_vector.<=>)" in source
    assert "websearch_to_tsquery('simple'" in source
    assert "DELETE FROM" not in source.upper()
    assert "SET active = false" in source


def test_phase07_settings_are_typed_and_bounded() -> None:
    configured = Settings(_env_file=ROOT / ".env.example")

    assert configured.chunk_strategy == "structure_aware_parent_child"
    assert configured.chunk_target_tokens == 420
    assert configured.chunk_max_tokens == 700
    assert configured.chunk_overlap_tokens == 60
    assert configured.parent_chunk_max_tokens == 1600
    assert configured.embedding_model == "BAAI/bge-m3"
    assert configured.embedding_dimension == 1024
    assert configured.vector_index_mode == "exact"
    assert configured.embedding_max_retries == 2


def test_invalid_chunk_limits_fail_fast() -> None:
    with pytest.raises(ValueError, match="CHUNK_TARGET_TOKENS"):
        Settings(
            _env_file=None,
            chunk_target_tokens=800,
            chunk_max_tokens=700,
        )


def test_phase08_settings_are_typed_bounded_and_fail_closed() -> None:
    configured = Settings(_env_file=ROOT / ".env.example")

    assert configured.rrf_k == 60
    assert configured.rerank_input_top_k == 30
    assert configured.rerank_output_top_k == 8
    assert configured.final_context_max_chunks == 8
    assert configured.final_context_max_tokens == 5000
    assert configured.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert configured.llm_primary_model == "qwen3.5:4b"
    assert configured.llm_fallback_model == "qwen3.5:9b"
    assert not configured.llm_thinking_enabled
    assert configured.llm_citation_validation_enabled
    assert configured.llm_refuse_without_evidence

    with pytest.raises(ValueError, match="RERANK_OUTPUT_TOP_K"):
        Settings(
            _env_file=None,
            rerank_input_top_k=4,
            rerank_output_top_k=5,
        )
    with pytest.raises(ValueError, match="LLM_THINKING_ENABLED"):
        Settings(_env_file=None, llm_thinking_enabled=True)


def test_vector_literal_is_parameter_value_and_rejects_nonfinite_data() -> None:
    assert _vector_literal((0.5, -1.25)) == "[0.5,-1.25]"

    with pytest.raises(ValueError, match="finite"):
        _vector_literal((float("nan"),))
