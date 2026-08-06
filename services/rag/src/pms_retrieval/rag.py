"""Secure Phase 08 hybrid retrieval and grounded RAG orchestration."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from threading import Lock
from typing import Literal, Protocol, cast

from pms_common.logging import get_request_id
from pms_common.security import (
    AuthorizationContext,
    AuthorizationService,
    Permission,
    UserRole,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from pms_common.settings import Settings
from sqlalchemy import Engine

from pms_retrieval.chunking import Tokenizer
from pms_retrieval.embedding import BgeM3EmbeddingAdapter, BgeM3Tokenizer
from pms_retrieval.generation import (
    DraftGenerator,
    GeneratedDraft,
    GenerationError,
    OllamaGenerator,
)
from pms_retrieval.models import (
    ContextEvidence,
    CorpusStatus,
    GroundedAnswer,
    QueryUnderstanding,
    RankedEvidence,
    ResponseLanguage,
    RetrievalHit,
    RetrievalTrace,
    SourceCitation,
)
from pms_retrieval.query import (
    lexical_search_query,
    reciprocal_rank_fusion,
    understand_query,
)
from pms_retrieval.repository import PostgresChunkRepository
from pms_retrieval.reranking import BgeReranker, EvidenceReranker

_TRACE_ROLES = frozenset({UserRole.AUDITOR, UserRole.ADMINISTRATOR})
_PROMPT_INJECTION_TERMS = (
    "ignore previous",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "execute sql",
    "call this tool",
)


class QueryEmbedder(Protocol):
    model: str
    revision: str

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class AuthorizationSafeQueryEmbeddingCache:
    """A bounded process-local cache that never crosses authorization scopes."""

    def __init__(self, settings: Settings) -> None:
        self._max_entries = settings.query_embedding_cache_max_entries
        self._ttl_seconds = settings.query_embedding_cache_ttl_seconds
        self._entries: OrderedDict[str, tuple[float, tuple[float, ...]]] = OrderedDict()
        self._lock = Lock()

    def get_or_embed(
        self,
        embedder: QueryEmbedder,
        normalized_query: str,
        context: AuthorizationContext,
    ) -> tuple[tuple[float, ...], bool]:
        key = self._key(embedder, normalized_query, context)
        now = time.monotonic()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached[0] > now:
                self._entries.move_to_end(key)
                return cached[1], True
            self._entries.pop(key, None)

        vectors = embedder.embed((normalized_query,))
        if len(vectors) != 1:
            raise GenerationError("query embedding count is invalid")
        vector = vectors[0]
        with self._lock:
            self._entries[key] = (now + self._ttl_seconds, vector)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return vector, False

    @staticmethod
    def _key(
        embedder: QueryEmbedder,
        normalized_query: str,
        context: AuthorizationContext,
    ) -> str:
        authorization_scope = "\x1f".join(
            (
                context.subject,
                ",".join(sorted(role.value for role in context.roles)),
                context.tenant_id or "",
                context.department_id or "",
                context.unit_id or "",
                context.classification.value,
            )
        )
        material = "\x1e".join(
            (embedder.model, embedder.revision, normalized_query, authorization_scope)
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class RagRepository(Protocol):
    def lexical_search(
        self,
        query: str,
        limit: int,
        *,
        as_of_date: date,
        document_pattern: str | None,
        additional_document_ids: Sequence[str] = (),
    ) -> tuple[RetrievalHit, ...]: ...

    def dense_search(
        self,
        vector: Sequence[float],
        *,
        model: str,
        revision: str,
        limit: int,
        as_of_date: date,
        document_pattern: str | None,
        additional_document_ids: Sequence[str] = (),
    ) -> tuple[RetrievalHit, ...]: ...

    def parent_chunks(
        self,
        parent_chunk_ids: Sequence[str],
        *,
        as_of_date: date,
    ) -> tuple[RetrievalHit, ...]: ...

    def corpus_status(self) -> CorpusStatus: ...

    def audit(
        self,
        understanding: QueryUnderstanding,
        source_ids: Sequence[str],
        *,
        model_version: str | None,
        result_status: str,
        entity_scope: dict[str, str],
    ) -> None: ...


class PostgresRagRepository:
    """Short transactions around RLS-scoped retrieval and audit writes."""

    def __init__(
        self,
        engine: Engine,
        context: AuthorizationContext,
    ) -> None:
        self._engine = engine
        self._context = context

    def lexical_search(
        self,
        query: str,
        limit: int,
        *,
        as_of_date: date,
        document_pattern: str | None,
        additional_document_ids: Sequence[str] = (),
    ) -> tuple[RetrievalHit, ...]:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(connection, self._context).lexical_search(
                query,
                limit,
                as_of_date=as_of_date,
                document_pattern=document_pattern,
                additional_document_ids=additional_document_ids,
            )

    def dense_search(
        self,
        vector: Sequence[float],
        *,
        model: str,
        revision: str,
        limit: int,
        as_of_date: date,
        document_pattern: str | None,
        additional_document_ids: Sequence[str] = (),
    ) -> tuple[RetrievalHit, ...]:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(
                connection,
                self._context,
            ).exact_vector_search(
                vector,
                model=model,
                revision=revision,
                limit=limit,
                as_of_date=as_of_date,
                document_pattern=document_pattern,
                additional_document_ids=additional_document_ids,
            )

    def parent_chunks(
        self,
        parent_chunk_ids: Sequence[str],
        *,
        as_of_date: date,
    ) -> tuple[RetrievalHit, ...]:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(
                connection,
                self._context,
            ).parent_chunks(parent_chunk_ids, as_of_date=as_of_date)

    def corpus_status(self) -> CorpusStatus:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(connection, self._context).corpus_status()

    def audit(
        self,
        understanding: QueryUnderstanding,
        source_ids: Sequence[str],
        *,
        model_version: str | None,
        result_status: str,
        entity_scope: dict[str, str],
    ) -> None:
        with self._engine.begin() as connection:
            apply_postgres_session_context(connection, self._context)
            write_audit_event(
                connection,
                create_audit_event(
                    self._context,
                    query_category="DOCUMENT_RAG",
                    entity_scope={
                        "query_hash": understanding.query_hash,
                        "as_of_date": understanding.as_of_date.isoformat(),
                        **entity_scope,
                    },
                    source_ids=source_ids,
                    model_version=model_version,
                    result_status=result_status,
                ),
            )


class HybridRagService:
    """Run ACL-first hybrid retrieval and fail-closed grounded generation."""

    def __init__(
        self,
        repository: RagRepository,
        context: AuthorizationContext,
        settings: Settings,
        *,
        embedder: QueryEmbedder | None = None,
        reranker: EvidenceReranker | None = None,
        generator: DraftGenerator | None = None,
        tokenizer: Tokenizer | None = None,
        query_embedding_cache: AuthorizationSafeQueryEmbeddingCache | None = None,
    ) -> None:
        AuthorizationService().require_permission(context, Permission.DOCUMENT_SEARCH)
        self._repository = repository
        self._context = context
        self._settings = settings
        self._embedder = embedder or BgeM3EmbeddingAdapter(settings)
        self._reranker = reranker or BgeReranker(settings)
        self._generator = generator or OllamaGenerator(settings)
        self._tokenizer = tokenizer or BgeM3Tokenizer(settings)
        self._query_embedding_cache = query_embedding_cache or AuthorizationSafeQueryEmbeddingCache(
            settings
        )

    def corpus_status(self) -> CorpusStatus:
        return self._repository.corpus_status()

    def generation_model_state(self) -> str:
        runtime_state = getattr(self._generator, "runtime_state", None)
        if not callable(runtime_state):
            return "unavailable"
        return str(runtime_state(self._settings.llm_primary_model))

    def ask(
        self,
        query: str,
        *,
        response_language: ResponseLanguage = ResponseLanguage.AUTO,
        include_trace: bool = False,
        today: date | None = None,
        lexical_only: bool = False,
        on_token: Callable[[str], None] | None = None,
        additional_document_ids: Sequence[str] = (),
        initial_stage_durations_ms: Mapping[str, float] | None = None,
    ) -> GroundedAnswer:
        started = time.perf_counter()
        durations: dict[str, float] = dict(initial_stage_durations_ms or {})
        understanding = understand_query(
            query,
            today=today,
            response_language=response_language,
            maximum_length=self._settings.query_max_length,
        )
        stage = time.perf_counter()
        lexical_query = lexical_search_query(
            understanding.normalized_query,
            maximum_length=self._settings.query_max_length,
        )
        if additional_document_ids:
            lexical = self._repository.lexical_search(
                lexical_query,
                self._settings.lexical_top_k,
                as_of_date=understanding.as_of_date,
                document_pattern=None,
                additional_document_ids=additional_document_ids,
            )
        else:
            lexical = self._repository.lexical_search(
                lexical_query,
                self._settings.lexical_top_k,
                as_of_date=understanding.as_of_date,
                document_pattern=None,
            )
        if not lexical:
            if additional_document_ids:
                lexical = self._repository.lexical_search(
                    lexical_query,
                    self._settings.lexical_top_k,
                    as_of_date=understanding.as_of_date,
                    document_pattern=None,
                    additional_document_ids=additional_document_ids,
                )
            else:
                lexical = self._repository.lexical_search(
                    lexical_query,
                    self._settings.lexical_top_k,
                    as_of_date=understanding.as_of_date,
                    document_pattern=None,
                )
        durations["lexical"] = _elapsed_ms(stage)

        dense: tuple[RetrievalHit, ...] = ()
        stage = time.perf_counter()
        if lexical_only:
            fused = reciprocal_rank_fusion(
                lexical,
                dense,
                k=self._settings.rrf_k,
                limit=self._settings.rerank_input_top_k,
            )
            reranked = fused[: self._settings.rerank_output_top_k]
            durations["query_embedding"] = 0.0
            durations["query_embedding_cache_hit"] = 0.0
            durations["vector_search"] = 0.0
        else:
            query_vector, cache_hit = self._query_embedding_cache.get_or_embed(
                self._embedder,
                understanding.normalized_query,
                self._context,
            )
            durations["query_embedding"] = _elapsed_ms(stage)
            durations["query_embedding_cache_hit"] = 1.0 if cache_hit else 0.0
            stage = time.perf_counter()
            if additional_document_ids:
                dense = self._repository.dense_search(
                    query_vector,
                    model=self._embedder.model,
                    revision=self._embedder.revision,
                    limit=self._settings.dense_top_k,
                    as_of_date=understanding.as_of_date,
                    document_pattern=None,
                    additional_document_ids=additional_document_ids,
                )
            else:
                dense = self._repository.dense_search(
                    query_vector,
                    model=self._embedder.model,
                    revision=self._embedder.revision,
                    limit=self._settings.dense_top_k,
                    as_of_date=understanding.as_of_date,
                    document_pattern=None,
                )
            durations["vector_search"] = _elapsed_ms(stage)
            stage = time.perf_counter()
            fused = reciprocal_rank_fusion(
                lexical,
                dense,
                k=self._settings.rrf_k,
                limit=self._settings.rerank_input_top_k,
            )
            durations["fusion"] = _elapsed_ms(stage)
            stage = time.perf_counter()
            reranked = self._reranker.rerank(
                understanding.normalized_query,
                fused,
                self._settings.rerank_output_top_k,
            )
        reranked = _prioritize_definition_evidence(lexical_query, reranked)
        reranked = self._deduplicate_translations(reranked)
        if self._settings.retrieval_min_score is not None:
            reranked = tuple(
                item
                for item in reranked
                if item.rerank_score is not None
                and item.rerank_score >= self._settings.retrieval_min_score
            )
        if lexical_only:
            durations["fusion"] = _elapsed_ms(stage)
            durations["reranking"] = 0.0
        else:
            durations["reranking"] = _elapsed_ms(stage)

        stage = time.perf_counter()
        context = self._build_context(reranked, understanding.as_of_date)
        durations["parent_context"] = _elapsed_ms(stage)
        if not context:
            durations["total"] = _elapsed_ms(started)
            answer = self._refusal(understanding, "INSUFFICIENT_AUTHORIZED_EVIDENCE")
            self._audit(
                understanding,
                (),
                reranked,
                durations,
                model_version=None,
                result_status="REVIEW_REQUIRED",
            )
            return answer.model_copy(
                update={
                    "trace": self._trace(
                        understanding,
                        lexical,
                        dense,
                        fused,
                        reranked,
                        context,
                        durations,
                        None,
                        False,
                    )
                    if self._trace_allowed(include_trace)
                    else None
                }
            )

        warnings = list(_context_warnings(context))
        stage = time.perf_counter()
        first_token_recorded = False

        def timed_token(delta: str) -> None:
            nonlocal first_token_recorded
            if not first_token_recorded:
                durations["first_generated_token"] = _elapsed_ms(stage)
                first_token_recorded = True
            if on_token is not None:
                on_token(delta)

        draft, fallback_used = self._generate(
            understanding,
            context,
            on_token=timed_token if on_token is not None else None,
        )
        durations["generation"] = _elapsed_ms(stage)
        durations["citation_validation"] = draft.citation_validation_ms
        durations["total"] = _elapsed_ms(started)
        warnings.extend(draft.warnings)
        trace = self._trace(
            understanding,
            lexical,
            dense,
            fused,
            reranked,
            context,
            durations,
            draft.model,
            fallback_used,
        )
        if draft.review_required:
            result = self._refusal(understanding, "GENERATION_REVIEW_REQUIRED").model_copy(
                update={
                    "warnings": tuple(dict.fromkeys(("GENERATION_REVIEW_REQUIRED", *warnings))),
                    "model": draft.model,
                    "trace": trace if self._trace_allowed(include_trace) else None,
                }
            )
            self._audit(
                understanding,
                tuple(item.chunk_id for item in context),
                reranked,
                durations,
                model_version=draft.model,
                result_status="REVIEW_REQUIRED",
            )
            return result
        source_map = {item.source_id: item for item in context}
        sources = tuple(
            _source_citation(source_map[source_id])
            for source_id in draft.cited_source_ids
            if source_id in source_map
        )
        result = GroundedAnswer(
            answer=draft.answer,
            sources=sources,
            confidence=_confidence(draft.confidence),
            warnings=tuple(dict.fromkeys(warnings)),
            review_required=draft.review_required,
            model=draft.model,
            trace=trace if self._trace_allowed(include_trace) else None,
        )
        self._audit(
            understanding,
            tuple(item.chunk_id for item in context),
            reranked,
            durations,
            model_version=draft.model,
            result_status="REVIEW_REQUIRED" if result.review_required else "ALLOWED",
        )
        return result

    def _audit(
        self,
        understanding: QueryUnderstanding,
        source_ids: Sequence[str],
        candidates: Sequence[RankedEvidence],
        durations: dict[str, float],
        *,
        model_version: str | None,
        result_status: str,
    ) -> None:
        self._repository.audit(
            understanding,
            source_ids,
            model_version=model_version,
            result_status=result_status,
            entity_scope={
                "retrieved_document_ids": ",".join(
                    dict.fromkeys(item.hit.document_id for item in candidates)
                ),
                "stage_durations_ms": json.dumps(
                    {key: round(value, 3) for key, value in durations.items()},
                    sort_keys=True,
                ),
            },
        )

    def _build_context(
        self,
        candidates: tuple[RankedEvidence, ...],
        as_of_date: date,
    ) -> tuple[ContextEvidence, ...]:
        parent_ids = tuple(
            dict.fromkeys(
                item.hit.parent_chunk_id
                for item in candidates
                if item.hit.parent_chunk_id is not None
            )
        )
        parents = {
            item.chunk_id: item
            for item in self._repository.parent_chunks(
                parent_ids,
                as_of_date=as_of_date,
            )
        }
        selected: list[ContextEvidence] = []
        selected_parent_ids: set[str] = set()
        used_tokens = 0
        for candidate in candidates:
            parent_id = candidate.hit.parent_chunk_id
            parent = parents.get(parent_id or "")
            if parent is None:
                continue
            if parent.chunk_id in selected_parent_ids:
                continue
            token_count = self._tokenizer.count(parent.text)
            source = parent
            supporting_text: str | None = candidate.hit.text
            if token_count > self._settings.final_context_max_tokens - used_tokens:
                token_count = self._tokenizer.count(candidate.hit.text)
                if token_count > self._settings.final_context_max_tokens - used_tokens:
                    continue
                source = candidate.hit
                supporting_text = None
            score = (
                candidate.rerank_score
                if candidate.rerank_score is not None
                else candidate.rrf_score
            )
            child_ids = tuple(
                item.hit.chunk_id
                for item in candidates
                if item.hit.parent_chunk_id == parent.chunk_id
            )
            selected.append(
                ContextEvidence(
                    source_id=f"S{len(selected) + 1}",
                    chunk_id=source.chunk_id,
                    child_chunk_ids=child_ids,
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    document_title=source.document_title,
                    text=source.text,
                    supporting_text=supporting_text,
                    token_count=token_count,
                    page_numbers=source.page_numbers,
                    citations=source.citations,
                    heading_path=source.heading_path,
                    section_number=source.section_number,
                    clause_number=source.clause_number,
                    language_code=source.language_code,
                    authoritative_language=source.authoritative_language,
                    effective_from=source.effective_from,
                    effective_to=source.effective_to,
                    score=score,
                )
            )
            selected_parent_ids.add(parent.chunk_id)
            used_tokens += token_count
            if len(selected) >= self._settings.final_context_max_chunks:
                break
        return tuple(selected)

    def _generate(
        self,
        understanding: QueryUnderstanding,
        context: tuple[ContextEvidence, ...],
        *,
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[GeneratedDraft, bool]:
        try:
            stream_generator = getattr(self._generator, "generate_stream", None)
            if on_token is not None and callable(stream_generator):
                primary = stream_generator(
                    understanding.normalized_query,
                    context,
                    response_language=understanding.response_language,
                    model=self._settings.llm_primary_model,
                    on_token=on_token,
                )
            else:
                primary = self._generator.generate(
                    understanding.normalized_query,
                    context,
                    response_language=understanding.response_language,
                    model=self._settings.llm_primary_model,
                )
        except GenerationError:
            primary = None
        if primary is not None and not primary.review_required:
            return primary, False
        if primary is not None:
            return primary, False
        return GeneratedDraft(
            answer=_refusal_text(understanding.response_language),
            cited_source_ids=(),
            evidence_quotes={},
            confidence="LOW",
            warnings=("GENERATION_VALIDATION_FAILED",),
            review_required=True,
            model=self._settings.llm_primary_model,
        ), False

    def _deduplicate_translations(
        self,
        candidates: tuple[RankedEvidence, ...],
    ) -> tuple[RankedEvidence, ...]:
        selected: dict[str, RankedEvidence] = {}
        ungrouped: list[RankedEvidence] = []
        for item in candidates:
            group = item.hit.translation_group_id
            if group is None:
                ungrouped.append(item)
                continue
            current = selected.get(group)
            if current is None or _translation_preference(item) > _translation_preference(current):
                selected[group] = item
        combined = (*ungrouped, *selected.values())
        return tuple(
            sorted(
                combined,
                key=lambda item: (
                    -_candidate_score(item),
                    -item.rrf_score,
                    item.hit.chunk_id,
                ),
            )
        )

    def _refusal(
        self,
        understanding: QueryUnderstanding,
        warning: str,
    ) -> GroundedAnswer:
        return GroundedAnswer(
            answer=_refusal_text(understanding.response_language),
            confidence="LOW",
            warnings=(warning,),
            review_required=True,
        )

    def _trace(
        self,
        understanding: QueryUnderstanding,
        lexical: tuple[RetrievalHit, ...],
        dense: tuple[RetrievalHit, ...],
        fused: tuple[RankedEvidence, ...],
        reranked: tuple[RankedEvidence, ...],
        context: tuple[ContextEvidence, ...],
        durations: dict[str, float],
        generation_model: str | None,
        fallback_used: bool,
    ) -> RetrievalTrace:
        return RetrievalTrace(
            correlation_id=get_request_id(),
            query_hash=understanding.query_hash,
            as_of_date=understanding.as_of_date,
            lexical_candidates=len(lexical),
            dense_candidates=len(dense),
            fused_candidates=len(fused),
            reranked_candidates=len(reranked),
            context_chunks=len(context),
            context_tokens=sum(item.token_count for item in context),
            selected_chunk_ids=tuple(item.chunk_id for item in context),
            embedding_model=f"{self._embedder.model}@{self._embedder.revision}",
            reranker_model=f"{self._reranker.model}@{self._reranker.revision}",
            generation_model=generation_model,
            fallback_used=fallback_used,
            durations_ms={key: round(value, 3) for key, value in durations.items()},
        )

    def _trace_allowed(self, requested: bool) -> bool:
        return requested and bool(self._context.roles.intersection(_TRACE_ROLES))


def _source_citation(item: ContextEvidence) -> SourceCitation:
    return SourceCitation(
        source_id=item.source_id,
        document_id=item.document_id,
        document_version_id=item.document_version_id,
        document_title=item.document_title,
        page_numbers=item.page_numbers,
        section_number=item.section_number,
        clause_number=item.clause_number,
        citations=item.citations,
    )


def _context_warnings(context: tuple[ContextEvidence, ...]) -> tuple[str, ...]:
    if any(term in item.text.casefold() for item in context for term in _PROMPT_INJECTION_TERMS):
        return ("UNTRUSTED_EVIDENCE_INSTRUCTION_IGNORED",)
    return ()


def _translation_preference(item: RankedEvidence) -> tuple[int, float]:
    authoritative = int(
        item.hit.authoritative_language is not None
        and item.hit.language_code == item.hit.authoritative_language
    )
    return authoritative, _candidate_score(item)


def _candidate_score(item: RankedEvidence) -> float:
    return item.rerank_score if item.rerank_score is not None else item.rrf_score


def _prioritize_definition_evidence(
    lexical_query: str,
    candidates: tuple[RankedEvidence, ...],
) -> tuple[RankedEvidence, ...]:
    """Keep an exact definition hit ahead of title/index matches.

    The deterministic lexical rewrite emits ``<term> defined`` only for an explicit
    definition question. In that narrow case, lexical rank is the stronger signal for
    the substantive definition; the cross-encoder remains the tie-breaker.
    """

    if not lexical_query.casefold().endswith(" defined"):
        return candidates
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.lexical_rank is None,
                item.lexical_rank or 10**9,
                -_candidate_score(item),
            ),
        )
    )


def _confidence(value: str) -> Literal["HIGH", "MEDIUM", "LOW"]:
    if value in {"HIGH", "MEDIUM", "LOW"}:
        return cast(Literal["HIGH", "MEDIUM", "LOW"], value)
    return "LOW"


def _refusal_text(language: ResponseLanguage) -> str:
    if language is ResponseLanguage.HINDI:
        return "उपलब्ध अधिकृत साक्ष्य उत्तर देने के लिए पर्याप्त नहीं हैं। समीक्षा आवश्यक है।"
    if language is ResponseLanguage.MARATHI:
        return "उपलब्ध अधिकृत पुरावा उत्तरासाठी पुरेसा नाही. पुनरावलोकन आवश्यक आहे."
    return "The available authorized evidence is insufficient. Review is required."


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
