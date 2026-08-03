"""Resumably index the audited embedded-text PDF corpus with existing services.

Only source files whose hash matches the supplied corpus inventory are selected.
Each exact duplicate is processed once. A document becomes searchable only after
the parser quality gate, protected-element chunking, and local BGE-M3 embedding
all succeed. This command never executes text found inside a document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import DocumentStatus
from pms_ingestion.parsing_service import DocumentParsingCoordinator
from pms_ingestion.service import DocumentServiceError
from pms_ingestion.storage import MinioObjectStore
from pms_retrieval.chunking import ChunkingReviewRequired
from pms_retrieval.embedding import BgeM3EmbeddingAdapter, BgeM3Tokenizer
from pms_retrieval.service import RetrievalCoordinator
from sqlalchemy import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INBOX = PROJECT_ROOT / "data" / "inbox"
INVENTORY = PROJECT_ROOT / "artifacts" / "evaluation" / "phase10_pdf_rule_evidence.json"
OUTPUT = PROJECT_ROOT / "artifacts" / "evaluation" / "text_rich_pdf_indexing.json"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A byte-verified embedded-text PDF selected by the corpus inventory."""

    relative_path: str
    checksum_sha256: str
    page_count: int


def _context() -> AuthorizationContext:
    """Keep the corpus in the restricted Estate scope used by Phase 06."""

    return AuthorizationContext(
        subject="bulk-text-rich-corpus-ingestion",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidates() -> tuple[Candidate, ...]:
    """Load embedded-text candidates and collapse byte-identical paths."""

    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise RuntimeError("corpus inventory has no documents list")
    selected: list[Candidate] = []
    seen_hashes: set[str] = set()
    for item in documents:
        if not isinstance(item, dict):
            raise RuntimeError("corpus inventory contains an invalid document entry")
        if item.get("extraction_status") != "embedded_text_available":
            continue
        relative_path = item.get("source_path")
        checksum = item.get("sha256")
        page_count = item.get("page_count")
        if not isinstance(relative_path, str) or not isinstance(checksum, str):
            raise RuntimeError("corpus candidate lacks path or checksum")
        if not isinstance(page_count, int):
            raise RuntimeError("corpus candidate lacks page count")
        if checksum in seen_hashes:
            continue
        seen_hashes.add(checksum)
        selected.append(Candidate(relative_path, checksum, page_count))
    return tuple(selected)


def _load_results() -> dict[str, dict[str, Any]]:
    if not OUTPUT.is_file():
        return {}
    payload = json.loads(OUTPUT.read_text(encoding="utf-8-sig"))
    records = payload.get("results", [])
    if not isinstance(records, list):
        raise RuntimeError("bulk indexing checkpoint has an invalid results list")
    return {
        str(record["checksum_sha256"]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("checksum_sha256"), str)
    }


def _write_results(results: Iterable[dict[str, Any]]) -> None:
    records = sorted(results, key=lambda item: str(item["relative_path"]).lower())
    outcome_counts: dict[str, int] = {}
    for record in records:
        outcome = str(record["outcome"])
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "Unique embedded-text PDF checksums from the existing corpus inventory.",
        "security_scope": {
            "classification": Classification.RESTRICTED.value,
            "department_id": "estate",
            "unit_id": "land",
        },
        "quality_boundary": (
            "Only parser-quality-passed and protected-element-safe documents are "
            "indexed. Review-required documents retain evidence but no RAG chunks "
            "or embeddings."
        ),
        "summary": {
            "unique_text_rich_candidates": len(records),
            "outcomes": outcome_counts,
        },
        "results": records,
    }
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(OUTPUT)


def _transition_to_review(
    engine: Engine,
    context: AuthorizationContext,
    settings: Settings,
    store: MinioObjectStore,
    document_id: str,
) -> None:
    with engine.begin() as connection:
        service = create_document_service(
            connection,
            context,
            settings,
            object_store=store,
        )
        service.transition_status(document_id, DocumentStatus.REVIEW_REQUIRED)


def _process_one(
    candidate: Candidate,
    *,
    engine: Engine,
    context: AuthorizationContext,
    settings: Settings,
    store: MinioObjectStore,
    tokenizer: BgeM3Tokenizer,
    embedder: BgeM3EmbeddingAdapter,
    retry_review_document_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    started = time.monotonic()
    path = INBOX / candidate.relative_path
    result: dict[str, Any] = {
        **asdict(candidate),
        "canonical_document_id": None,
        "parser": None,
        "parser_mode": None,
        "quality_issue_codes": [],
        "parent_chunks": 0,
        "child_chunks": 0,
        "embeddings_created": 0,
        "outcome": "failed",
    }
    try:
        if not path.is_file():
            result["outcome"] = "source_missing"
            return result
        if _sha256(path) != candidate.checksum_sha256:
            result["outcome"] = "source_checksum_mismatch"
            return result
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                context,
                settings,
                object_store=store,
            )
            uploaded = service.upload(
                title=f"Inbox PDF: {candidate.relative_path}",
                filename=path.name,
                mime_type="application/pdf",
                content=path.read_bytes(),
                classification=Classification.RESTRICTED,
            )
        metadata = uploaded.document
        result["canonical_document_id"] = metadata.canonical_document_id
        if (
            uploaded.duplicate
            and metadata.status == DocumentStatus.REVIEW_REQUIRED.value
            and metadata.canonical_document_id not in retry_review_document_ids
        ):
            result["outcome"] = "existing_review_required"
            return result
        if uploaded.duplicate and metadata.status == DocumentStatus.INDEXED.value:
            result["outcome"] = "already_indexed"
            return result

        parsed = DocumentParsingCoordinator(
            engine,
            context,
            settings,
            object_store=store,
        ).parse(
            metadata.canonical_document_id,
            force=metadata.canonical_document_id in retry_review_document_ids,
        )
        result["parser"] = parsed.parser
        result["parser_mode"] = parsed.parser_mode
        result["quality_issue_codes"] = list(parsed.issue_codes)
        if not parsed.quality_passed or parsed.review_required:
            result["outcome"] = "review_required"
            return result

        coordinator = RetrievalCoordinator(
            engine,
            context,
            settings,
            object_store=store,
        )
        try:
            chunked = coordinator.chunk(
                metadata.canonical_document_id,
                tokenizer=tokenizer,
                resume=True,
            )
        except ChunkingReviewRequired as error:
            _transition_to_review(
                engine,
                context,
                settings,
                store,
                metadata.canonical_document_id,
            )
            result["quality_issue_codes"] = ["CHUNKING_REVIEW_REQUIRED"]
            result["detail"] = str(error)
            result["outcome"] = "review_required"
            return result
        result["parent_chunks"] = chunked.summary.parent_chunks
        result["child_chunks"] = chunked.summary.child_chunks
        embedded = coordinator.embed(
            metadata.canonical_document_id,
            dry_run=False,
            adapter=embedder,
            resume=True,
        )
        result["embeddings_created"] = (
            embedded.write_summary.created if embedded.write_summary else 0
        )
        result["outcome"] = "indexed"
        return result
    except (DocumentServiceError, RuntimeError, ValueError) as error:
        result["detail"] = f"{type(error).__name__}: {error}"
        return result
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-documents",
        type=int,
        default=5,
        help="Bound this invocation; repeat the same command to resume.",
    )
    parser.add_argument(
        "--retry-review-document-id",
        action="append",
        default=[],
        help=(
            "Retry one explicitly named review-required document after a known "
            "interruption. Repeat the option for additional document IDs."
        ),
    )
    parser.add_argument(
        "--source-path",
        action="append",
        default=[],
        help="Process only this inventory-relative PDF path; repeat for a batch.",
    )
    arguments = parser.parse_args()
    if arguments.max_documents < 1 or arguments.max_documents > 100:
        raise SystemExit("--max-documents must be between 1 and 100")
    candidates = _candidates()
    if arguments.source_path:
        requested_paths = frozenset(arguments.source_path)
        candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.relative_path in requested_paths
        )
        missing_paths = requested_paths - {
            candidate.relative_path for candidate in candidates
        }
        if missing_paths:
            raise SystemExit(
                "source path is not an embedded-text inventory candidate: "
                + ", ".join(sorted(missing_paths))
            )
    prior = _load_results()
    retry_review_document_ids = frozenset(arguments.retry_review_document_id)
    remaining = [
        item
        for item in candidates
        if item.checksum_sha256 not in prior
        or prior[item.checksum_sha256].get("canonical_document_id")
        in retry_review_document_ids
    ]
    selected = remaining[: arguments.max_documents]
    if not selected:
        print(json.dumps({"status": "PASS", "processed": 0, "remaining": 0}))
        return 0
    settings = Settings()
    context = _context()
    engine = create_database_engine(settings, read_only=False)
    store = MinioObjectStore(settings)
    try:
        tokenizer = BgeM3Tokenizer(settings)
        embedder = BgeM3EmbeddingAdapter(settings)
        for candidate in selected:
            record = _process_one(
                candidate,
                engine=engine,
                context=context,
                settings=settings,
                store=store,
                tokenizer=tokenizer,
                embedder=embedder,
                retry_review_document_ids=retry_review_document_ids,
            )
            prior[candidate.checksum_sha256] = record
            _write_results(prior.values())
            print(
                json.dumps(
                    {
                        "path": candidate.relative_path,
                        "outcome": record["outcome"],
                        "document_id": record["canonical_document_id"],
                        "elapsed_seconds": record["elapsed_seconds"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "status": "PASS",
                "processed": len(selected),
                "remaining": len(remaining) - len(selected),
                "checkpoint": str(OUTPUT.relative_to(PROJECT_ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
