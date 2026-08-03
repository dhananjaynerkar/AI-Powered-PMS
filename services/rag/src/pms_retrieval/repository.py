"""Authorization-scoped PostgreSQL persistence for chunks and exact vectors."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import uuid4

from pms_common.security import (
    AuthorizationContext,
    Classification,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from sqlalchemy import Connection, bindparam, text

from pms_retrieval.models import (
    ChunkCitation,
    ChunkKind,
    ChunkWriteSummary,
    DocumentChunk,
    EmbeddingPlan,
    EmbeddingWrite,
    EmbeddingWriteSummary,
    RetrievalHit,
)


class ChunkRepository(Protocol):
    def replace_document_chunks(
        self,
        chunks: Sequence[DocumentChunk],
    ) -> ChunkWriteSummary: ...

    def embedding_plan(
        self,
        document_id: str,
        *,
        model: str,
        revision: str,
        embedding_version: str,
        dimension: int,
    ) -> EmbeddingPlan: ...

    def store_embeddings(
        self,
        document_id: str,
        writes: Sequence[EmbeddingWrite],
        *,
        model: str,
        revision: str,
        embedding_version: str,
        dimension: int,
    ) -> EmbeddingWriteSummary: ...

    def child_chunks(self, document_id: str) -> tuple[DocumentChunk, ...]: ...

    def lexical_search(
        self,
        query: str,
        limit: int,
        *,
        as_of_date: date,
        document_pattern: str | None = None,
    ) -> tuple[RetrievalHit, ...]: ...

    def exact_vector_search(
        self,
        vector: Sequence[float],
        *,
        model: str,
        revision: str,
        limit: int,
        as_of_date: date,
        document_pattern: str | None = None,
    ) -> tuple[RetrievalHit, ...]: ...

    def parent_chunks(
        self,
        parent_chunk_ids: Sequence[str],
        *,
        as_of_date: date,
    ) -> tuple[RetrievalHit, ...]: ...

    def deactivate_document(self, document_id: str) -> int: ...

    def pending_documents(self, batch_size: int) -> tuple[str, ...]: ...

    def start_checkpoint(
        self,
        document_id: str,
        document_version_id: str,
        *,
        stage: str,
        chunking_version: str,
        embedding_model: str | None,
        embedding_revision: str | None,
        resume: bool,
    ) -> str: ...

    def finish_checkpoint(
        self,
        checkpoint_id: str,
        *,
        status: str,
        last_chunk_ordinal: int,
        error_code: str | None = None,
    ) -> None: ...

    def audit(
        self,
        category: str,
        document_id: str,
        source_ids: Sequence[str],
        *,
        model_version: str | None = None,
        result_status: str = "ALLOWED",
    ) -> None: ...


class PostgresChunkRepository:
    """Parameterized storage with PostgreSQL RLS applied before every query."""

    def __init__(
        self,
        connection: Connection,
        context: AuthorizationContext,
    ) -> None:
        self._connection = connection
        self._context = context
        apply_postgres_session_context(connection, context)

    def replace_document_chunks(
        self,
        chunks: Sequence[DocumentChunk],
    ) -> ChunkWriteSummary:
        if not chunks:
            raise ValueError("at least one chunk is required")
        document_ids = {chunk.canonical_document_id for chunk in chunks}
        version_ids = {chunk.document_version_id for chunk in chunks}
        versions = {chunk.chunking_version for chunk in chunks}
        if len(document_ids) != 1 or len(version_ids) != 1 or len(versions) != 1:
            raise ValueError("one chunk write may contain only one document version")
        document_id = next(iter(document_ids))
        document_version_id = next(iter(version_ids))
        chunking_version = next(iter(versions))
        current_ids = {chunk.chunk_id for chunk in chunks}
        existing_rows = self._connection.execute(
            text(
                """
                SELECT chunk_id, content_hash
                FROM pms_vector.document_chunk
                WHERE canonical_document_id = :document_id
                  AND document_version_id = :version_id
                  AND chunking_version = :chunking_version
                  AND active
                """
            ),
            {
                "document_id": document_id,
                "version_id": document_version_id,
                "chunking_version": chunking_version,
            },
        ).mappings()
        existing = {
            str(row["chunk_id"]): str(row["content_hash"]) for row in existing_rows
        }
        stale = tuple(chunk_id for chunk_id in existing if chunk_id not in current_ids)
        if stale:
            self._deactivate_chunk_ids(stale)

        created = 0
        unchanged = 0
        for chunk in chunks:
            if existing.get(chunk.chunk_id) == chunk.content_hash:
                unchanged += 1
                continue
            self._insert_chunk(chunk)
            created += 1
        return ChunkWriteSummary(
            document_id=document_id,
            document_version_id=document_version_id,
            created=created,
            unchanged=unchanged,
            deactivated=len(stale),
            parent_chunks=sum(chunk.chunk_kind is ChunkKind.PARENT for chunk in chunks),
            child_chunks=sum(chunk.chunk_kind is ChunkKind.CHILD for chunk in chunks),
        )

    def _insert_chunk(self, chunk: DocumentChunk) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO pms_vector.document_chunk (
                  chunk_id, canonical_document_id, document_version_id,
                  parent_chunk_id, chunk_kind, ordinal, text, token_count,
                  content_hash, heading_path, page_numbers, bounding_boxes,
                  section_number, clause_number, language_code, languages,
                  script_code, translation_group_id, authoritative_language,
                  publication_date, effective_from, effective_to,
                  document_status, port_id, department_id,
                  security_classification, review_status, ocr_confidence,
                  parser_name, parser_version, chunking_version, active,
                  created_by_subject, created_at
                ) VALUES (
                  :chunk_id, :document_id, :version_id, :parent_chunk_id,
                  :chunk_kind, :ordinal, :text, :token_count, :content_hash,
                  CAST(:heading_path AS jsonb), :page_numbers,
                  CAST(:bounding_boxes AS jsonb), :section_number,
                  :clause_number, :language_code, :languages, :script_code,
                  :translation_group_id, :authoritative_language,
                  :publication_date, :effective_from, :effective_to,
                  :document_status, :port_id, :department_id, :classification,
                  :review_status, :ocr_confidence, :parser_name,
                  :parser_version, :chunking_version, true,
                  :created_by_subject, :created_at
                )
                """
            ),
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.canonical_document_id,
                "version_id": chunk.document_version_id,
                "parent_chunk_id": chunk.parent_chunk_id,
                "chunk_kind": chunk.chunk_kind.value,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "token_count": chunk.token_count,
                "content_hash": chunk.content_hash,
                "heading_path": json.dumps(chunk.heading_path),
                "page_numbers": list(chunk.page_numbers),
                "bounding_boxes": json.dumps(
                    [citation.model_dump(mode="json") for citation in chunk.citations]
                ),
                "section_number": chunk.section_number,
                "clause_number": chunk.clause_number,
                "language_code": chunk.language_code,
                "languages": list(chunk.languages),
                "script_code": chunk.script_code,
                "translation_group_id": chunk.translation_group_id,
                "authoritative_language": chunk.authoritative_language,
                "publication_date": chunk.publication_date,
                "effective_from": chunk.effective_from,
                "effective_to": chunk.effective_to,
                "document_status": chunk.document_status,
                "port_id": chunk.port_id,
                "department_id": chunk.department_id,
                "classification": chunk.security_classification.value,
                "review_status": chunk.review_status,
                "ocr_confidence": chunk.ocr_confidence,
                "parser_name": chunk.parser_name,
                "parser_version": chunk.parser_version,
                "chunking_version": chunk.chunking_version,
                "created_by_subject": self._context.subject,
                "created_at": datetime.now(UTC),
            },
        )
        self._connection.execute(
            text(
                """
                INSERT INTO pms_vector.chunk_acl (
                  chunk_id, canonical_document_id, canonical_tenant_id,
                  classification, allowed_roles, allowed_departments
                )
                SELECT
                  :chunk_id, acl.canonical_document_id,
                  acl.canonical_tenant_id, acl.classification,
                  acl.allowed_roles, acl.allowed_departments
                FROM pms_doc.document_acl AS acl
                WHERE acl.canonical_document_id = :document_id
                """
            ),
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.canonical_document_id,
            },
        )

    def _deactivate_chunk_ids(self, chunk_ids: Sequence[str]) -> None:
        parameters = {"chunk_ids": tuple(chunk_ids), "now": datetime.now(UTC)}
        self._connection.execute(
            text(
                """
                UPDATE pms_vector.chunk_embedding
                SET active = false, deactivated_at = :now
                WHERE chunk_id IN :chunk_ids AND active
                """
            ).bindparams(bindparam("chunk_ids", expanding=True)),
            parameters,
        )
        self._connection.execute(
            text(
                """
                UPDATE pms_vector.document_chunk
                SET active = false,
                    deactivated_by_subject = :subject,
                    deactivated_at = :now
                WHERE chunk_id IN :chunk_ids AND active
                """
            ).bindparams(bindparam("chunk_ids", expanding=True)),
            {
                **parameters,
                "subject": self._context.subject,
            },
        )

    def child_chunks(self, document_id: str) -> tuple[DocumentChunk, ...]:
        rows = self._connection.execute(
            text(
                """
                SELECT *
                FROM pms_vector.document_chunk
                WHERE canonical_document_id = :document_id
                  AND chunk_kind = 'child'
                  AND active
                ORDER BY ordinal
                """
            ),
            {"document_id": document_id},
        ).mappings()
        return tuple(_chunk_from_row(dict(row)) for row in rows)

    def embedding_plan(
        self,
        document_id: str,
        *,
        model: str,
        revision: str,
        embedding_version: str,
        dimension: int,
    ) -> EmbeddingPlan:
        rows = self._connection.execute(
            text(
                """
                SELECT
                  chunk.chunk_id,
                  chunk.content_hash,
                  embedding.content_hash AS embedded_hash
                FROM pms_vector.document_chunk AS chunk
                LEFT JOIN pms_vector.chunk_embedding AS embedding
                  ON embedding.chunk_id = chunk.chunk_id
                 AND embedding.embedding_model = :model
                 AND embedding.embedding_revision = :revision
                 AND embedding.embedding_version = :embedding_version
                 AND embedding.active
                WHERE chunk.canonical_document_id = :document_id
                  AND chunk.chunk_kind = 'child'
                  AND chunk.active
                ORDER BY chunk.ordinal
                """
            ),
            {
                "document_id": document_id,
                "model": model,
                "revision": revision,
                "embedding_version": embedding_version,
            },
        ).mappings()
        pending: list[str] = []
        unchanged: list[str] = []
        for row in rows:
            target = (
                unchanged
                if row["embedded_hash"] == row["content_hash"]
                else pending
            )
            target.append(str(row["chunk_id"]))
        return EmbeddingPlan(
            document_id=document_id,
            model=model,
            revision=revision,
            embedding_version=embedding_version,
            dimension=dimension,
            pending_chunk_ids=tuple(pending),
            unchanged_chunk_ids=tuple(unchanged),
        )

    def store_embeddings(
        self,
        document_id: str,
        writes: Sequence[EmbeddingWrite],
        *,
        model: str,
        revision: str,
        embedding_version: str,
        dimension: int,
    ) -> EmbeddingWriteSummary:
        for write in writes:
            if len(write.vector) != dimension:
                raise ValueError("embedding dimension does not match configured value")
            vector = _vector_literal(write.vector)
            now = datetime.now(UTC)
            self._connection.execute(
                text(
                    """
                    UPDATE pms_vector.chunk_embedding
                    SET active = false, deactivated_at = :now
                    WHERE chunk_id = :chunk_id
                      AND embedding_model = :model
                      AND embedding_revision = :revision
                      AND active
                    """
                ),
                {
                    "now": now,
                    "chunk_id": write.chunk_id,
                    "model": model,
                    "revision": revision,
                },
            )
            self._connection.execute(
                text(
                    """
                    INSERT INTO pms_vector.chunk_embedding (
                      embedding_id, chunk_id, embedding_model,
                      embedding_revision, embedding_version, dimension,
                      content_hash, embedding, active, created_by_subject,
                      created_at
                    ) VALUES (
                      :embedding_id, :chunk_id, :model, :revision,
                      :embedding_version, :dimension, :content_hash,
                      CAST(:embedding AS pms_vector.vector), true,
                      :subject, :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_chunk_embedding_version
                    DO UPDATE SET
                      embedding = EXCLUDED.embedding,
                      active = true,
                      created_by_subject = EXCLUDED.created_by_subject,
                      created_at = EXCLUDED.created_at,
                      deactivated_at = NULL
                    """
                ),
                {
                    "embedding_id": str(uuid4()),
                    "chunk_id": write.chunk_id,
                    "model": model,
                    "revision": revision,
                    "embedding_version": embedding_version,
                    "dimension": dimension,
                    "content_hash": write.content_hash,
                    "embedding": vector,
                    "subject": self._context.subject,
                    "created_at": now,
                },
            )
        total_children = len(self.child_chunks(document_id))
        return EmbeddingWriteSummary(
            document_id=document_id,
            created=len(writes),
            unchanged=max(total_children - len(writes), 0),
            dimension=dimension,
        )

    def lexical_search(
        self,
        query: str,
        limit: int,
        *,
        as_of_date: date,
        document_pattern: str | None = None,
    ) -> tuple[RetrievalHit, ...]:
        if not query.strip():
            return ()
        rows = self._connection.execute(
            text(
                """
                SELECT
                  chunk.chunk_id, chunk.parent_chunk_id,
                  chunk.canonical_document_id, chunk.document_version_id,
                  record.title AS document_title, chunk.text,
                  chunk.page_numbers, chunk.bounding_boxes,
                  chunk.language_code, chunk.languages, chunk.script_code,
                  chunk.heading_path, chunk.section_number,
                  chunk.clause_number, chunk.translation_group_id,
                  chunk.authoritative_language, chunk.effective_from,
                  chunk.effective_to,
                  ts_rank_cd(
                    chunk.fts,
                    websearch_to_tsquery('simple', :query)
                  ) AS score
                FROM pms_vector.document_chunk AS chunk
                JOIN pms_vector.chunk_acl AS acl
                  ON acl.chunk_id = chunk.chunk_id
                JOIN pms_doc.document_record AS record
                  ON record.canonical_document_id = chunk.canonical_document_id
                WHERE chunk.active
                  AND chunk.chunk_kind = 'child'
                  AND chunk.review_status = 'accepted'
                  AND record.status = 'indexed'
                  AND (chunk.effective_from IS NULL
                       OR chunk.effective_from <= :as_of_date)
                  AND (chunk.effective_to IS NULL
                       OR chunk.effective_to > :as_of_date)
                  AND (
                    CAST(:document_pattern AS text) IS NULL
                    OR record.title ILIKE :document_pattern
                    OR record.original_filename ILIKE :document_pattern
                  )
                  AND chunk.fts @@ websearch_to_tsquery('simple', :query)
                ORDER BY score DESC, chunk.chunk_id
                LIMIT :limit
                """
            ),
            {
                "query": query,
                "limit": limit,
                "as_of_date": as_of_date,
                "document_pattern": document_pattern,
            },
        ).mappings()
        return tuple(_hit_from_row(dict(row)) for row in rows)

    def exact_vector_search(
        self,
        vector: Sequence[float],
        *,
        model: str,
        revision: str,
        limit: int,
        as_of_date: date,
        document_pattern: str | None = None,
    ) -> tuple[RetrievalHit, ...]:
        literal = _vector_literal(vector)
        rows = self._connection.execute(
            text(
                """
                SELECT
                  chunk.chunk_id,
                  chunk.parent_chunk_id,
                  chunk.canonical_document_id,
                  chunk.document_version_id,
                  record.title AS document_title,
                  chunk.text,
                  chunk.page_numbers,
                  chunk.bounding_boxes,
                  chunk.language_code,
                  chunk.languages,
                  chunk.script_code,
                  chunk.heading_path,
                  chunk.section_number,
                  chunk.clause_number,
                  chunk.translation_group_id,
                  chunk.authoritative_language,
                  chunk.effective_from,
                  chunk.effective_to,
                  1 - (
                    embedding.embedding OPERATOR(pms_vector.<=>)
                    CAST(:query_embedding AS pms_vector.vector)
                  ) AS score
                FROM pms_vector.document_chunk AS chunk
                JOIN pms_vector.chunk_acl AS acl
                  ON acl.chunk_id = chunk.chunk_id
                JOIN pms_doc.document_record AS record
                  ON record.canonical_document_id = chunk.canonical_document_id
                JOIN pms_vector.chunk_embedding AS embedding
                  ON embedding.chunk_id = chunk.chunk_id
                WHERE chunk.active
                  AND chunk.chunk_kind = 'child'
                  AND chunk.review_status = 'accepted'
                  AND record.status = 'indexed'
                  AND (chunk.effective_from IS NULL
                       OR chunk.effective_from <= :as_of_date)
                  AND (chunk.effective_to IS NULL
                       OR chunk.effective_to > :as_of_date)
                  AND (
                    CAST(:document_pattern AS text) IS NULL
                    OR record.title ILIKE :document_pattern
                    OR record.original_filename ILIKE :document_pattern
                  )
                  AND embedding.active
                  AND embedding.embedding_model = :model
                  AND embedding.embedding_revision = :revision
                ORDER BY
                  embedding.embedding OPERATOR(pms_vector.<=>)
                  CAST(:query_embedding AS pms_vector.vector),
                  chunk.chunk_id
                LIMIT :limit
                """
            ),
            {
                "query_embedding": literal,
                "model": model,
                "revision": revision,
                "limit": limit,
                "as_of_date": as_of_date,
                "document_pattern": document_pattern,
            },
        ).mappings()
        return tuple(_hit_from_row(dict(row)) for row in rows)

    def parent_chunks(
        self,
        parent_chunk_ids: Sequence[str],
        *,
        as_of_date: date,
    ) -> tuple[RetrievalHit, ...]:
        if not parent_chunk_ids:
            return ()
        rows = self._connection.execute(
            text(
                """
                SELECT
                  chunk.chunk_id, chunk.parent_chunk_id,
                  chunk.canonical_document_id, chunk.document_version_id,
                  record.title AS document_title, chunk.text,
                  chunk.page_numbers, chunk.bounding_boxes,
                  chunk.language_code, chunk.languages, chunk.script_code,
                  chunk.heading_path, chunk.section_number,
                  chunk.clause_number, chunk.translation_group_id,
                  chunk.authoritative_language, chunk.effective_from,
                  chunk.effective_to, 0.0 AS score
                FROM pms_vector.document_chunk AS chunk
                JOIN pms_vector.chunk_acl AS acl
                  ON acl.chunk_id = chunk.chunk_id
                JOIN pms_doc.document_record AS record
                  ON record.canonical_document_id = chunk.canonical_document_id
                WHERE chunk.chunk_id IN :parent_chunk_ids
                  AND chunk.active
                  AND chunk.chunk_kind = 'parent'
                  AND chunk.review_status = 'accepted'
                  AND record.status = 'indexed'
                  AND (chunk.effective_from IS NULL
                       OR chunk.effective_from <= :as_of_date)
                  AND (chunk.effective_to IS NULL
                       OR chunk.effective_to > :as_of_date)
                ORDER BY chunk.chunk_id
                """
            ).bindparams(bindparam("parent_chunk_ids", expanding=True)),
            {
                "parent_chunk_ids": tuple(parent_chunk_ids),
                "as_of_date": as_of_date,
            },
        ).mappings()
        return tuple(_hit_from_row(dict(row)) for row in rows)

    def deactivate_document(self, document_id: str) -> int:
        rows = self._connection.execute(
            text(
                """
                SELECT chunk_id
                FROM pms_vector.document_chunk
                WHERE canonical_document_id = :document_id AND active
                """
            ),
            {"document_id": document_id},
        ).scalars()
        chunk_ids = tuple(str(value) for value in rows)
        if chunk_ids:
            self._deactivate_chunk_ids(chunk_ids)
        return len(chunk_ids)

    def pending_documents(self, batch_size: int) -> tuple[str, ...]:
        rows = self._connection.execute(
            text(
                """
                SELECT record.canonical_document_id
                FROM pms_doc.document_record AS record
                JOIN pms_doc.document_acl AS acl
                  ON acl.canonical_document_id = record.canonical_document_id
                WHERE record.status = 'canonicalized'
                ORDER BY record.updated_at, record.canonical_document_id
                LIMIT :batch_size
                """
            ),
            {"batch_size": batch_size},
        ).scalars()
        return tuple(str(value) for value in rows)

    def start_checkpoint(
        self,
        document_id: str,
        document_version_id: str,
        *,
        stage: str,
        chunking_version: str,
        embedding_model: str | None,
        embedding_revision: str | None,
        resume: bool,
    ) -> str:
        checkpoint_id = hashlib.sha256(
            (
                f"{document_id}:{document_version_id}:{stage}:"
                f"{chunking_version}:{embedding_model or ''}:"
                f"{embedding_revision or ''}"
            ).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        self._connection.execute(
            text(
                """
                INSERT INTO pms_vector.index_checkpoint (
                  checkpoint_id, canonical_document_id, document_version_id,
                  stage, status, last_chunk_ordinal, chunking_version,
                  embedding_model, embedding_revision, error_code,
                  started_by_subject, started_at, updated_at
                ) VALUES (
                  :checkpoint_id, :document_id, :version_id, :stage,
                  'running', -1, :chunking_version, :embedding_model,
                  :embedding_revision, NULL, :subject, :now, :now
                )
                ON CONFLICT (checkpoint_id) DO UPDATE SET
                  status = 'running',
                  last_chunk_ordinal = CASE
                    WHEN :resume THEN index_checkpoint.last_chunk_ordinal
                    ELSE -1
                  END,
                  error_code = NULL,
                  started_by_subject = :subject,
                  updated_at = :now
                """
            ),
            {
                "checkpoint_id": checkpoint_id,
                "document_id": document_id,
                "version_id": document_version_id,
                "stage": stage,
                "chunking_version": chunking_version,
                "embedding_model": embedding_model,
                "embedding_revision": embedding_revision,
                "subject": self._context.subject,
                "now": now,
                "resume": resume,
            },
        )
        return checkpoint_id

    def finish_checkpoint(
        self,
        checkpoint_id: str,
        *,
        status: str,
        last_chunk_ordinal: int,
        error_code: str | None = None,
    ) -> None:
        if status not in {"running", "complete", "failed"}:
            raise ValueError("invalid checkpoint status")
        self._connection.execute(
            text(
                """
                UPDATE pms_vector.index_checkpoint
                SET status = :status,
                    last_chunk_ordinal = :last_chunk_ordinal,
                    error_code = :error_code,
                    updated_at = :updated_at
                WHERE checkpoint_id = :checkpoint_id
                """
            ),
            {
                "status": status,
                "last_chunk_ordinal": last_chunk_ordinal,
                "error_code": error_code,
                "updated_at": datetime.now(UTC),
                "checkpoint_id": checkpoint_id,
            },
        )

    def audit(
        self,
        category: str,
        document_id: str,
        source_ids: Sequence[str],
        *,
        model_version: str | None = None,
        result_status: str = "ALLOWED",
    ) -> None:
        write_audit_event(
            self._connection,
            create_audit_event(
                self._context,
                query_category=category,
                entity_scope={"document_id": document_id},
                source_ids=tuple(source_ids),
                model_version=model_version,
                result_status=result_status,
            ),
        )


def _vector_literal(values: Sequence[float]) -> str:
    normalized = tuple(float(value) for value in values)
    if not normalized or not all(math.isfinite(value) for value in normalized):
        raise ValueError("vector must contain only finite values")
    return "[" + ",".join(format(value, ".9g") for value in normalized) + "]"


def _chunk_from_row(row: dict[str, object]) -> DocumentChunk:
    citations_value = row["bounding_boxes"]
    citations = (
        citations_value
        if isinstance(citations_value, list)
        else json.loads(str(citations_value))
    )
    heading_value = row["heading_path"]
    heading = (
        heading_value if isinstance(heading_value, list) else json.loads(str(heading_value))
    )
    return DocumentChunk(
        chunk_id=str(row["chunk_id"]),
        canonical_document_id=str(row["canonical_document_id"]),
        document_version_id=str(row["document_version_id"]),
        parent_chunk_id=(
            str(row["parent_chunk_id"]) if row["parent_chunk_id"] is not None else None
        ),
        chunk_kind=ChunkKind(str(row["chunk_kind"])),
        ordinal=int(str(row["ordinal"])),
        text=str(row["text"]),
        token_count=int(str(row["token_count"])),
        content_hash=str(row["content_hash"]),
        heading_path=tuple(str(item) for item in heading),
        page_numbers=tuple(
            int(str(item)) for item in _object_sequence(row["page_numbers"])
        ),
        citations=tuple(ChunkCitation.model_validate(item) for item in citations),
        section_number=(
            str(row["section_number"]) if row["section_number"] is not None else None
        ),
        clause_number=(
            str(row["clause_number"]) if row["clause_number"] is not None else None
        ),
        language_code=str(row["language_code"]),
        languages=tuple(str(item) for item in _object_sequence(row["languages"])),
        script_code=str(row["script_code"]),
        translation_group_id=(
            str(row["translation_group_id"])
            if row["translation_group_id"] is not None
            else None
        ),
        authoritative_language=(
            str(row["authoritative_language"])
            if row["authoritative_language"] is not None
            else None
        ),
        publication_date=row["publication_date"],  # type: ignore[arg-type]
        effective_from=row["effective_from"],  # type: ignore[arg-type]
        effective_to=row["effective_to"],  # type: ignore[arg-type]
        document_status=str(row["document_status"]),
        port_id=str(row["port_id"]) if row["port_id"] is not None else None,
        department_id=(
            str(row["department_id"]) if row["department_id"] is not None else None
        ),
        security_classification=Classification(str(row["security_classification"])),
        review_status=str(row["review_status"]),
        ocr_confidence=(
            float(str(row["ocr_confidence"]))
            if row["ocr_confidence"] is not None
            else None
        ),
        parser_name=str(row["parser_name"]),
        parser_version=str(row["parser_version"]),
        chunking_version=str(row["chunking_version"]),
    )


def _hit_from_row(row: dict[str, object]) -> RetrievalHit:
    citations_value = row["bounding_boxes"]
    citations = (
        citations_value
        if isinstance(citations_value, list)
        else json.loads(str(citations_value))
    )
    heading_value = row["heading_path"]
    heading = (
        heading_value if isinstance(heading_value, list) else json.loads(str(heading_value))
    )
    return RetrievalHit(
        chunk_id=str(row["chunk_id"]),
        parent_chunk_id=(
            str(row["parent_chunk_id"]) if row["parent_chunk_id"] is not None else None
        ),
        document_id=str(row["canonical_document_id"]),
        document_version_id=str(row["document_version_id"]),
        document_title=str(row["document_title"]),
        text=str(row["text"]),
        page_numbers=tuple(
            int(str(item)) for item in _object_sequence(row["page_numbers"])
        ),
        citations=tuple(ChunkCitation.model_validate(item) for item in citations),
        language_code=str(row["language_code"]),
        languages=tuple(str(item) for item in _object_sequence(row["languages"])),
        script_code=str(row["script_code"]),
        heading_path=tuple(str(item) for item in heading),
        section_number=(
            str(row["section_number"]) if row["section_number"] is not None else None
        ),
        clause_number=(
            str(row["clause_number"]) if row["clause_number"] is not None else None
        ),
        translation_group_id=(
            str(row["translation_group_id"])
            if row["translation_group_id"] is not None
            else None
        ),
        authoritative_language=(
            str(row["authoritative_language"])
            if row["authoritative_language"] is not None
            else None
        ),
        effective_from=row["effective_from"],  # type: ignore[arg-type]
        effective_to=row["effective_to"],  # type: ignore[arg-type]
        score=float(str(row["score"])),
    )


def _object_sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list | tuple):
        raise ValueError("database array value is invalid")
    return value
