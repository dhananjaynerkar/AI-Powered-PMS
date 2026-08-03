"""Phase 07 orchestration across canonical artifacts, chunks and embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pms_common.security import AuthorizationContext
from pms_common.settings import Settings
from pms_ingestion.factory import create_document_service
from pms_ingestion.models import DocumentStatus, ObjectKind
from pms_ingestion.parsing import CanonicalDocument
from pms_ingestion.parsing_service import PIPELINE_PRODUCER, PIPELINE_VERSION
from pms_ingestion.storage import MinioObjectStore, ObjectStore
from sqlalchemy import Engine

from pms_retrieval.chunking import CHUNKING_VERSION, StructureAwareChunker, Tokenizer
from pms_retrieval.embedding import (
    BgeM3EmbeddingAdapter,
    BgeM3Tokenizer,
)
from pms_retrieval.models import (
    ChunkWriteSummary,
    EmbeddingPlan,
    EmbeddingWrite,
    EmbeddingWriteSummary,
    RetrievalHit,
)
from pms_retrieval.repository import PostgresChunkRepository


class RetrievalServiceError(RuntimeError):
    """Raised when a Phase 07 operation cannot complete safely."""


class InvalidIndexingStatus(RetrievalServiceError):
    """Raised when a non-canonical or review-required document is selected."""


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    summary: ChunkWriteSummary
    status: str
    maximum_child_tokens: int
    maximum_parent_tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    plan: EmbeddingPlan
    write_summary: EmbeddingWriteSummary | None
    dry_run: bool
    status: str


class RetrievalCoordinator:
    """Keep model work outside transactions and preserve authorization boundaries."""

    def __init__(
        self,
        engine: Engine,
        context: AuthorizationContext,
        settings: Settings,
        *,
        object_store: ObjectStore | None = None,
    ) -> None:
        self._engine = engine
        self._context = context
        self._settings = settings
        self._object_store = object_store or MinioObjectStore(settings)

    def chunk(
        self,
        document_id: str,
        *,
        tokenizer: Tokenizer | None = None,
        resume: bool = False,
    ) -> ChunkingResult:
        with self._engine.begin() as connection:
            document_service = create_document_service(
                connection,
                self._context,
                self._settings,
                object_store=self._object_store,
            )
            metadata = document_service.metadata(document_id)
            if metadata.status not in {
                DocumentStatus.CANONICALIZED.value,
                DocumentStatus.CHUNK_READY.value,
                DocumentStatus.INDEXED.value,
            }:
                raise InvalidIndexingStatus(
                    f"document status {metadata.status} is not eligible for chunking"
                )
            artifact = document_service.retrieve_derived(
                document_id=document_id,
                object_kind=ObjectKind.CANONICAL_JSON,
                producer=PIPELINE_PRODUCER,
                producer_version=PIPELINE_VERSION,
            )
            if artifact is None:
                raise RetrievalServiceError("canonical JSON artifact is missing")
            canonical = CanonicalDocument.model_validate_json(artifact.content)

        chunker = StructureAwareChunker(
            self._settings,
            tokenizer or BgeM3Tokenizer(self._settings),
        )
        chunks = chunker.chunk(
            canonical,
            self._context,
            classification=metadata.classification.value,
        )
        checkpoint_id = self._start_checkpoint(
            document_id,
            metadata.version_id,
            stage="chunk",
            embedding_model=None,
            embedding_revision=None,
            resume=resume,
        )
        try:
            with self._engine.begin() as connection:
                repository = PostgresChunkRepository(connection, self._context)
                summary = repository.replace_document_chunks(chunks)
                repository.finish_checkpoint(
                    checkpoint_id,
                    status="complete",
                    last_chunk_ordinal=max(
                        (chunk.ordinal for chunk in chunks),
                        default=-1,
                    ),
                )
                repository.audit(
                    "DOCUMENT_CHUNK",
                    document_id,
                    tuple(chunk.chunk_id for chunk in chunks),
                )
                document_service = create_document_service(
                    connection,
                    self._context,
                    self._settings,
                    object_store=self._object_store,
                )
                status = metadata.status
                if (
                    metadata.status != DocumentStatus.INDEXED.value
                    or summary.created
                    or summary.deactivated
                ):
                    status = document_service.transition_status(
                        document_id,
                        DocumentStatus.CHUNK_READY,
                    ).status
        except Exception as error:
            self._fail_checkpoint(checkpoint_id, type(error).__name__)
            raise
        children = [chunk for chunk in chunks if chunk.chunk_kind.value == "child"]
        parents = [chunk for chunk in chunks if chunk.chunk_kind.value == "parent"]
        return ChunkingResult(
            summary=summary,
            status=status,
            maximum_child_tokens=max(
                (chunk.token_count for chunk in children),
                default=0,
            ),
            maximum_parent_tokens=max(
                (chunk.token_count for chunk in parents),
                default=0,
            ),
        )

    def embed(
        self,
        document_id: str,
        *,
        dry_run: bool,
        adapter: BgeM3EmbeddingAdapter | None = None,
        resume: bool = False,
    ) -> EmbeddingResult:
        provider = adapter or BgeM3EmbeddingAdapter(self._settings)
        with self._engine.begin() as connection:
            document_service = create_document_service(
                connection,
                self._context,
                self._settings,
                object_store=self._object_store,
            )
            metadata = document_service.metadata(document_id)
            if metadata.status not in {
                DocumentStatus.CHUNK_READY.value,
                DocumentStatus.INDEXED.value,
            }:
                raise InvalidIndexingStatus(
                    f"document status {metadata.status} is not eligible for embedding"
                )
            repository = PostgresChunkRepository(connection, self._context)
            plan = repository.embedding_plan(
                document_id,
                model=provider.model,
                revision=provider.revision,
                embedding_version=provider.embedding_version,
                dimension=provider.dimension,
            )
            chunks = {
                chunk.chunk_id: chunk for chunk in repository.child_chunks(document_id)
            }
        if dry_run:
            return EmbeddingResult(
                plan=plan,
                write_summary=None,
                dry_run=True,
                status=metadata.status,
            )

        checkpoint_id = self._start_checkpoint(
            document_id,
            metadata.version_id,
            stage="embed",
            embedding_model=provider.model,
            embedding_revision=provider.revision,
            resume=resume,
        )
        try:
            pending_chunks = tuple(chunks[chunk_id] for chunk_id in plan.pending_chunk_ids)
            vectors = provider.embed(tuple(chunk.text for chunk in pending_chunks))
            writes = tuple(
                EmbeddingWrite(
                    chunk_id=chunk.chunk_id,
                    content_hash=chunk.content_hash,
                    vector=vector,
                )
                for chunk, vector in zip(pending_chunks, vectors, strict=True)
            )
            with self._engine.begin() as connection:
                repository = PostgresChunkRepository(connection, self._context)
                summary = repository.store_embeddings(
                    document_id,
                    writes,
                    model=provider.model,
                    revision=provider.revision,
                    embedding_version=provider.embedding_version,
                    dimension=provider.dimension,
                )
                repository.finish_checkpoint(
                    checkpoint_id,
                    status="complete",
                    last_chunk_ordinal=max(
                        (chunk.ordinal for chunk in pending_chunks),
                        default=-1,
                    ),
                )
                repository.audit(
                    "DOCUMENT_EMBED",
                    document_id,
                    plan.pending_chunk_ids,
                    model_version=(
                        f"{provider.model}@{provider.revision}:"
                        f"{provider.embedding_version}"
                    ),
                )
                document_service = create_document_service(
                    connection,
                    self._context,
                    self._settings,
                    object_store=self._object_store,
                )
                status = metadata.status
                if metadata.status == DocumentStatus.CHUNK_READY.value:
                    status = document_service.transition_status(
                        document_id,
                        DocumentStatus.INDEXED,
                    ).status
        except Exception as error:
            self._fail_checkpoint(checkpoint_id, type(error).__name__)
            raise
        return EmbeddingResult(
            plan=plan,
            write_summary=summary,
            dry_run=False,
            status=status,
        )

    def deactivate(self, document_id: str) -> int:
        with self._engine.begin() as connection:
            document_service = create_document_service(
                connection,
                self._context,
                self._settings,
                object_store=self._object_store,
            )
            metadata = document_service.metadata(document_id)
            if metadata.status not in {
                DocumentStatus.CANONICALIZED.value,
                DocumentStatus.CHUNK_READY.value,
                DocumentStatus.INDEXED.value,
            }:
                raise InvalidIndexingStatus(
                    f"document status {metadata.status} cannot be deactivated"
                )
            repository = PostgresChunkRepository(connection, self._context)
            count = repository.deactivate_document(document_id)
            repository.audit(
                "DOCUMENT_INDEX_DEACTIVATE",
                document_id,
                (metadata.version_id,),
            )
            document_service.transition_status(
                document_id,
                DocumentStatus.DEACTIVATED,
            )
            return count

    def lexical_search(
        self,
        query: str,
        limit: int,
        *,
        as_of_date: date | None = None,
        document_pattern: str | None = None,
    ) -> tuple[RetrievalHit, ...]:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(
                connection,
                self._context,
            ).lexical_search(
                query,
                limit,
                as_of_date=as_of_date or date.today(),
                document_pattern=document_pattern,
            )

    def exact_vector_search(
        self,
        vector: tuple[float, ...],
        *,
        model: str,
        revision: str,
        limit: int,
        as_of_date: date | None = None,
        document_pattern: str | None = None,
    ) -> tuple[RetrievalHit, ...]:
        if len(vector) != self._settings.embedding_dimension:
            raise ValueError(
                f"query vector must have {self._settings.embedding_dimension} values"
            )
        with self._engine.begin() as connection:
            return PostgresChunkRepository(
                connection,
                self._context,
            ).exact_vector_search(
                vector,
                model=model,
                revision=revision,
                limit=limit,
                as_of_date=as_of_date or date.today(),
                document_pattern=document_pattern,
            )

    def pending_documents(self, batch_size: int) -> tuple[str, ...]:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(
                connection,
                self._context,
            ).pending_documents(batch_size)

    def _start_checkpoint(
        self,
        document_id: str,
        version_id: str,
        *,
        stage: str,
        embedding_model: str | None,
        embedding_revision: str | None,
        resume: bool,
    ) -> str:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(
                connection,
                self._context,
            ).start_checkpoint(
                document_id,
                version_id,
                stage=stage,
                chunking_version=CHUNKING_VERSION,
                embedding_model=embedding_model,
                embedding_revision=embedding_revision,
                resume=resume,
            )

    def _fail_checkpoint(self, checkpoint_id: str, error_code: str) -> None:
        with self._engine.begin() as connection:
            PostgresChunkRepository(
                connection,
                self._context,
            ).finish_checkpoint(
                checkpoint_id,
                status="failed",
                last_chunk_ordinal=-1,
                error_code=error_code,
            )
