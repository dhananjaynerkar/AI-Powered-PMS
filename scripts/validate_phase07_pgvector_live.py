"""Validate Phase 07 persistence, RLS, FTS and exact vectors in an isolated DB."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime

import psycopg
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_ingestion.parsing import BoundingBox
from pms_retrieval.models import (
    ChunkCitation,
    ChunkKind,
    DocumentChunk,
    EmbeddingWrite,
)
from pms_retrieval.repository import PostgresChunkRepository
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import URL, text

DOCUMENT_ID = "phase07-document"
VERSION_ID = "phase07-version"


def _admin_dsn() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _context(department: str) -> AuthorizationContext:
    return AuthorizationContext(
        subject=f"phase07-{department}",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id=department,
        unit_id="port-1",
        classification=Classification.RESTRICTED,
    )


def _create_runtime() -> tuple[str, str, str]:
    role = f"pms_phase07_role_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    with psycopg.connect(_admin_dsn(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(password),
                )
            )
            role_identifier = sql.Identifier(role)
            cursor.execute(
                sql.SQL(
                    "GRANT USAGE ON SCHEMA "
                    "pms_app, pms_audit, pms_doc, pms_vector TO {}"
                ).format(role_identifier)
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT ON pms_doc.document_record, "
                    "pms_doc.document_version, pms_doc.document_acl TO {}"
                ).format(role_identifier)
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON "
                    "pms_vector.document_chunk, pms_vector.chunk_embedding, "
                    "pms_vector.index_checkpoint, pms_vector.chunk_acl TO {}"
                ).format(role_identifier)
            )
            cursor.execute(
                sql.SQL("GRANT INSERT ON pms_audit.security_event TO {}").format(
                    role_identifier
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION "
                    "pms_app.has_role(text), "
                    "pms_app.classification_rank(text) TO {}"
                ).format(role_identifier)
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON TYPE pms_vector.vector TO {}").format(
                    role_identifier
                )
            )
        info = connection.info
        url = URL.create(
            "postgresql+psycopg",
            username=role,
            password=password,
            host=info.host,
            port=info.port,
            database=info.dbname,
        ).render_as_string(hide_password=False)
    return role, password, url


def _seed_document() -> None:
    now = datetime.now(UTC)
    with psycopg.connect(_admin_dsn()) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO pms_doc.document_acl (
              canonical_document_id, canonical_tenant_id, classification,
              allowed_roles, allowed_departments
            ) VALUES (%s, NULL, 'internal', '{}', '{estate}')
            """,
            (DOCUMENT_ID,),
        )
        cursor.execute(
            """
            INSERT INTO pms_doc.document_record (
              canonical_document_id, title, original_filename, status,
              current_version, created_by_subject, created_at, updated_at
            ) VALUES (%s, 'Phase 07 fixture', 'phase07.pdf', 'canonicalized',
                      1, 'phase07-admin', %s, %s)
            """,
            (DOCUMENT_ID, now, now),
        )
        cursor.execute(
            """
            INSERT INTO pms_doc.stored_object (
              object_id, bucket_name, object_key, object_version,
              checksum_sha256, size_bytes, mime_type, object_kind,
              retention_mode, retention_until, created_by_subject, created_at
            ) VALUES (
              'phase07-object', 'fixture', 'fixture/phase07.pdf', NULL,
              %s, 1, 'application/pdf', 'original', 'versioned', NULL,
              'phase07-admin', %s
            )
            """,
            ("a" * 64, now),
        )
        cursor.execute(
            """
            INSERT INTO pms_doc.document_version (
              version_id, canonical_document_id, version_number,
              original_object_id, checksum_sha256, size_bytes, mime_type,
              created_by_subject, created_at
            ) VALUES (
              %s, %s, 1, 'phase07-object', %s, 1, 'application/pdf',
              'phase07-admin', %s
            )
            """,
            (VERSION_ID, DOCUMENT_ID, "a" * 64, now),
        )
        connection.commit()


def _chunks() -> tuple[DocumentChunk, DocumentChunk]:
    citation = ChunkCitation(
        block_id="block-1",
        page_number=1,
        bounding_box=BoundingBox(left=10, bottom=10, right=100, top=30),
    )
    common = {
        "canonical_document_id": DOCUMENT_ID,
        "document_version_id": VERSION_ID,
        "text": "Section 4 lease rent increases every five years.",
        "token_count": 9,
        "content_hash": "b" * 64,
        "heading_path": ("Lease",),
        "page_numbers": (1,),
        "citations": (citation,),
        "section_number": "4",
        "clause_number": None,
        "language_code": "en",
        "languages": ("en",),
        "script_code": "Latn",
        "document_status": "canonicalized",
        "port_id": "port-1",
        "department_id": "estate",
        "security_classification": Classification.INTERNAL,
        "review_status": "accepted",
        "parser_name": "controlled",
        "parser_version": "1",
        "chunking_version": "1.0",
    }
    parent = DocumentChunk(
        chunk_id="parent-1",
        parent_chunk_id=None,
        chunk_kind=ChunkKind.PARENT,
        ordinal=0,
        **common,
    )
    child = DocumentChunk(
        chunk_id="child-1",
        parent_chunk_id=parent.chunk_id,
        chunk_kind=ChunkKind.CHILD,
        ordinal=0,
        **common,
    )
    return parent, child


def _runtime_settings(url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(url),
        rls_enabled=True,
    )


def main() -> int:
    _seed_document()
    role, _password, url = _create_runtime()
    runtime = _runtime_settings(url)
    own_engine = create_database_engine(runtime, read_only=False)
    try:
        with own_engine.begin() as connection:
            repository = PostgresChunkRepository(connection, _context("estate"))
            first = repository.replace_document_chunks(_chunks())
            second = repository.replace_document_chunks(_chunks())
            plan = repository.embedding_plan(
                DOCUMENT_ID,
                model="BAAI/bge-m3",
                revision="controlled-revision",
                embedding_version="controlled-version",
                dimension=1024,
            )
            if first.created != 2 or second.unchanged != 2:
                raise RuntimeError("stable chunk hashes did not skip the second write")
            if plan.pending_chunk_ids != ("child-1",):
                raise RuntimeError("new child chunk was not pending embedding")
            repository.store_embeddings(
                DOCUMENT_ID,
                (
                    EmbeddingWrite(
                        chunk_id="child-1",
                        content_hash="b" * 64,
                        vector=(1.0, *([0.0] * 1023)),
                    ),
                ),
                model="BAAI/bge-m3",
                revision="controlled-revision",
                embedding_version="controlled-version",
                dimension=1024,
            )
            repeated = repository.embedding_plan(
                DOCUMENT_ID,
                model="BAAI/bge-m3",
                revision="controlled-revision",
                embedding_version="controlled-version",
                dimension=1024,
            )
            lexical = repository.lexical_search("lease rent", 5)
            dense = repository.exact_vector_search(
                (1.0, *([0.0] * 1023)),
                model="BAAI/bge-m3",
                revision="controlled-revision",
                limit=5,
            )
            if repeated.unchanged_chunk_ids != ("child-1",):
                raise RuntimeError("unchanged embedding was not skipped")
            if not lexical or lexical[0].chunk_id != "child-1":
                raise RuntimeError("lexical retrieval failed")
            if not dense or dense[0].chunk_id != "child-1":
                raise RuntimeError("exact vector retrieval failed")
            if lexical[0].page_numbers != (1,) or not lexical[0].citations:
                raise RuntimeError("retrieval citation provenance was not preserved")
            checkpoint_id = repository.start_checkpoint(
                DOCUMENT_ID,
                VERSION_ID,
                stage="embed",
                chunking_version="1.0",
                embedding_model="BAAI/bge-m3",
                embedding_revision="controlled-revision",
                resume=False,
            )
            repository.finish_checkpoint(
                checkpoint_id,
                status="failed",
                last_chunk_ordinal=7,
                error_code="CONTROLLED_INTERRUPTION",
            )
            resumed_id = repository.start_checkpoint(
                DOCUMENT_ID,
                VERSION_ID,
                stage="embed",
                chunking_version="1.0",
                embedding_model="BAAI/bge-m3",
                embedding_revision="controlled-revision",
                resume=True,
            )
            checkpoint = connection.execute(
                text(
                    """
                    SELECT status, last_chunk_ordinal, error_code
                    FROM pms_vector.index_checkpoint
                    WHERE checkpoint_id = :checkpoint_id
                    """
                ),
                {"checkpoint_id": checkpoint_id},
            ).one()
            if resumed_id != checkpoint_id or tuple(checkpoint) != ("running", 7, None):
                raise RuntimeError("checkpoint resume did not preserve progress")
        denied_engine = create_database_engine(runtime, read_only=False)
        try:
            with denied_engine.begin() as connection:
                denied = PostgresChunkRepository(
                    connection,
                    _context("legal"),
                ).exact_vector_search(
                    (1.0, *([0.0] * 1023)),
                    model="BAAI/bge-m3",
                    revision="controlled-revision",
                    limit=5,
                )
                if denied:
                    raise RuntimeError("unauthorized vector chunks were returned")
        finally:
            denied_engine.dispose()
        with own_engine.begin() as connection:
            repository = PostgresChunkRepository(connection, _context("estate"))
            if repository.deactivate_document(DOCUMENT_ID) != 2:
                raise RuntimeError("document chunks were not deactivated")
            if repository.lexical_search("lease rent", 5):
                raise RuntimeError("deactivated chunks remained retrievable")
        with psycopg.connect(_admin_dsn()) as connection, connection.cursor() as cursor:
            revision = cursor.execute(
                "SELECT version_num FROM pms_app.alembic_version"
            ).fetchone()
            hnsw = cursor.execute(
                """
                SELECT count(*) FROM pg_indexes
                WHERE schemaname = 'pms_vector' AND indexdef ILIKE '%%hnsw%%'
                """
            ).fetchone()
            policies = cursor.execute(
                """
                SELECT count(*) FROM pg_policies
                WHERE schemaname = 'pms_vector'
                  AND tablename IN (
                    'document_chunk', 'chunk_embedding', 'index_checkpoint'
                  )
                """
            ).fetchone()
        if revision is None or revision[0] != "20260729_0006":
            raise RuntimeError("isolated database revision is not Phase 07")
        if hnsw is None or int(str(hnsw[0])) != 0:
            raise RuntimeError("HNSW was created without a benchmark")
        if policies is None or int(str(policies[0])) != 6:
            raise RuntimeError("Phase 07 RLS policies are incomplete")
        print("PASS phase07_migration_revision=20260729_0006")
        print("PASS phase07_parent_child_stable_hashes")
        print("PASS phase07_embedding_dimension=1024")
        print("PASS phase07_unchanged_embedding_skip")
        print("PASS phase07_fts_and_exact_vector_retrieval")
        print("PASS phase07_page_bbox_citation_provenance")
        print("PASS phase07_checkpoint_resume")
        print("PASS phase07_unauthorized_chunk_exclusion")
        print("PASS phase07_document_deactivation")
        print("PASS phase07_hnsw_indexes=0")
        return 0
    finally:
        own_engine.dispose()
        with (
            psycopg.connect(_admin_dsn(), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role))
            )
            cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        print("PASS phase07_runtime_role_cleanup")


if __name__ == "__main__":
    raise SystemExit(main())
