"""Build one five-PDF corpus report from persisted PostgreSQL and MinIO state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import ObjectKind
from pms_ingestion.parsing import CanonicalDocument
from pms_ingestion.parsing_service import PIPELINE_PRODUCER, PIPELINE_VERSION
from pms_ingestion.storage import MinioObjectStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INBOX = PROJECT_ROOT / "data" / "inbox"
INVENTORY = PROJECT_ROOT / "artifacts/evaluation/phase10_pdf_rule_evidence.json"
CHECKPOINT = PROJECT_ROOT / "artifacts/evaluation/text_rich_pdf_indexing.json"
REPORTS = PROJECT_ROOT / "docs/phase_reports"
BATCH_SIZE = 5
PREEXISTING_PHASE06_PATHS = {
    "acts/Indian Easement Act 1882.pdf",
    "circulars/Bylaw No. 9.pdf",
}


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="bulk-text-rich-corpus-ingestion",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _physical_candidates() -> list[dict[str, Any]]:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return [
        item
        for item in payload["documents"]
        if item["extraction_status"] == "embedded_text_available"
        and item["source_path"] not in PREEXISTING_PHASE06_PATHS
    ]


def _checkpoint_by_checksum() -> dict[str, dict[str, Any]]:
    if not CHECKPOINT.is_file():
        return {}
    payload = json.loads(CHECKPOINT.read_text(encoding="utf-8-sig"))
    return {item["checksum_sha256"]: item for item in payload["results"]}


def _database_rows(settings: Settings) -> dict[str, dict[str, Any]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    query = """
        SELECT v.checksum_sha256, r.canonical_document_id::text, r.status,
               r.title, r.original_filename, v.size_bytes,
               COALESCE(c.parent_chunks, 0), COALESCE(c.child_chunks, 0),
               COALESCE(e.embedding_count, 0), e.dimension,
               COALESCE(e.embedding_storage_bytes, 0)
        FROM pms_doc.document_record AS r
        JOIN pms_doc.document_version AS v
          ON v.canonical_document_id = r.canonical_document_id
         AND v.version_number = r.current_version
        LEFT JOIN LATERAL (
          SELECT count(*) FILTER (WHERE chunk_kind = 'parent' AND active)::int
                   AS parent_chunks,
                 count(*) FILTER (WHERE chunk_kind = 'child' AND active)::int
                   AS child_chunks
          FROM pms_vector.document_chunk
          WHERE canonical_document_id = r.canonical_document_id
        ) AS c ON true
        LEFT JOIN LATERAL (
          SELECT count(*)::int AS embedding_count,
                 max(embedding.dimension)::int AS dimension,
                 sum(pg_column_size(embedding.embedding))::bigint
                   AS embedding_storage_bytes
          FROM pms_vector.chunk_embedding AS embedding
          JOIN pms_vector.document_chunk AS chunk
            ON chunk.chunk_id = embedding.chunk_id
          WHERE chunk.canonical_document_id = r.canonical_document_id
            AND embedding.active
        ) AS e ON true
    """
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    ) as connection, connection.cursor() as cursor:
        cursor.execute(query)
        fields = (
            "checksum_sha256",
            "document_id",
            "status",
            "title",
            "original_filename",
            "source_size_bytes",
            "parent_chunks",
            "child_chunks",
            "embedding_count",
            "dimension",
            "embedding_storage_bytes",
        )
        return {
            str(row[0]): dict(zip(fields, row, strict=True))
            for row in cursor.fetchall()
        }


def _canonical_rows(
    settings: Settings,
    database_rows: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    engine = create_database_engine(settings, read_only=False)
    values: dict[str, dict[str, Any]] = {}
    try:
        with engine.begin() as connection:
            service = create_document_service(
                connection,
                _context(),
                settings,
                object_store=MinioObjectStore(settings),
            )
            for checksum, row in database_rows.items():
                artifact = service.retrieve_derived(
                    document_id=str(row["document_id"]),
                    object_kind=ObjectKind.CANONICAL_JSON,
                    producer=PIPELINE_PRODUCER,
                    producer_version=PIPELINE_VERSION,
                )
                if artifact is None:
                    continue
                canonical = CanonicalDocument.model_validate_json(artifact.content)
                metrics = canonical.quality.metrics
                values[checksum] = {
                    "canonical_json_size_bytes": len(artifact.content),
                    "canonical_pages": len(canonical.pages),
                    "parser": canonical.parser,
                    "parser_version": canonical.parser_version,
                    "table_count": metrics.table_count,
                    "invalid_table_count": metrics.invalid_table_count,
                    "clause_count": metrics.clause_or_proviso_count,
                    "missing_clause_count": metrics.missing_clause_boundary_count,
                    "missing_numeric_count": metrics.missing_numeric_token_count,
                    "bbox_coverage": metrics.bounding_box_coverage,
                    "quality_passed": canonical.quality.passed,
                    "review_required": canonical.quality.review_required,
                    "quality_issues": [issue.code for issue in canonical.quality.issues],
                }
    finally:
        engine.dispose()
    return values


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,} B"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-number", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.batch_number < 1:
        raise SystemExit("--batch-number must be at least 1")
    candidates = _physical_candidates()
    start = (arguments.batch_number - 1) * BATCH_SIZE
    selected = candidates[start : start + BATCH_SIZE]
    if not selected:
        raise SystemExit("batch number is outside the 77-PDF corpus")

    settings = Settings()
    checkpoint = _checkpoint_by_checksum()
    database = _database_rows(settings)
    canonicals = _canonical_rows(settings, database)
    rows: list[dict[str, Any]] = []
    for candidate in selected:
        checksum = str(candidate["sha256"])
        source = INBOX / str(candidate["source_path"])
        row = {
            **candidate,
            "actual_source_size_bytes": source.stat().st_size,
            **database.get(checksum, {}),
            **canonicals.get(checksum, {}),
        }
        checkpoint_row = checkpoint.get(checksum, {})
        row["outcome"] = checkpoint_row.get("outcome", "pending")
        # A rejected OpenDataLoader run must not be represented by an older
        # canonical artifact produced before the parser policy changed.
        # Preserve that immutable artifact in storage, but keep this report
        # faithful to the current production attempt.
        if row["outcome"] == "review_required" and checkpoint_row.get("parser") in {
            None,
            "none",
        }:
            for key in (
                "canonical_json_size_bytes",
                "canonical_pages",
                "parser_version",
                "table_count",
                "invalid_table_count",
                "clause_count",
                "missing_clause_count",
                "bbox_coverage",
                "quality_passed",
                "review_required",
                "parent_chunks",
                "child_chunks",
                "embedding_count",
                "dimension",
                "embedding_storage_bytes",
            ):
                row.pop(key, None)
            row["parser"] = "opendataloader (rejected)"
        row["issue_codes"] = (
            row.get("quality_issues")
            or checkpoint_row.get("quality_issue_codes")
            or []
        )
        row["rag_searchable"] = (
            row.get("status") == "indexed"
            and int(row.get("child_chunks", 0)) > 0
            and int(row.get("embedding_count", 0))
            == int(row.get("child_chunks", 0))
        )
        rows.append(row)

    terminal = all(row.get("status") in {"indexed", "review_required"} for row in rows)
    indexed = sum(bool(row["rag_searchable"]) for row in rows)
    review = sum(row.get("status") == "review_required" for row in rows)
    lines = [
        f"# PDF Corpus Batch {arguments.batch_number:02d}",
        "",
        f"- **Status:** {'Complete' if terminal else 'In progress'}",
        f"- **Physical PDFs:** {len(rows)}",
        f"- **RAG searchable:** {indexed}",
        f"- **Review required:** {review}",
        "- **Security scope:** `restricted`, department `estate`, unit `land`",
        "- **Embedding model:** `BAAI/bge-m3`, 1,024 dimensions",
        "",
        "## Persisted results",
        "",
        "| PDF | Pages | Source size | Status | Canonical JSON | Parser | Tables | "
        "Clauses | Parent/child | Embeddings | Vector bytes | RAG | Issues |",
        "|---|---:|---:|---|---:|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        tables = (
            f"{row.get('table_count', '-')}/invalid {row.get('invalid_table_count', '-')}"
        )
        clauses = (
            f"{row.get('clause_count', '-')}/missing {row.get('missing_clause_count', '-')}"
        )
        chunks = f"{row.get('parent_chunks', 0)}/{row.get('child_chunks', 0)}"
        embeddings = (
            f"{row.get('embedding_count', 0)} x {row.get('dimension') or '-'}"
        )
        issues = ", ".join(row["issue_codes"]) or "-"
        lines.append(
            "| `{path}` | {pages} | {source_size} | `{status}` | {canonical} | "
            "{parser} | {tables} | {clauses} | {chunks} | {embeddings} | "
            "{vector_size} | {rag} | {issues} |".format(
                path=row["source_path"],
                pages=row["page_count"],
                source_size=_format_bytes(row["actual_source_size_bytes"]),
                status=row.get("status", "not_registered"),
                canonical=_format_bytes(row.get("canonical_json_size_bytes")),
                parser=row.get("parser", "-"),
                tables=tables,
                clauses=clauses,
                chunks=chunks,
                embeddings=embeddings,
                vector_size=_format_bytes(row.get("embedding_storage_bytes")),
                rag="YES" if row["rag_searchable"] else "NO",
                issues=issues,
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "A `review_required` row is intentionally absent from RAG. Table-count, "
            "clause-boundary, numeric-token, citation-coordinate or OCR failures are "
            "not overridden. Reranking is query-time and stores no per-PDF payload.",
            "The current batch attempt used OpenDataLoader only; preserved historical "
            "fallback artifacts are not counted as current output.",
            "",
            "Visual page annotations are added only after the terminal batch is "
            "rendered and inspected.",
            "",
        ]
    )
    output = REPORTS / f"PDF_CORPUS_BATCH_{arguments.batch_number:02d}.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "batch_number": arguments.batch_number,
                "status": "complete" if terminal else "in_progress",
                "rag_searchable": indexed,
                "review_required": review,
                "report": str(output.relative_to(PROJECT_ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
