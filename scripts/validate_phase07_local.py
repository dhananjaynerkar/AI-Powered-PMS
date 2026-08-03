"""Run the Phase 07 local model and canonical-chunk gate without database writes."""

from __future__ import annotations

import json
import math
from pathlib import Path

from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import ObjectKind
from pms_ingestion.parsing import CanonicalDocument
from pms_ingestion.parsing_service import PIPELINE_PRODUCER, PIPELINE_VERSION
from pms_ingestion.storage import MinioObjectStore
from pms_retrieval.chunking import StructureAwareChunker
from pms_retrieval.embedding import BgeM3EmbeddingAdapter, BgeM3Tokenizer
from pms_retrieval.models import ChunkKind

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX = PROJECT_ROOT / "artifacts/evaluation/phase06_representative_matrix.json"


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="phase07-local-validator",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _authorized_document_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("Phase 06 representative matrix is invalid")
    canonicalized = tuple(
        str(item["canonical_document_id"])
        for item in results
        if isinstance(item, dict) and item.get("final_status") == "canonicalized"
    )
    review_required = tuple(
        str(item["canonical_document_id"])
        for item in results
        if isinstance(item, dict) and item.get("final_status") == "review_required"
    )
    if len(canonicalized) != 2 or len(review_required) != 2:
        raise RuntimeError("expected two canonicalized and two review-required documents")
    return canonicalized, review_required


def _validate_chunks(settings: Settings) -> None:
    canonicalized, review_required = _authorized_document_ids()
    engine = create_database_engine(settings, read_only=False)
    store = MinioObjectStore(settings)
    tokenizer = BgeM3Tokenizer(settings)
    chunker = StructureAwareChunker(settings, tokenizer)
    try:
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                _context(),
                settings,
                object_store=store,
            )
            for document_id in review_required:
                metadata = service.metadata(document_id)
                if metadata.status != "review_required":
                    raise RuntimeError("review-required Phase 06 document status changed")
            for document_id in canonicalized:
                artifact = service.retrieve_derived(
                    document_id=document_id,
                    object_kind=ObjectKind.CANONICAL_JSON,
                    producer=PIPELINE_PRODUCER,
                    producer_version=PIPELINE_VERSION,
                )
                if artifact is None:
                    raise RuntimeError("canonical Phase 06 artifact is missing")
                canonical = CanonicalDocument.model_validate_json(artifact.content)
                first = chunker.chunk(
                    canonical,
                    _context(),
                    classification=artifact.document.classification.value,
                )
                second = chunker.chunk(
                    canonical,
                    _context(),
                    classification=artifact.document.classification.value,
                )
                if first != second:
                    raise RuntimeError("chunk hashes or identifiers are unstable")
                children = tuple(
                    chunk for chunk in first if chunk.chunk_kind is ChunkKind.CHILD
                )
                parents = tuple(
                    chunk for chunk in first if chunk.chunk_kind is ChunkKind.PARENT
                )
                if not children or not parents:
                    raise RuntimeError("parent/child chunks were not both created")
                if max(chunk.token_count for chunk in children) > settings.chunk_max_tokens:
                    raise RuntimeError("child token limit was exceeded")
                if (
                    max(chunk.token_count for chunk in parents)
                    > settings.parent_chunk_max_tokens
                ):
                    raise RuntimeError("parent token limit was exceeded")
                if any(not chunk.citations for chunk in first):
                    raise RuntimeError("chunk citation provenance is missing")
                print(
                    "PASS "
                    f"document={document_id} parents={len(parents)} "
                    f"children={len(children)} "
                    f"max_parent_tokens={max(chunk.token_count for chunk in parents)} "
                    f"max_child_tokens={max(chunk.token_count for chunk in children)}"
                )
    finally:
        engine.dispose()
    print("PASS review_required_documents_excluded=2")


def _similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _validate_multilingual_embeddings(settings: Settings) -> None:
    adapter = BgeM3EmbeddingAdapter(settings)
    texts = (
        "When does lease rent increase?",
        "पट्टा किराया कब बढ़ता है?",
        "भाडेपट्ट्याचे भाडे कधी वाढते?",
        "पट्टा किराया समझौते के अनुसार हर पांच वर्ष में बढ़ेगा।",
        "The lease rent increases every five years under the agreement.",
        "Cargo handling equipment requires monthly inspection.",
        "माल हाताळणी उपकरणांची दर महिन्याला तपासणी करावी.",
        "कार्गो उपकरण का मासिक निरीक्षण आवश्यक है।",
    )
    vectors = adapter.embed(texts)
    if any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise RuntimeError("BGE-M3 dimension gate failed")
    if any(
        not math.isclose(
            sum(value * value for value in vector),
            1,
            abs_tol=1e-5,
        )
        for vector in vectors
    ):
        raise RuntimeError("BGE-M3 normalization gate failed")

    cases = (
        (0, (3, 6), 3, "english_to_hindi"),
        (1, (4, 6), 4, "hindi_to_english"),
        (2, (4, 7), 4, "marathi_to_english"),
    )
    for query_index, candidate_indices, expected, label in cases:
        best = max(
            candidate_indices,
            key=lambda index: _similarity(vectors[query_index], vectors[index]),
        )
        if best != expected:
            raise RuntimeError(f"cross-language retrieval failed: {label}")
        print(
            f"PASS {label} score={_similarity(vectors[query_index], vectors[best]):.6f}"
        )
    print(
        "PASS "
        f"embedding_model={adapter.model} revision={adapter.revision} "
        f"version={adapter.embedding_version} dimension={adapter.dimension}"
    )


def main() -> int:
    settings = Settings()
    _validate_chunks(settings)
    _validate_multilingual_embeddings(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
