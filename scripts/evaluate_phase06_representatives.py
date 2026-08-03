"""Evaluate the four user-authorized Phase 06 representative PDFs locally."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import ObjectKind
from pms_ingestion.parser_adapters import (
    DoclingAdapter,
    LocalPdfVerifier,
    OpenDataLoaderAdapter,
    PaddleOCRAdapter,
    PyMuPDFAdapter,
)
from pms_ingestion.parsing import (
    CanonicalDocument,
    ExtractionQualityGate,
    ParserAdapter,
    ParserOutput,
    ParsingEngine,
)
from pms_ingestion.parsing_service import (
    PIPELINE_PRODUCER,
    PIPELINE_VERSION,
    DocumentParsingCoordinator,
)
from pms_ingestion.storage import MinioObjectStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "artifacts/evaluation/phase06_representative_matrix.json"


@dataclass(frozen=True, slots=True)
class Representative:
    category: str
    path: Path
    checksum_sha256: str
    parsers: tuple[str, ...]
    visual_page: int
    visual_annotation: str
    require_devanagari: bool = False
    minimum_tables: int = 0


REPRESENTATIVES = (
    Representative(
        category="digital_english",
        path=PROJECT_ROOT / "data/inbox/acts/Indian Easement Act 1882.pdf",
        checksum_sha256=(
            "1b2a1b27e1c3646cb7fe123cb9f052cea0950096c1932cad6036ff705492f140"
        ),
        parsers=("opendataloader", "docling", "pymupdf"),
        visual_page=1,
        visual_annotation=(
            "Digital title, arrangement of sections and page number are sharp and legible."
        ),
    ),
    Representative(
        category="scanned_english",
        path=(
            PROJECT_ROOT
            / "data/inbox/circulars/Clarification No 2 of 2019_1048.pdf"
        ),
        checksum_sha256=(
            "e169d3715d5f4612be95e7c275583e73fb86390a12d6a03931d155236beca558"
        ),
        parsers=("docling",),
        visual_page=1,
        visual_annotation=(
            "Scanned English circular has visible scan noise but legible heading, "
            "paragraphs, date and signature."
        ),
    ),
    Representative(
        category="hindi_scan",
        path=PROJECT_ROOT / "data/inbox/policies/MPA Act rules.pdf",
        checksum_sha256=(
            "cc62e58b55e35f065033f6eec9bdc39f07b8215b49e3895e85ee345f13651430"
        ),
        parsers=("paddleocr",),
        visual_page=1,
        visual_annotation=(
            "Scanned Gazette page visibly contains Hindi Devanagari and English text."
        ),
        require_devanagari=True,
    ),
    Representative(
        category="table_heavy",
        path=PROJECT_ROOT / "data/inbox/circulars/Bylaw No. 9.pdf",
        checksum_sha256=(
            "c5e1297053590d393e892cbe79d9262f29ac8cd455a9f82d8ce6e374f83cbccd"
        ),
        parsers=("opendataloader", "docling", "pymupdf"),
        visual_page=3,
        visual_annotation=(
            "Page 3 visibly contains a ruled eight-column schedule of storage rates "
            "with row labels, decimals and notes."
        ),
        minimum_tables=1,
    ),
)


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="phase06-representative-evaluator",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _adapters(
    specification: Representative,
    settings: Settings,
) -> tuple[ParserAdapter, ...]:
    available: dict[str, ParserAdapter] = {
        "opendataloader": OpenDataLoaderAdapter(settings),
        "docling": DoclingAdapter(settings),
        "paddleocr": PaddleOCRAdapter(settings),
        "pymupdf": PyMuPDFAdapter(settings),
    }
    if "paddleocr" in specification.parsers:
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    return tuple(available[name] for name in specification.parsers)


def _verify_source(specification: Representative) -> bytes:
    if not specification.path.is_file():
        raise RuntimeError(f"representative PDF is missing: {specification.path}")
    content = specification.path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != specification.checksum_sha256:
        raise RuntimeError(
            f"representative checksum changed for {specification.category}"
        )
    return content


def _parser_version(parser: str) -> str:
    distribution = {
        "opendataloader": "opendataloader-pdf",
        "docling": "docling",
        "paddleocr": "paddleocr",
        "pymupdf": "pymupdf",
    }[parser]
    return importlib.metadata.version(distribution)


def _evaluate_one(
    specification: Representative,
    settings: Settings,
) -> dict[str, object]:
    content = _verify_source(specification)
    context = _context()
    database = create_database_engine(settings, read_only=False)
    object_store = MinioObjectStore(settings)
    started = time.monotonic()
    try:
        with database.begin() as connection:
            service = create_document_service(
                connection,
                context,
                settings,
                object_store=object_store,
            )
            uploaded = service.upload(
                title=f"Phase 06 representative: {specification.category}",
                filename=specification.path.name,
                mime_type="application/pdf",
                content=content,
                classification=Classification.INTERNAL,
            )

        parser = ParsingEngine(
            settings,
            _adapters(specification, settings),
            LocalPdfVerifier(settings),
        )
        coordinator = DocumentParsingCoordinator(
            database,
            context,
            settings,
            parsing_engine=parser,
            object_store=object_store,
        )
        first = coordinator.parse(uploaded.document.canonical_document_id)
        second = (
            coordinator.parse(uploaded.document.canonical_document_id)
            if first.canonical_object_key is not None
            else None
        )

        canonical: CanonicalDocument | None = None
        raw_candidate: ParserOutput | None = None
        raw_candidates: list[ParserOutput] = []
        with database.begin() as connection:
            service = create_document_service(
                connection,
                context,
                settings,
                object_store=object_store,
            )
            retrieved = service.retrieve_derived(
                document_id=uploaded.document.canonical_document_id,
                object_kind=ObjectKind.CANONICAL_JSON,
                producer=PIPELINE_PRODUCER,
                producer_version=PIPELINE_VERSION,
            )
            if retrieved is not None:
                canonical = CanonicalDocument.model_validate_json(retrieved.content)
            if canonical is None:
                for producer in specification.parsers:
                    raw = service.retrieve_derived(
                        document_id=uploaded.document.canonical_document_id,
                        object_kind=ObjectKind.RAW_PARSER,
                        producer=producer,
                        producer_version=_parser_version(producer),
                    )
                    if raw is not None:
                        raw_candidates.append(
                            ParserOutput.model_validate_json(raw.content)
                        )

        block_count = 0
        devanagari_count = 0
        table_count = 0
        metrics: dict[str, object] = {}
        candidate_quality = None
        if canonical is not None:
            canonical_blocks = [
                block for page in canonical.pages for block in page.blocks
            ]
            block_count = len(canonical_blocks)
            devanagari_count = sum(
                "\u0900" <= character <= "\u097f"
                for block in canonical_blocks
                for character in block.text
            )
            table_count = canonical.quality.metrics.table_count
            metrics = canonical.quality.metrics.model_dump(mode="json")
        elif raw_candidates:
            verification = LocalPdfVerifier(settings).verify(
                content,
                specification.path.name,
            )
            evaluated_candidates = [
                (
                    candidate,
                    ExtractionQualityGate(settings).evaluate(
                        candidate,
                        verification,
                    ),
                )
                for candidate in raw_candidates
            ]
            raw_candidate, candidate_quality = max(
                evaluated_candidates,
                key=lambda item: (
                    int(
                        item[1].metrics.table_count
                        >= specification.minimum_tables
                    ),
                    int(
                        not specification.require_devanagari
                        or item[1].metrics.devanagari_character_count > 0
                    ),
                    int(item[1].passed),
                    -len(item[1].issues),
                    sum(len(page.blocks) for page in item[0].pages),
                ),
            )
            candidate_blocks = [
                block for page in raw_candidate.pages for block in page.blocks
            ]
            block_count = len(candidate_blocks)
            devanagari_count = sum(
                "\u0900" <= character <= "\u097f"
                for block in candidate_blocks
                for character in block.text
            )
            table_count = candidate_quality.metrics.table_count
            metrics = candidate_quality.metrics.model_dump(mode="json")

        acceptance_checks = {
            "parser": (
                first.parser in specification.parsers
                or (
                    raw_candidate is not None
                    and raw_candidate.parser in specification.parsers
                )
            ),
            "quality_gate": first.quality_passed and not first.review_required,
            "canonical_json": canonical is not None,
            "blocks": block_count > 0,
            "idempotency": second is not None
            and second.idempotent
            and second.canonical_object_key == first.canonical_object_key,
            "devanagari": (
                not specification.require_devanagari or devanagari_count > 0
            ),
            "tables": table_count >= specification.minimum_tables,
        }
        return {
            "category": specification.category,
            "source_path": str(specification.path.relative_to(PROJECT_ROOT)),
            "checksum_sha256": specification.checksum_sha256,
            "size_bytes": len(content),
            "canonical_document_id": uploaded.document.canonical_document_id,
            "duplicate_upload": uploaded.duplicate,
            "parser": first.parser,
            "candidate_parser": (
                raw_candidate.parser if raw_candidate is not None else None
            ),
            "parser_mode": first.parser_mode,
            "fallback_used": first.fallback_used,
            "page_count": first.page_count,
            "block_count": block_count,
            "devanagari_character_count": devanagari_count,
            "table_count": table_count,
            "quality_gate_passed": first.quality_passed,
            "review_required": first.review_required,
            "issue_codes": list(first.issue_codes),
            "final_status": first.final_status,
            "canonical_json_object_key": first.canonical_object_key,
            "raw_parser_object_keys": list(first.raw_object_keys),
            "idempotent_repeat": second.idempotent if second is not None else None,
            "visual_page_checked": specification.visual_page,
            "visual_annotation": specification.visual_annotation,
            "quality_metrics": metrics,
            "acceptance_checks": acceptance_checks,
            "accepted": all(acceptance_checks.values()),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        database.dispose()


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--category",
        choices=[item.category for item in REPRESENTATIVES],
    )
    return parser.parse_args(argv)


def _existing_results() -> dict[str, dict[str, object]]:
    if not OUTPUT_PATH.is_file():
        return {}
    payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        return {}
    return {
        str(result["category"]): result
        for result in raw_results
        if isinstance(result, dict) and result.get("category") is not None
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    settings = Settings()
    selected = [
        item
        for item in REPRESENTATIVES
        if arguments.category is None or item.category == arguments.category
    ]
    results: list[dict[str, object]] = []
    stored_results = _existing_results()
    for specification in selected:
        try:
            result = _evaluate_one(specification, settings)
        except Exception as error:
            result = {
                "category": specification.category,
                "source_path": str(specification.path.relative_to(PROJECT_ROOT)),
                "checksum_sha256": specification.checksum_sha256,
                "accepted": False,
                "error_type": type(error).__name__,
                "error": str(error),
            }
        results.append(result)
        stored_results[specification.category] = result
        print(json.dumps(result, sort_keys=True), flush=True)

    ordered_results = [
        stored_results[item.category]
        for item in REPRESENTATIVES
        if item.category in stored_results
    ]
    payload = {
        "schema_version": "1.0",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "authorization": (
            "User directed completion of the remaining Phase 06 gate on 2026-07-29; "
            "local processing only."
        ),
        "configured_database_revision": "20260729_0005",
        "results": ordered_results,
        "passed": len(ordered_results) == len(REPRESENTATIVES)
        and all(bool(result.get("accepted")) for result in ordered_results),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"evaluation_report={OUTPUT_PATH}", flush=True)
    return 0 if all(bool(result.get("accepted")) for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
