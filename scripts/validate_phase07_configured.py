"""Persist and validate the two accepted Phase 06 documents on configured services."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.storage import MinioObjectStore
from pms_retrieval.embedding import BgeM3EmbeddingAdapter, BgeM3Tokenizer
from pms_retrieval.service import RetrievalCoordinator
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX = PROJECT_ROOT / "artifacts/evaluation/phase06_representative_matrix.json"
TARGET_REVISION = "20260729_0006"
EXPECTED_PARENT_CHUNKS = 33
EXPECTED_CHILD_CHUNKS = 77


def _connect(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for validation")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def _revision(connection: psycopg.Connection[tuple[object, ...]]) -> str:
    with connection.cursor() as cursor:
        row = cursor.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
    if row is None:
        raise RuntimeError("configured database has no Alembic revision")
    return str(row[0])


def _pgvector_available(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[bool, str | None, str | None]:
    with connection.cursor() as cursor:
        available = cursor.execute(
            """
            SELECT default_version
            FROM pg_available_extensions
            WHERE name = 'vector'
            """
        ).fetchone()
        installed = cursor.execute(
            """
            SELECT n.nspname, e.extversion
            FROM pg_extension AS e
            JOIN pg_namespace AS n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
            """
        ).fetchone()
    if available is None:
        return False, None, None
    if installed is None:
        return True, None, str(available[0])
    return True, str(installed[0]), str(installed[1])


def _context(department_id: str) -> AuthorizationContext:
    return AuthorizationContext(
        subject=f"phase07-configured-validator-{department_id}",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id=department_id,
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _document_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("Phase 06 representative matrix is invalid")
    accepted = tuple(
        str(item["canonical_document_id"])
        for item in results
        if isinstance(item, dict) and item.get("final_status") == "canonicalized"
    )
    excluded = tuple(
        str(item["canonical_document_id"])
        for item in results
        if isinstance(item, dict) and item.get("final_status") == "review_required"
    )
    if len(accepted) != 2 or len(excluded) != 2:
        raise RuntimeError("expected exactly two accepted and two excluded documents")
    return accepted, excluded


def _database_gate(settings: Settings) -> None:
    with _connect(settings) as connection:
        revision = _revision(connection)
        available, installed_schema, version = _pgvector_available(connection)
        with connection.cursor() as cursor:
            role = cursor.execute(
                """
                SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication,
                       rolbypassrls
                FROM pg_roles
                WHERE rolname = 'pms_app_runtime'
                """
            ).fetchone()
    if revision != TARGET_REVISION:
        raise RuntimeError(f"configured revision is {revision}, not {TARGET_REVISION}")
    if not available or installed_schema != "pms_vector":
        raise RuntimeError("pgvector is not installed in pms_vector")
    if role != (False, False, False, False, False):
        raise RuntimeError("pms_app_runtime is absent or elevated")
    print(
        "PASS "
        f"configured_revision={revision} pgvector_version={version} "
        "pgvector_schema=pms_vector runtime_role=least_privilege"
    )


def _validate_indexing(
    settings: Settings,
    accepted: tuple[str, ...],
) -> tuple[BgeM3EmbeddingAdapter, tuple[tuple[float, ...], ...]]:
    engine = create_database_engine(settings, read_only=False)
    context = _context("estate")
    store = MinioObjectStore(settings)
    tokenizer = BgeM3Tokenizer(settings)
    adapter = BgeM3EmbeddingAdapter(settings)
    coordinator = RetrievalCoordinator(
        engine,
        context,
        settings,
        object_store=store,
    )
    try:
        with engine.begin() as connection:
            current_user = str(connection.execute(text("SELECT current_user")).scalar_one())
        if current_user != "pms_app_runtime":
            raise RuntimeError(f"configured runtime user is {current_user}")

        for document_id in accepted:
            first = coordinator.chunk(document_id, tokenizer=tokenizer)
            second = coordinator.chunk(document_id, tokenizer=tokenizer)
            total = first.summary.parent_chunks + first.summary.child_chunks
            if (
                second.summary.created != 0
                or second.summary.unchanged != total
                or second.summary.deactivated != 0
            ):
                raise RuntimeError(f"chunk idempotency failed for {document_id}")
            dry_run = coordinator.embed(document_id, dry_run=True, adapter=adapter)
            if dry_run.plan.pending_chunk_ids:
                written = coordinator.embed(document_id, dry_run=False, adapter=adapter)
                if written.write_summary is None:
                    raise RuntimeError(f"embedding write summary missing for {document_id}")
            repeated = coordinator.embed(document_id, dry_run=True, adapter=adapter)
            if repeated.plan.pending_chunk_ids:
                raise RuntimeError(f"unchanged embeddings were not skipped for {document_id}")
            if len(repeated.plan.unchanged_chunk_ids) != first.summary.child_chunks:
                raise RuntimeError(f"embedding count mismatch for {document_id}")
            if repeated.status != "indexed":
                raise RuntimeError(f"document {document_id} is not indexed")
            print(
                "PASS "
                f"document={document_id} parents={first.summary.parent_chunks} "
                f"children={first.summary.child_chunks} "
                f"chunks_created={first.summary.created} "
                f"chunks_unchanged_second_run={second.summary.unchanged} "
                f"embeddings_unchanged={len(repeated.plan.unchanged_chunk_ids)} "
                "status=indexed"
            )

        query_vectors = adapter.embed(
            (
                "What rights does an easement give the dominant owner?",
                "What clarification applies to lease policy at major ports?",
            )
        )
        return adapter, query_vectors
    finally:
        engine.dispose()


def _require_target_hit(
    hits: tuple[object, ...],
    document_id: str,
    *,
    label: str,
) -> None:
    target = next(
        (
            hit
            for hit in hits
            if getattr(hit, "document_id", None) == document_id
        ),
        None,
    )
    if target is None:
        raise RuntimeError(f"{label} did not retrieve {document_id}")
    if not getattr(target, "page_numbers", ()) or not getattr(target, "citations", ()):
        raise RuntimeError(f"{label} citation provenance is incomplete")


def _validate_retrieval(
    settings: Settings,
    accepted: tuple[str, ...],
    adapter: BgeM3EmbeddingAdapter,
    query_vectors: tuple[tuple[float, ...], ...],
) -> None:
    engine = create_database_engine(settings, read_only=False)
    authorized = RetrievalCoordinator(engine, _context("estate"), settings)
    denied = RetrievalCoordinator(engine, _context("legal"), settings)
    try:
        lexical_cases = (
            ("easement", accepted[0]),
            ("clarification", accepted[1]),
        )
        for query, document_id in lexical_cases:
            hits = authorized.lexical_search(query, 10)
            _require_target_hit(hits, document_id, label=f"FTS query {query!r}")
            print(
                f"PASS fts_query={query} target_document={document_id} hits={len(hits)}"
            )

        for vector, document_id in zip(query_vectors, accepted, strict=True):
            hits = authorized.exact_vector_search(
                vector,
                model=adapter.model,
                revision=adapter.revision,
                limit=10,
            )
            _require_target_hit(hits, document_id, label="exact vector query")
            print(
                "PASS "
                f"exact_vector_target={document_id} hits={len(hits)} "
                "citation_page_bbox=present"
            )

        if denied.lexical_search("easement clarification lease", 10):
            raise RuntimeError("unauthorized FTS search returned chunks")
        for vector in query_vectors:
            if denied.exact_vector_search(
                vector,
                model=adapter.model,
                revision=adapter.revision,
                limit=10,
            ):
                raise RuntimeError("unauthorized vector search returned chunks")
        print("PASS unauthorized_department_fts_and_vector_hits=0")
    finally:
        engine.dispose()


def _persistence_gate(
    settings: Settings,
    accepted: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    with _connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            SELECT
              count(*) FILTER (WHERE chunk_kind = 'parent'),
              count(*) FILTER (WHERE chunk_kind = 'child'),
              max(token_count) FILTER (WHERE chunk_kind = 'parent'),
              max(token_count) FILTER (WHERE chunk_kind = 'child'),
              count(*) FILTER (
                WHERE cardinality(page_numbers) = 0
                   OR jsonb_array_length(bounding_boxes) = 0
              )
            FROM pms_vector.document_chunk
            WHERE active
              AND canonical_document_id = ANY(%s)
            """,
            (list(accepted),),
        ).fetchone()
        acl_count = cursor.execute(
            """
            SELECT count(*)
            FROM pms_vector.document_chunk AS chunk
            JOIN pms_vector.chunk_acl AS acl ON acl.chunk_id = chunk.chunk_id
            WHERE chunk.active
              AND chunk.canonical_document_id = ANY(%s)
            """,
            (list(accepted),),
        ).fetchone()
        embedding = cursor.execute(
            """
            SELECT count(*), min(dimension), max(dimension),
                   min(pms_vector.vector_dims(embedding)),
                   max(pms_vector.vector_dims(embedding))
            FROM pms_vector.chunk_embedding AS embedding
            JOIN pms_vector.document_chunk AS chunk
              ON chunk.chunk_id = embedding.chunk_id
            WHERE embedding.active
              AND chunk.active
              AND chunk.canonical_document_id = ANY(%s)
            """,
            (list(accepted),),
        ).fetchone()
        excluded_count = cursor.execute(
            """
            SELECT count(*)
            FROM pms_vector.document_chunk
            WHERE canonical_document_id = ANY(%s)
            """,
            (list(excluded),),
        ).fetchone()
        statuses = cursor.execute(
            """
            SELECT canonical_document_id, status
            FROM pms_doc.document_record
            WHERE canonical_document_id = ANY(%s)
            ORDER BY canonical_document_id
            """,
            (list(accepted),),
        ).fetchall()
        hnsw = cursor.execute(
            """
            SELECT count(*)
            FROM pg_indexes
            WHERE schemaname = 'pms_vector' AND indexdef ILIKE '%%hnsw%%'
            """
        ).fetchone()

    if row is None or tuple(row[:2]) != (
        EXPECTED_PARENT_CHUNKS,
        EXPECTED_CHILD_CHUNKS,
    ):
        raise RuntimeError(f"unexpected persisted chunk counts: {row}")
    if int(row[2]) > settings.parent_chunk_max_tokens:
        raise RuntimeError("persisted parent token maximum was exceeded")
    if int(row[3]) > settings.chunk_max_tokens:
        raise RuntimeError("persisted child token maximum was exceeded")
    if int(row[4]) != 0:
        raise RuntimeError("persisted citation page/bounding-box metadata is missing")
    if acl_count is None or int(acl_count[0]) != sum(row[:2]):
        raise RuntimeError("not every active chunk has a persisted ACL")
    if embedding is None or tuple(embedding) != (77, 1024, 1024, 1024, 1024):
        raise RuntimeError(f"unexpected embedding persistence result: {embedding}")
    if excluded_count is None or int(excluded_count[0]) != 0:
        raise RuntimeError("review-required documents were persisted")
    if any(status != "indexed" for _, status in statuses) or len(statuses) != 2:
        raise RuntimeError(f"accepted document statuses are invalid: {statuses}")
    if hnsw is None or int(hnsw[0]) != 0:
        raise RuntimeError("HNSW was created without a benchmark")
    print(
        "PASS "
        f"persisted_parent_chunks={row[0]} persisted_child_chunks={row[1]} "
        f"chunk_acl_rows={acl_count[0]} active_embeddings={embedding[0]} "
        "vector_dimension=1024"
    )
    print("PASS review_required_document_chunks=0")
    print("PASS hnsw_indexes=0")


def main() -> int:
    settings = Settings()
    accepted, excluded = _document_ids()
    _database_gate(settings)
    adapter, query_vectors = _validate_indexing(settings, accepted)
    _validate_retrieval(settings, accepted, adapter, query_vectors)
    _persistence_gate(settings, accepted, excluded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
