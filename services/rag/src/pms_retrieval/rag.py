"""Secure Phase 08 hybrid retrieval and grounded RAG orchestration."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from datetime import date
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
    GroundedAnswer,
    QueryUnderstanding,
    RankedEvidence,
    ResponseLanguage,
    RetrievalHit,
    RetrievalTrace,
    SourceCitation,
)
from pms_retrieval.query import (
    document_pattern,
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


class RagRepository(Protocol):
    def lexical_search(
        self,
        query: str,
        limit: int,
        *,
        as_of_date: date,
        document_pattern: str | None,
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
    ) -> tuple[RetrievalHit, ...]: ...

    def parent_chunks(
        self,
        parent_chunk_ids: Sequence[str],
        *,
        as_of_date: date,
    ) -> tuple[RetrievalHit, ...]: ...

    def audit(
        self,
        understanding: QueryUnderstanding,
        source_ids: Sequence[str],
        *,
        model_version: str | None,
        result_status: str,
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
    ) -> tuple[RetrievalHit, ...]:
        with self._engine.begin() as connection:
            return PostgresChunkRepository(connection, self._context).lexical_search(
                query,
                limit,
                as_of_date=as_of_date,
                document_pattern=document_pattern,
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

    def audit(
        self,
        understanding: QueryUnderstanding,
        source_ids: Sequence[str],
        *,
        model_version: str | None,
        result_status: str,
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
    ) -> None:
        AuthorizationService().require_permission(context, Permission.DOCUMENT_SEARCH)
        self._repository = repository
        self._context = context
        self._settings = settings
        self._embedder = embedder or BgeM3EmbeddingAdapter(settings)
        self._reranker = reranker or BgeReranker(settings)
        self._generator = generator or OllamaGenerator(settings)
        self._tokenizer = tokenizer or BgeM3Tokenizer(settings)

    def ask(
        self,
        query: str,
        *,
        response_language: ResponseLanguage = ResponseLanguage.AUTO,
        include_trace: bool = False,
        today: date | None = None,
        use_extractive_fallback: bool = False,
    ) -> GroundedAnswer:
        started = time.perf_counter()
        durations: dict[str, float] = {}
        understanding = understand_query(
            query,
            today=today,
            response_language=response_language,
        )
        pattern = document_pattern(understanding.document_type)

        stage = time.perf_counter()
        lexical = self._repository.lexical_search(
            understanding.normalized_query,
            self._settings.lexical_top_k,
            as_of_date=understanding.as_of_date,
            document_pattern=pattern,
        )
        durations["lexical"] = _elapsed_ms(stage)

        stage = time.perf_counter()
        query_vectors = self._embedder.embed((understanding.normalized_query,))
        if len(query_vectors) != 1:
            raise GenerationError("query embedding count is invalid")
        dense = self._repository.dense_search(
            query_vectors[0],
            model=self._embedder.model,
            revision=self._embedder.revision,
            limit=self._settings.dense_top_k,
            as_of_date=understanding.as_of_date,
            document_pattern=pattern,
        )
        durations["dense"] = _elapsed_ms(stage)

        stage = time.perf_counter()
        fused = reciprocal_rank_fusion(
            lexical,
            dense,
            k=self._settings.rrf_k,
            limit=self._settings.rerank_input_top_k,
        )
        reranked = self._reranker.rerank(
            understanding.normalized_query,
            fused,
            self._settings.rerank_output_top_k,
        )
        reranked = self._deduplicate_translations(reranked)
        if self._settings.retrieval_min_score is not None:
            reranked = tuple(
                item
                for item in reranked
                if item.rerank_score is not None
                and item.rerank_score >= self._settings.retrieval_min_score
            )
        durations["fusion_rerank"] = _elapsed_ms(stage)

        stage = time.perf_counter()
        context = self._build_context(reranked, understanding.as_of_date)
        durations["parent_context"] = _elapsed_ms(stage)
        if not context:
            answer = self._refusal(understanding, "INSUFFICIENT_AUTHORIZED_EVIDENCE")
            self._repository.audit(
                understanding,
                (),
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
        draft, fallback_used = self._generate(
            understanding,
            context,
            use_extractive_fallback=use_extractive_fallback,
        )
        durations["generation"] = _elapsed_ms(stage)
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
                    "warnings": tuple(
                        dict.fromkeys(("GENERATION_REVIEW_REQUIRED", *warnings))
                    ),
                    "model": draft.model,
                    "trace": trace if self._trace_allowed(include_trace) else None,
                }
            )
            self._repository.audit(
                understanding,
                tuple(item.chunk_id for item in context),
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
        self._repository.audit(
            understanding,
            tuple(item.chunk_id for item in context),
            model_version=draft.model,
            result_status="REVIEW_REQUIRED" if result.review_required else "ALLOWED",
        )
        return result

    def answer_verified_extractive_evidence(
        self,
        query: str,
        *,
        document_id: str,
        document_version_id: str,
        parent_chunk_id: str,
        child_chunk_id: str,
    ) -> GroundedAnswer:
        """Return a cited extract after an RLS-scoped lookup of one reviewed chunk.

        Callers must limit this to a fixed, reviewed demonstration question. It is not a
        general document-answering route and deliberately does not use an embedding model,
        reranker or LLM.
        """

        understanding = understand_query(query)
        hits = self._repository.lexical_search(
            understanding.normalized_query,
            10,
            as_of_date=understanding.as_of_date,
            document_pattern=None,
        )
        child = next(
            (
                item
                for item in hits
                if item.chunk_id == child_chunk_id
                and item.parent_chunk_id == parent_chunk_id
                and item.document_id == document_id
                and item.document_version_id == document_version_id
            ),
            None,
        )
        if child is None:
            return self._verified_evidence_refusal(understanding)
        parent = next(
            (
                item
                for item in self._repository.parent_chunks(
                    (parent_chunk_id,), as_of_date=understanding.as_of_date
                )
                if item.chunk_id == parent_chunk_id
                and item.document_id == document_id
                and item.document_version_id == document_version_id
            ),
            None,
        )
        if parent is None:
            return self._verified_evidence_refusal(understanding)
        context = ContextEvidence(
            source_id="S1",
            chunk_id=parent.chunk_id,
            child_chunk_ids=(child.chunk_id,),
            document_id=parent.document_id,
            document_version_id=parent.document_version_id,
            document_title=parent.document_title,
            text=parent.text,
            supporting_text=child.text,
            token_count=self._tokenizer.count(parent.text),
            page_numbers=parent.page_numbers,
            citations=parent.citations,
            heading_path=parent.heading_path,
            section_number=parent.section_number,
            clause_number=parent.clause_number,
            language_code=parent.language_code,
            authoritative_language=parent.authoritative_language,
            effective_from=parent.effective_from,
            effective_to=parent.effective_to,
            score=child.score,
        )
        draft = _extractive_evidence_draft((context,))
        result = GroundedAnswer(
            answer=draft.answer,
            sources=(_source_citation(context),),
            confidence="MEDIUM",
            warnings=("VERIFIED_EXTRACTIVE_DEMO_EVIDENCE",),
            review_required=False,
            model=draft.model,
        )
        self._repository.audit(
            understanding,
            (child.chunk_id, parent.chunk_id),
            model_version=draft.model,
            result_status="ALLOWED",
        )
        return result

    def _verified_evidence_refusal(self, understanding: QueryUnderstanding) -> GroundedAnswer:
        result = self._refusal(understanding, "INSUFFICIENT_AUTHORIZED_EVIDENCE")
        self._repository.audit(
            understanding,
            (),
            model_version=None,
            result_status="REVIEW_REQUIRED",
        )
        return result

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
        used_tokens = 0
        for candidate in candidates:
            parent_id = candidate.hit.parent_chunk_id
            parent = parents.get(parent_id or "")
            if parent is None:
                continue
            if any(item.chunk_id == parent.chunk_id for item in selected):
                continue
            token_count = self._tokenizer.count(parent.text)
            if token_count > self._settings.final_context_max_tokens - used_tokens:
                continue
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
                    chunk_id=parent.chunk_id,
                    child_chunk_ids=child_ids,
                    document_id=parent.document_id,
                    document_version_id=parent.document_version_id,
                    document_title=parent.document_title,
                    text=parent.text,
                    supporting_text=candidate.hit.text,
                    token_count=token_count,
                    page_numbers=parent.page_numbers,
                    citations=parent.citations,
                    heading_path=parent.heading_path,
                    section_number=parent.section_number,
                    clause_number=parent.clause_number,
                    language_code=parent.language_code,
                    authoritative_language=parent.authoritative_language,
                    effective_from=parent.effective_from,
                    effective_to=parent.effective_to,
                    score=score,
                )
            )
            used_tokens += token_count
            if len(selected) >= self._settings.final_context_max_chunks:
                break
        return tuple(selected)

    def _generate(
        self,
        understanding: QueryUnderstanding,
        context: tuple[ContextEvidence, ...],
        *,
        use_extractive_fallback: bool,
    ) -> tuple[GeneratedDraft, bool]:
        try:
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
        if primary is None and use_extractive_fallback:
            return _extractive_evidence_draft(context), True
        if (
            self._settings.llm_allow_fallback
            and understanding.difficult
            and self._fallback_available()
        ):
            try:
                fallback = self._generator.generate(
                    understanding.normalized_query,
                    context,
                    response_language=understanding.response_language,
                    model=self._settings.llm_fallback_model,
                )
                return fallback, True
            except GenerationError:
                pass
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

    def _fallback_available(self) -> bool:
        try:
            return self._settings.llm_fallback_model in self._generator.available_models()
        except GenerationError:
            return False

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
            if current is None or _translation_preference(item) > _translation_preference(
                current
            ):
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


def _extractive_evidence_draft(
    context: tuple[ContextEvidence, ...],
) -> GeneratedDraft:
    """Return one readable sentence copied only from the highest-ranked evidence.

    This is intentionally opt-in at the caller. It is for a pre-registered demo
    question when local generation is unavailable, not a general replacement for
    grounded generation.
    """

    source = context[0]
    text = " ".join((source.supporting_text or source.text).split())
    sentences = tuple(
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.split()) >= 8
    )
    excerpt = sentences[0] if sentences else " ".join(text.split()[:48])
    quote = " ".join(text.split()[:24])
    if len(quote.split()) < 8:
        raise GenerationError("extractive fallback evidence is too short")
    return GeneratedDraft(
        answer=f"Evidence-extracted: {excerpt} [{source.source_id}]",
        cited_source_ids=(source.source_id,),
        evidence_quotes={source.source_id: quote},
        confidence="MEDIUM",
        warnings=("EXTRACTIVE_EVIDENCE_FALLBACK", "LOCAL_GENERATION_UNAVAILABLE"),
        review_required=False,
        model="extractive-evidence-v1",
    )


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
    if any(
        term in item.text.casefold()
        for item in context
        for term in _PROMPT_INJECTION_TERMS
    ):
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
