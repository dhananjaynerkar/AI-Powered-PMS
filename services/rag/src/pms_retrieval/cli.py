"""Authenticated document indexing and Phase 08 grounded-RAG commands."""

from __future__ import annotations

import argparse
import json
import os
from typing import NoReturn

from pms_common.database import create_database_engine
from pms_common.security import (
    AuthenticationError,
    AuthorizationContext,
    AuthorizationDenied,
    JwtValidator,
)
from pms_common.settings import Settings
from pms_ingestion.service import DocumentServiceError
from pms_ingestion.storage import ObjectStorageError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from pms_retrieval.chunking import ChunkingError
from pms_retrieval.embedding import EmbeddingError, check_bge_m3_model
from pms_retrieval.generation import GenerationError, OllamaGenerator
from pms_retrieval.models import ResponseLanguage
from pms_retrieval.rag import HybridRagService, PostgresRagRepository
from pms_retrieval.reranking import (
    RerankerUnavailable,
    RerankingError,
    check_reranker_model,
)
from pms_retrieval.service import RetrievalCoordinator, RetrievalServiceError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pms_retrieval.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    models = commands.add_parser("models")
    model_commands = models.add_subparsers(dest="model_command", required=True)
    model_commands.add_parser("check")

    commands.add_parser("rag-check")

    ask = commands.add_parser("ask")
    ask.add_argument("--query", required=True)
    ask.add_argument("--response-language", choices=("auto", "en", "hi", "mr"), default="auto")
    ask.add_argument("--show-sources", action="store_true")
    ask.add_argument("--show-trace", action="store_true")

    chunk = commands.add_parser("chunk")
    chunk.add_argument("--document-id", required=True)
    chunk.add_argument("--explain", action="store_true")
    chunk.add_argument("--resume", action="store_true")

    embed = commands.add_parser("embed")
    embed.add_argument("--document-id", required=True)
    embed.add_argument("--dry-run", action="store_true")
    embed.add_argument("--resume", action="store_true")

    pending = commands.add_parser("index-pending")
    pending.add_argument("--batch-size", type=int, default=5)
    pending.add_argument("--resume", action="store_true")

    deactivate = commands.add_parser("deactivate")
    deactivate.add_argument("--document-id", required=True)
    return parser


def _error(message: str) -> NoReturn:
    raise SystemExit(message)


def _trusted_context(settings: Settings) -> AuthorizationContext:
    token = os.environ.get("PMS_ACCESS_TOKEN", "").strip()
    if not token:
        _error("PMS_ACCESS_TOKEN is required for Phase 07 document commands")
    try:
        return JwtValidator(settings).validate(token)
    except AuthenticationError:
        _error("PMS_ACCESS_TOKEN is invalid")


def _model_check(settings: Settings) -> int:
    embedding = check_bge_m3_model(settings)
    reranker = check_reranker_model(settings)
    try:
        available_models = OllamaGenerator(settings).available_models()
        ollama_detail = "local Ollama API is available"
    except GenerationError as error:
        available_models = frozenset()
        ollama_detail = str(error)
    required_llms = {
        settings.llm_primary_model,
        settings.llm_fallback_model,
    }
    passed = (
        embedding.available
        and reranker.available
        and required_llms <= available_models
    )
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "provider": settings.embedding_provider,
                "provider_version": embedding.provider_version,
                "model": embedding.model,
                "revision": embedding.revision,
                "dimension": embedding.dimension,
                "device": settings.embedding_device,
                "local_only": True,
                "detail": embedding.detail,
                "reranker": {
                    "available": reranker.available,
                    "model": reranker.model,
                    "revision": reranker.revision,
                    "provider_version": reranker.provider_version,
                    "detail": reranker.detail,
                },
                "ollama": {
                    "available": bool(available_models),
                    "required_models": sorted(required_llms),
                    "missing_models": sorted(required_llms - available_models),
                    "detail": ollama_detail,
                },
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 2


def _rag_check(settings: Settings) -> int:
    context = _trusted_context(settings)
    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            from pms_retrieval.repository import PostgresChunkRepository

            PostgresChunkRepository(connection, context)
            revision = connection.execute(
                text("SELECT version_num FROM pms_app.alembic_version")
            ).scalar_one()
            counts = connection.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE chunk_kind = 'parent' AND active
                      ) AS parents,
                      count(*) FILTER (
                        WHERE chunk_kind = 'child' AND active
                      ) AS children
                    FROM pms_vector.document_chunk
                    """
                )
            ).mappings().one()
            embeddings = connection.execute(
                text(
                    "SELECT count(*) FROM pms_vector.chunk_embedding WHERE active"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    embedding = check_bge_m3_model(settings)
    reranker = check_reranker_model(settings)
    try:
        available = OllamaGenerator(settings).available_models()
    except GenerationError:
        available = frozenset()
    required = {settings.llm_primary_model, settings.llm_fallback_model}
    passed = (
        revision == "20260729_0006"
        and int(counts["parents"]) > 0
        and int(counts["children"]) > 0
        and int(embeddings) > 0
        and embedding.available
        and reranker.available
        and required <= available
    )
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "revision": revision,
                "active_parent_chunks": int(counts["parents"]),
                "active_child_chunks": int(counts["children"]),
                "active_embeddings": int(embeddings),
                "embedding_available": embedding.available,
                "reranker_available": reranker.available,
                "missing_llm_models": sorted(required - available),
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 2


def _ask(settings: Settings, arguments: argparse.Namespace) -> int:
    context = _trusted_context(settings)
    engine = create_database_engine(settings, read_only=False)
    try:
        service = HybridRagService(
            PostgresRagRepository(engine, context),
            context,
            settings,
        )
        answer = service.ask(
            arguments.query,
            response_language=ResponseLanguage(arguments.response_language),
            include_trace=arguments.show_trace,
        )
    finally:
        engine.dispose()
    payload = answer.model_dump(mode="json")
    if not arguments.show_sources:
        payload["sources"] = []
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 2 if answer.review_required else 0


def _coordinator(settings: Settings) -> tuple[RetrievalCoordinator, Engine]:
    engine = create_database_engine(settings, read_only=False)
    return RetrievalCoordinator(engine, _trusted_context(settings), settings), engine


def _chunk(settings: Settings, arguments: argparse.Namespace) -> int:
    coordinator, engine = _coordinator(settings)
    try:
        result = coordinator.chunk(
            arguments.document_id,
            resume=arguments.resume,
        )
    finally:
        engine.dispose()
    payload: dict[str, object] = {
        "document_id": result.summary.document_id,
        "document_version_id": result.summary.document_version_id,
        "status": result.status,
        "created": result.summary.created,
        "unchanged": result.summary.unchanged,
        "deactivated": result.summary.deactivated,
        "parent_chunks": result.summary.parent_chunks,
        "child_chunks": result.summary.child_chunks,
        "maximum_parent_tokens": result.maximum_parent_tokens,
        "maximum_child_tokens": result.maximum_child_tokens,
    }
    if arguments.explain:
        payload["next_command"] = (
            "python -m pms_retrieval.cli embed "
            f"--document-id {result.summary.document_id} --dry-run"
        )
        payload["security"] = "document ACL copied to every chunk before retrieval"
    print(json.dumps(payload, sort_keys=True))
    return 0


def _embed(settings: Settings, arguments: argparse.Namespace) -> int:
    coordinator, engine = _coordinator(settings)
    try:
        result = coordinator.embed(
            arguments.document_id,
            dry_run=arguments.dry_run,
            resume=arguments.resume,
        )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "document_id": result.plan.document_id,
                "status": result.status,
                "dry_run": result.dry_run,
                "model": result.plan.model,
                "revision": result.plan.revision,
                "embedding_version": result.plan.embedding_version,
                "dimension": result.plan.dimension,
                "pending": len(result.plan.pending_chunk_ids),
                "unchanged": len(result.plan.unchanged_chunk_ids),
                "created": (
                    result.write_summary.created if result.write_summary else 0
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def _index_pending(settings: Settings, arguments: argparse.Namespace) -> int:
    if arguments.batch_size < 1 or arguments.batch_size > 100:
        _error("--batch-size must be between 1 and 100")
    coordinator, engine = _coordinator(settings)
    results: list[dict[str, object]] = []
    try:
        document_ids = coordinator.pending_documents(arguments.batch_size)
        for document_id in document_ids:
            chunked = coordinator.chunk(document_id, resume=arguments.resume)
            embedded = coordinator.embed(
                document_id,
                dry_run=False,
                resume=arguments.resume,
            )
            results.append(
                {
                    "document_id": document_id,
                    "chunks_created": chunked.summary.created,
                    "chunks_unchanged": chunked.summary.unchanged,
                    "embeddings_created": (
                        embedded.write_summary.created
                        if embedded.write_summary
                        else 0
                    ),
                    "status": embedded.status,
                }
            )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "processed": len(results),
                "batch_size": arguments.batch_size,
                "resume": arguments.resume,
                "results": results,
            },
            sort_keys=True,
        )
    )
    return 0


def _deactivate(settings: Settings, arguments: argparse.Namespace) -> int:
    coordinator, engine = _coordinator(settings)
    try:
        deactivated = coordinator.deactivate(arguments.document_id)
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "document_id": arguments.document_id,
                "status": "deactivated",
                "chunks_deactivated": deactivated,
                "audit_history_preserved": True,
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    settings = Settings()
    try:
        if arguments.command == "models":
            return _model_check(settings)
        if arguments.command == "rag-check":
            return _rag_check(settings)
        if arguments.command == "ask":
            return _ask(settings, arguments)
        if arguments.command == "chunk":
            return _chunk(settings, arguments)
        if arguments.command == "embed":
            return _embed(settings, arguments)
        if arguments.command == "index-pending":
            return _index_pending(settings, arguments)
        if arguments.command == "deactivate":
            return _deactivate(settings, arguments)
    except (
        AuthorizationDenied,
        ChunkingError,
        DocumentServiceError,
        EmbeddingError,
        GenerationError,
        ObjectStorageError,
        RerankerUnavailable,
        RerankingError,
        RetrievalServiceError,
        SQLAlchemyError,
        ValueError,
    ) as error:
        _error(str(error))
    _error("unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
