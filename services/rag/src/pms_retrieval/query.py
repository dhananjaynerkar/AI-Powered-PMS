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


def normalize_query(query: str) -> str:
    """Normalize presentation differences without stemming legal terms."""

    normalized = _SPACE.sub(" ", unicodedata.normalize("NFKC", query)).strip()
    if not normalized:
        raise ValueError("query must not be blank")
    if len(normalized) > 2000:
        raise ValueError("query must not exceed 2000 characters")
    return normalized


def understand_query(
    query: str,
    *,
    today: date | None = None,
    response_language: ResponseLanguage = ResponseLanguage.AUTO,
) -> QueryUnderstanding:
    normalized = normalize_query(query)
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
