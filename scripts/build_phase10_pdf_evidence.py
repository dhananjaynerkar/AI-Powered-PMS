"""Build and optionally load exact, unapproved Phase 10 PDF evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz
import psycopg
from pms_common.settings import Settings
from pms_rule_engine.evidence import (
    PageEvidence,
    discover_page_evidence,
    eligible_source,
    normalize_text,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INBOX = PROJECT_ROOT / "data/inbox"
OUTPUT = PROJECT_ROOT / "artifacts/evaluation/phase10_pdf_rule_evidence.json"
TARGET_REVISION = "20260730_0009"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _scan() -> dict[str, Any]:
    pdfs = sorted(
        (path for path in INBOX.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: str(path).lower(),
    )
    entries: list[PageEvidence] = []
    documents: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    total_pages = 0
    text_pages = 0
    for path in pdfs:
        relative = path.relative_to(INBOX).as_posix()
        checksum = _sha256(path)
        try:
            document = fitz.open(path)
            page_count = len(document)
            total_pages += page_count
            document_text_pages = 0
            if eligible_source(path, INBOX):
                for index, page in enumerate(document):
                    page_text = page.get_text("text") or ""
                    if len(normalize_text(page_text)) >= 40:
                        document_text_pages += 1
                        entries.extend(
                            discover_page_evidence(
                                source_path=relative,
                                document_sha256=checksum,
                                page_number=index + 1,
                                page_text=page_text,
                            )
                        )
            else:
                document_text_pages = sum(
                    1
                    for page in document
                    if len(normalize_text(page.get_text("text") or "")) >= 40
                )
            text_pages += document_text_pages
            documents.append(
                {
                    "source_path": relative,
                    "sha256": checksum,
                    "page_count": page_count,
                    "text_pages": document_text_pages,
                    "candidate_source_group": eligible_source(path, INBOX),
                    "extraction_status": (
                        "embedded_text_available"
                        if document_text_pages
                        else "ocr_required"
                    ),
                }
            )
            document.close()
        except (OSError, RuntimeError, ValueError) as error:
            failures.append(
                {
                    "source_path": relative,
                    "error_type": type(error).__name__,
                    "detail": str(error),
                }
            )
    serialized_entries = [
        {
            "candidate_id": item.candidate_id,
            "candidate_family": item.candidate_family,
            "source_path": item.source_path,
            "document_sha256": item.document_sha256,
            "page_number": item.page_number,
            "excerpt": item.excerpt,
            "excerpt_sha256": item.excerpt_sha256,
            "exact_tokens": list(item.exact_tokens),
            "matched_terms": list(item.matched_terms),
            "candidate_status": "unapproved",
            "evidence_status": "linked_unverified",
            "interpretation": None,
        }
        for item in entries
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "All local PDFs audited; candidates limited to policy/rate/circular groups.",
        "normalization": "Whitespace normalized; wording and exact tokens preserved.",
        "authority_boundary": (
            "Evidence extraction is not Finance or Legal approval and creates "
            "no executable rule."
        ),
        "summary": {
            "pdf_count": len(pdfs),
            "parsed_pdf_count": len(pdfs) - len(failures),
            "failure_count": len(failures),
            "page_count": total_pages,
            "text_page_count": text_pages,
            "candidate_source_document_count": sum(
                1 for item in documents if item["candidate_source_group"]
            ),
            "evidence_candidate_count": len(serialized_entries),
            "ocr_required_candidate_documents": sum(
                1
                for item in documents
                if item["candidate_source_group"]
                and item["extraction_status"] == "ocr_required"
            ),
        },
        "documents": documents,
        "evidence_candidates": serialized_entries,
        "failures": failures,
    }


def _connect(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required to load candidates")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def _load(payload: dict[str, Any], settings: Settings) -> int:
    candidates = payload["evidence_candidates"]
    if not isinstance(candidates, list):
        raise RuntimeError("evidence candidate payload is invalid")
    loaded = 0
    with _connect(settings) as connection, connection.cursor() as cursor:
        revision = cursor.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
        if revision is None or str(revision[0]) != TARGET_REVISION:
            raise RuntimeError("configured database is not at the Phase 10 revision")
        for start in range(0, len(candidates), settings.rule_candidate_batch_size):
            batch = candidates[start : start + settings.rule_candidate_batch_size]
            for item in batch:
                cursor.execute(
                    """
                    INSERT INTO pms_rules.rule_candidate (
                      candidate_id, candidate_family, source_schema, source_table,
                      source_record_id, raw_payload, source_document_id,
                      source_clause, source_page, evidence_status,
                      candidate_status, imported_by_subject
                    ) VALUES (
                      %s, %s, 'local_pdf_evidence', 'phase10_page_excerpt',
                      %s, %s::jsonb, %s,
                      'page-level statement; interpretation pending review',
                      %s, 'linked', 'unapproved', 'phase10-pdf-evidence'
                    )
                    ON CONFLICT (source_schema, source_table, source_record_id)
                    DO NOTHING
                    """,
                    (
                        item["candidate_id"],
                        item["candidate_family"],
                        item["candidate_id"],
                        json.dumps(item, ensure_ascii=False, sort_keys=True),
                        f"sha256:{item['document_sha256']}",
                        item["page_number"],
                    ),
                )
                loaded += cursor.rowcount
        connection.commit()
        approved = cursor.execute(
            """
            SELECT count(*) FROM pms_rules.rule_definition
            WHERE review_status = 'approved'
            """
        ).fetchone()
        if approved is None or int(str(approved[0])) != 0:
            raise RuntimeError("PDF evidence load must not approve executable rules")
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--load",
        action="store_true",
        help="load extracted page evidence as unapproved PostgreSQL candidates",
    )
    args = parser.parse_args()
    payload = _scan()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"PASS pdf_count={payload['summary']['pdf_count']}")
    print(f"PASS parsed_pdf_count={payload['summary']['parsed_pdf_count']}")
    print(f"PASS failure_count={payload['summary']['failure_count']}")
    print(f"PASS page_count={payload['summary']['page_count']}")
    print(
        "PASS evidence_candidate_count="
        f"{payload['summary']['evidence_candidate_count']}"
    )
    print(
        "PASS ocr_required_candidate_documents="
        f"{payload['summary']['ocr_required_candidate_documents']}"
    )
    if args.load:
        print(f"PASS newly_loaded_unapproved_candidates={_load(payload, Settings())}")
    print(f"PASS artifact={OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
