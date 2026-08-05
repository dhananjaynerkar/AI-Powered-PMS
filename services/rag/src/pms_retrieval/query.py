"""Deterministic query understanding and reciprocal-rank fusion."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime

from pms_retrieval.models import (
    QueryUnderstanding,
    RankedEvidence,
    ResponseLanguage,
    RetrievalHit,
)

_SPACE = re.compile(r"\s+")
_ISO_DATE = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
_DMY_DATE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/](?:19|20)\d{2}\b")
_ENTITY = re.compile(
    r"\b(?:customer|tenant|tenancy|plot|lease|document)"
    r"[\s:#-]*[A-Za-z0-9][A-Za-z0-9._/-]{1,63}\b",
    re.IGNORECASE,
)
_DOCUMENT_TYPES = {
    "office order": "%office%order%",
    "circular": "%circular%",
    "agreement": "%agreement%",
    "act": "%act%",
    "notice": "%notice%",
    "policy": "%policy%",
    "manual": "%manual%",
}
_DIFFICULT_TERMS = frozenset(
    {
        "amend",
        "effective",
        "compare",
        "conflict",
        "supersed",
        "retrospective",
        "historical",
    }
)
_HINDI_MARKERS = frozenset({"क्या", "कब", "कौन", "कानून", "धारा", "आदेश", "नियम"})
_MARATHI_MARKERS = frozenset({"काय", "केव्हा", "कोण", "कायदा", "कलम", "आदेश", "नियम"})
_LEXICAL_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_DEFINITION_QUESTION = re.compile(
    r"\bwhat\s+(?:is|are)\s+(?:(?:an?|the)\s+)?(?P<term>[^?.,;:]{2,80})",
    re.IGNORECASE,
)
_LEXICAL_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "according",
        "an",
        "and",
        "based",
        "does",
        "document",
        "explain",
        "for",
        "from",
        "give",
        "in",
        "indexed",
        "is",
        "me",
        "of",
        "on",
        "pdf",
        "please",
        "say",
        "summarize",
        "the",
        "to",
        "under",
        "what",
    }
)


def normalize_query(query: str, *, maximum_length: int = 2000) -> str:
    """Normalize presentation differences without stemming legal terms."""

    if maximum_length < 1:
        raise ValueError("maximum query length must be positive")
    normalized = _SPACE.sub(" ", unicodedata.normalize("NFKC", query)).strip()
    if not normalized:
        raise ValueError("query must not be blank")
    if len(normalized) > maximum_length:
        raise ValueError(f"query must not exceed {maximum_length} characters")
    return normalized


def understand_query(
    query: str,
    *,
    today: date | None = None,
    response_language: ResponseLanguage = ResponseLanguage.AUTO,
    maximum_length: int = 2000,
) -> QueryUnderstanding:
    normalized = normalize_query(query, maximum_length=maximum_length)
    mentioned_dates = _extract_dates(normalized)
    as_of_date = mentioned_dates[-1] if mentioned_dates else (today or date.today())
    lowered = normalized.casefold()
    document_type = next(
        (name for name in _DOCUMENT_TYPES if name in lowered),
        None,
    )
    resolved_language = (
        _detect_response_language(normalized)
        if response_language is ResponseLanguage.AUTO
        else response_language
    )
    difficult = (
        bool(mentioned_dates)
        or any(term in lowered for term in _DIFFICULT_TERMS)
        or resolved_language in {ResponseLanguage.HINDI, ResponseLanguage.MARATHI}
    )
    return QueryUnderstanding(
        normalized_query=normalized,
        query_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        as_of_date=as_of_date,
        mentioned_dates=mentioned_dates,
        entity_references=tuple(match.group(0) for match in _ENTITY.finditer(normalized)),
        document_type=document_type,
        response_language=resolved_language,
        difficult=difficult,
    )


def lexical_search_query(
    query: str,
    *,
    maximum_terms: int = 12,
    maximum_length: int = 2000,
) -> str:
    """Build a bounded PostgreSQL web-search query from conversational text.

    Dense retrieval and reranking still receive the complete normalized question. This
    rewrite only prevents filler words from turning lexical retrieval into an
    accidental all-terms match that returns no candidates.
    """

    normalized = normalize_query(query, maximum_length=maximum_length)
    definition = _DEFINITION_QUESTION.search(normalized)
    if definition is not None:
        subject_tokens = tuple(
            token.casefold()
            for token in _LEXICAL_TOKEN.findall(definition.group("term"))
            if token.casefold() not in _LEXICAL_STOP_WORDS
        )
        if subject_tokens:
            return " ".join((*subject_tokens[:4], "defined"))

    terms = tuple(
        dict.fromkeys(
            token.casefold()
            for token in _LEXICAL_TOKEN.findall(normalized)
            if token.casefold() not in _LEXICAL_STOP_WORDS
        )
    )[:maximum_terms]
    if not terms:
        return normalized
    return " OR ".join(terms)


def document_pattern(document_type: str | None) -> str | None:
    return _DOCUMENT_TYPES.get(document_type or "")


def reciprocal_rank_fusion(
    lexical: tuple[RetrievalHit, ...],
    dense: tuple[RetrievalHit, ...],
    *,
    k: int,
    limit: int,
) -> tuple[RankedEvidence, ...]:
    if k < 1 or limit < 1:
        raise ValueError("RRF k and limit must be positive")
    hits_by_id: dict[str, RetrievalHit] = {}
    lexical_ranks: dict[str, int] = {}
    dense_ranks: dict[str, int] = {}
    scores: dict[str, float] = {}
    for source, hits in (("lexical", lexical), ("dense", dense)):
        for rank, hit in enumerate(hits, start=1):
            hits_by_id.setdefault(hit.chunk_id, hit)
            if source == "lexical":
                lexical_ranks[hit.chunk_id] = rank
            else:
                dense_ranks[hit.chunk_id] = rank
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
    ranked = (
        RankedEvidence(
            hit=hit,
            lexical_rank=lexical_ranks.get(chunk_id),
            dense_rank=dense_ranks.get(chunk_id),
            rrf_score=scores[chunk_id],
        )
        for chunk_id, hit in hits_by_id.items()
    )
    return tuple(
        sorted(
            ranked,
            key=lambda item: (-item.rrf_score, item.hit.chunk_id),
        )[:limit]
    )


def _extract_dates(query: str) -> tuple[date, ...]:
    values: list[date] = []
    for raw in _ISO_DATE.findall(query):
        try:
            values.append(date.fromisoformat(raw))
        except ValueError:
            continue
    for raw in _DMY_DATE.findall(query):
        separator = "/" if "/" in raw else "-"
        try:
            values.append(datetime.strptime(raw, f"%d{separator}%m{separator}%Y").date())
        except ValueError:
            continue
    return tuple(dict.fromkeys(values))


def _detect_response_language(query: str) -> ResponseLanguage:
    words = frozenset(re.findall(r"[\u0900-\u097f]+", query))
    hindi = len(words & _HINDI_MARKERS)
    marathi = len(words & _MARATHI_MARKERS)
    if hindi > marathi:
        return ResponseLanguage.HINDI
    if marathi > hindi:
        return ResponseLanguage.MARATHI
    return ResponseLanguage.ENGLISH
