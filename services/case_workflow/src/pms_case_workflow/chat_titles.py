"""Deterministic chat-title derivation; no generation call is needed."""

from __future__ import annotations

import re


def title_from_first_question(question: str, *, max_length: int = 80) -> str:
    """Turn the first user question into a compact, stable display title."""

    normalized = re.sub(r"\s+", " ", question).strip()
    if not normalized:
        return "New Chat"
    if len(normalized) <= max_length:
        return normalized
    shortened = normalized[: max_length - 1].rsplit(" ", 1)[0].strip()
    return f"{shortened}…" if shortened else f"{normalized[: max_length - 1]}…"
