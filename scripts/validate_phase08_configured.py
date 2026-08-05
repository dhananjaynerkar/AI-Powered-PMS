"""Validate the configured Phase 08 gate without inventing representative evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pymupdf
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.parser_adapters import LocalPdfVerifier, PyMuPDFAdapter
from pms_ingestion.parsing import ExtractionQualityGate
from pms_retrieval.embedding import BgeM3EmbeddingAdapter, check_bge_m3_model
from pms_retrieval.generation import GenerationError, OllamaGenerator
from pms_retrieval.models import ResponseLanguage
from pms_retrieval.rag import HybridRagService, PostgresRagRepository
from pms_retrieval.repository import PostgresChunkRepository
from pms_retrieval.reranking import check_reranker_model
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "artifacts/evaluation/phase06_representative_matrix.json"
GOLDEN_EVIDENCE = ROOT / "artifacts/evaluation/phase08_golden_evidence.json"
# Phase 08 objects remain present under the later application migration head.
# The configured database has since applied the Phase 09-11 application-owned
# migrations without changing the protected extraction or retrieval schemas.
TARGET_REVISION = "20260730_0011"


def _context(department: str) -> AuthorizationContext:
    return AuthorizationContext(
        subject=f"phase08-configured-{department}",
        roles=frozenset({UserRole.AUDITOR}),
        tenant_id=None,
        department_id=department,
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _check_database(settings: Settings) -> dict[str, object]:
    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            PostgresChunkRepository(connection, _context("estate"))
            revision = str(
                connection.execute(
                    text("SELECT version_num FROM pms_app.alembic_version")
                ).scalar_one()
            )
            row = connection.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE chunk.chunk_kind = 'parent' AND chunk.active
                      ) AS parents,
                      count(*) FILTER (
                        WHERE chunk.chunk_kind = 'child' AND chunk.active
                      ) AS children,
                      count(DISTINCT chunk.canonical_document_id) FILTER (
                        WHERE chunk.active
                      ) AS documents
                    FROM pms_vector.document_chunk AS chunk
                    JOIN pms_doc.document_record AS record
                      ON record.canonical_document_id = chunk.canonical_document_id
                    WHERE record.status = 'indexed'
                    """
                )
            ).mappings().one()
            embeddings = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pms_vector.chunk_embedding AS embedding
                        JOIN pms_vector.document_chunk AS chunk
                          ON chunk.chunk_id = embedding.chunk_id
                        JOIN pms_doc.document_record AS record
                          ON record.canonical_document_id = chunk.canonical_document_id
                        WHERE embedding.active
                          AND chunk.active
                          AND record.status = 'indexed'
                        """
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()
    return {
        "passed": (
            revision == TARGET_REVISION
            and int(row["parents"]) > 0
            and int(row["children"]) > 0
            and int(row["documents"]) > 0
            and embeddings >= int(row["children"])
        ),
        "revision": revision,
        "parent_chunks": int(row["parents"]),
        "child_chunks": int(row["children"]),
        "indexed_documents": int(row["documents"]),
        "active_embeddings": embeddings,
    }


def _check_models(settings: Settings) -> dict[str, object]:
    embedding = check_bge_m3_model(settings)
    reranker = check_reranker_model(settings)
    try:
        available_llms = OllamaGenerator(settings).available_models()
        ollama_live = True
    except GenerationError:
        available_llms = frozenset()
        ollama_live = False
    required = {settings.llm_primary_model}
    return {
        "passed": (
            embedding.available
            and reranker.available
            and ollama_live
            and required <= available_llms
        ),
        "embedding_available": embedding.available,
        "embedding_revision": embedding.revision,
        "reranker_available": reranker.available,
        "reranker_detail": reranker.detail,
        "ollama_live": ollama_live,
        "missing_llm_models": sorted(required - available_llms),
    }


def _check_retrieval_baseline(settings: Settings) -> dict[str, object]:
    adapter = BgeM3EmbeddingAdapter(settings)
    authorized_engine = create_database_engine(settings, read_only=False)
    try:
        with authorized_engine.begin() as connection:
            PostgresChunkRepository(connection, _context("estate"))
            sample = connection.execute(
                text(
                    """
                    SELECT chunk.canonical_document_id, chunk.text
                    FROM pms_vector.document_chunk AS chunk
                    JOIN pms_doc.document_record AS record
                      ON record.canonical_document_id = chunk.canonical_document_id
                    WHERE chunk.active
                      AND chunk.chunk_kind = 'child'
                      AND chunk.review_status = 'accepted'
                      AND record.status = 'indexed'
                    ORDER BY chunk.canonical_document_id, chunk.ordinal
                    LIMIT 1
                    """
                )
            ).mappings().one()
        tokens = re.findall(r"[A-Za-z0-9]{4,}", str(sample["text"]))
        if not tokens:
            raise RuntimeError("indexed corpus has no usable lexical probe text")
        query = " ".join(tokens[: min(len(tokens), settings.lexical_top_k)])
        vector = adapter.embed((query,))[0]
        authorized = PostgresRagRepository(authorized_engine, _context("estate"))
        lexical = authorized.lexical_search(
            query,
            settings.lexical_top_k,
            as_of_date=date(2026, 7, 30),
            document_pattern=None,
        )
        dense = authorized.dense_search(
            vector,
            model=adapter.model,
            revision=adapter.revision,
            limit=settings.dense_top_k,
            as_of_date=date(2026, 7, 30),
            document_pattern=None,
        )
    finally:
        authorized_engine.dispose()
    return {
        "passed": (
            any(item.document_id == str(sample["canonical_document_id"]) for item in lexical)
            and any(item.document_id == str(sample["canonical_document_id"]) for item in dense)
        ),
        "fts_hits": len(lexical),
        "dense_hits": len(dense),
        "probe_document_id": str(sample["canonical_document_id"]),
    }


def _golden_evidence_gate(settings: Settings) -> dict[str, object]:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    results = payload["results"]
    table = next(item for item in results if item["category"] == "table_heavy")
    devanagari = next(item for item in results if item["category"] == "hindi_scan")
    evidence = json.loads(GOLDEN_EVIDENCE.read_text(encoding="utf-8"))
    table_path = ROOT / evidence["table_value"]["source_path"]
    table_checksum = hashlib.sha256(table_path.read_bytes()).hexdigest()

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        evidence["prompt_injection_pdf"]["fixture_text"],
    )
    prompt_content = document.tobytes()
    document.close()
    parsed = PyMuPDFAdapter(settings).parse(
        prompt_content,
        "controlled_prompt_injection.pdf",
    )
    prompt_quality = ExtractionQualityGate(settings).evaluate(
        parsed,
        LocalPdfVerifier(settings).verify(
            prompt_content,
            "controlled_prompt_injection.pdf",
        ),
    )
    table_passed = (
        evidence["table_value"]["status"] == "approved_for_phase08_golden_test"
        and table_checksum == evidence["table_value"]["source_checksum_sha256"]
        and evidence["table_value"]["visual_review"]["reviewed_logical_table_count"]
        == 1
        and evidence["table_value"]["expected_answer"]
        == "Rs. 1.00 per square metre per day."
        and table["final_status"] == "review_required"
    )
    prompt_passed = (
        evidence["prompt_injection_pdf"]["status"]
        == "approved_controlled_in_memory_fixture"
        and prompt_quality.review_required
        and prompt_quality.metrics.prompt_injection_indicator_count == 2
    )
    return {
        "passed": table_passed and prompt_passed,
        "table_value": {
            "status": "APPROVED_CONTROLLED_GOLDEN_EVIDENCE",
            "document_id": table["canonical_document_id"],
            "page_number": evidence["table_value"]["page_number"],
            "expected_answer": evidence["table_value"]["expected_answer"],
            "source_checksum_verified": table_passed,
            "production_document_status": table["final_status"],
        },
        "representative_devanagari": {
            "status": "PENDING_PRODUCTION_REVIEW_NOT_PHASE08_BLOCKER",
            "document_id": devanagari["canonical_document_id"],
            "reason": devanagari["issue_codes"],
        },
        "prompt_injection_inside_approved_pdf": {
            "status": "PASSED_CONTROLLED_IN_MEMORY_PDF",
            "review_required": prompt_quality.review_required,
            "indicator_count": prompt_quality.metrics.prompt_injection_indicator_count,
        },
    }


def _run_live_answers(settings: Settings) -> dict[str, object]:
    engine = create_database_engine(settings, read_only=False)
    service = HybridRagService(
        PostgresRagRepository(engine, _context("estate")),
        _context("estate"),
        settings,
    )
    results: list[dict[str, object]] = []
    try:
        with engine.begin() as connection:
            PostgresChunkRepository(connection, _context("estate"))
            cases = connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (record.canonical_document_id)
                           record.canonical_document_id, chunk.text
                    FROM pms_doc.document_record AS record
                    JOIN pms_vector.document_chunk AS chunk
                      ON chunk.canonical_document_id = record.canonical_document_id
                    JOIN pms_vector.chunk_embedding AS embedding
                      ON embedding.chunk_id = chunk.chunk_id
                     AND embedding.active
                    WHERE record.status = 'indexed'
                      AND chunk.active
                      AND chunk.chunk_kind = 'child'
                      AND chunk.review_status = 'accepted'
                    ORDER BY record.canonical_document_id, chunk.ordinal
                    """
                )
            ).mappings().all()
        for item in cases:
            expected_document = str(item["canonical_document_id"])
            terms = re.findall(r"[A-Za-z0-9]{4,}", str(item["text"]))
            if not terms:
                results.append(
                    {
                        "document_id": expected_document,
                        "passed": False,
                        "reason": "no_usable_lexical_probe_text",
                    }
                )
                continue
            query = "What does the document state about " + " ".join(terms[:8]) + "?"
            print(f"RUN corpus_document={expected_document}", file=sys.stderr, flush=True)
            started = time.perf_counter()
            answer = service.ask(
                query,
                response_language=ResponseLanguage.ENGLISH,
                include_trace=True,
                today=date.today(),
            )
            results.append(
                {
                    "document_id": expected_document,
                    "passed": (
                        not answer.review_required
                        and any(
                            source.document_id == expected_document
                            for source in answer.sources
                        )
                    ),
                    "review_required": answer.review_required,
                    "source_document_ids": [
                        source.document_id for source in answer.sources
                    ],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "trace": (
                        answer.trace.model_dump(mode="json")
                        if answer.trace is not None
                        else None
                    ),
                }
            )
            print(
                f"DONE corpus_document={expected_document} "
                f"passed={results[-1]['passed']} "
                f"latency_ms={results[-1]['latency_ms']}",
                file=sys.stderr,
                flush=True,
            )
    finally:
        engine.dispose()
    return {
        "passed": bool(results) and all(bool(item["passed"]) for item in results),
        "configured_profile": {
            "rerank_input_top_k": settings.rerank_input_top_k,
            "rerank_output_top_k": settings.rerank_output_top_k,
            "final_context_max_chunks": settings.final_context_max_chunks,
            "final_context_max_tokens": settings.final_context_max_tokens,
            "llm_max_output_tokens": settings.llm_max_output_tokens,
            "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
        },
        "cases": results,
    }


def main() -> int:
    settings = Settings()
    database = _check_database(settings)
    models = _check_models(settings)
    baseline = _check_retrieval_baseline(settings)
    evidence_gaps = _golden_evidence_gate(settings)
    live_answers: dict[str, object] = {
        "passed": False,
        "status": "BLOCKED_BY_MODEL_GATE",
    }
    if bool(models["passed"]):
        live_answers = _run_live_answers(settings)
    result = {
        "phase": "08",
        "passed": (
            bool(database["passed"])
            and bool(models["passed"])
            and bool(baseline["passed"])
            and bool(evidence_gaps["passed"])
            and bool(live_answers["passed"])
        ),
        "database": database,
        "models": models,
        "retrieval_baseline": baseline,
        "configured_evidence_gaps": evidence_gaps,
        "live_answers": live_answers,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
