"""Deterministic discovery of page-cited rule evidence in local PDFs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

RULE_TERMS = {
    "rent": re.compile(r"\b(?:rent|rental|lease rent|compensation)\b", re.IGNORECASE),
    "tax": re.compile(
        r"\b(?:tax|taxes|cess|GST|service tax|property tax|WBT|SBT|EGC|ED)\b",
        re.IGNORECASE,
    ),
    "interest": re.compile(r"\binterest\b", re.IGNORECASE),
}
CONTEXT_TERMS = re.compile(
    r"\b(?:effective|with effect|applicable|valid from|escalat\w*|"
    r"revision|formula|calculation|rate|per annum|per month)\b",
    re.IGNORECASE,
)
EXACT_TOKEN = re.compile(
    r"(?:₹|Rs\.?|`)\s*[\d,]+(?:\.\d+)?|"
    r"\b\d+(?:\.\d+)?\s*%|"
    r"\b(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-](?:19|20)\d{2}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PageEvidence:
    """One exact, normalized, page-level excerpt awaiting human review."""

    source_path: str
    document_sha256: str
    page_number: int
    candidate_family: str
    excerpt: str
    excerpt_sha256: str
    exact_tokens: tuple[str, ...]
    matched_terms: tuple[str, ...]

    @property
    def candidate_id(self) -> str:
        return (
            f"pdf:{self.document_sha256[:20]}:"
            f"p{self.page_number}:{self.candidate_family}"
        )


def normalize_text(value: str) -> str:
    """Normalize layout whitespace without changing characters or wording."""

    return " ".join(value.split())


def evidence_families(text: str) -> tuple[str, ...]:
    """Return only families explicitly named on a page."""

    return tuple(name for name, pattern in RULE_TERMS.items() if pattern.search(text))


def exact_tokens(text: str) -> tuple[str, ...]:
    """Preserve exact monetary, percentage, and full-date tokens."""

    return tuple(dict.fromkeys(match.group(0) for match in EXACT_TOKEN.finditer(text)))


def bounded_excerpt(text: str, *, maximum_characters: int = 1800) -> str:
    """Keep exact statement windows around rate/applicability terms."""

    normalized = normalize_text(text)
    matches = list(CONTEXT_TERMS.finditer(normalized))
    if not matches:
        return normalized[:maximum_characters]
    windows: list[str] = []
    used: set[tuple[int, int]] = set()
    for match in matches[:8]:
        start = max(0, match.start() - 180)
        end = min(len(normalized), match.end() + 320)
        key = (start, end)
        if key not in used:
            used.add(key)
            windows.append(normalized[start:end])
        if sum(len(item) for item in windows) >= maximum_characters:
            break
    return " [...] ".join(windows)[:maximum_characters]


def discover_page_evidence(
    *,
    source_path: str,
    document_sha256: str,
    page_number: int,
    page_text: str,
) -> tuple[PageEvidence, ...]:
    """Create no candidate unless a page states a family and an exact token."""

    normalized = normalize_text(page_text)
    families = evidence_families(normalized)
    tokens = exact_tokens(normalized)
    if not families or not tokens or not CONTEXT_TERMS.search(normalized):
        return ()
    excerpt = bounded_excerpt(normalized)
    excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    matched = tuple(
        term
        for term in (
            "effective",
            "applicable",
            "escalation",
            "revision",
            "formula",
            "calculation",
            "rate",
            "per annum",
            "per month",
        )
        if re.search(rf"\b{re.escape(term)}\b", normalized, re.IGNORECASE)
    )
    return tuple(
        PageEvidence(
            source_path=source_path,
            document_sha256=document_sha256,
            page_number=page_number,
            candidate_family=family,
            excerpt=excerpt,
            excerpt_sha256=excerpt_hash,
            exact_tokens=tokens,
            matched_terms=matched,
        )
        for family in families
    )


def eligible_source(path: Path, inbox: Path) -> bool:
    """Restrict candidate discovery to policy/rate/circular source groups."""

    relative = path.relative_to(inbox)
    return relative.parts[0].lower() in {
        "sor",
        "tax formulas",
        "policies",
        "circulars",
    }
