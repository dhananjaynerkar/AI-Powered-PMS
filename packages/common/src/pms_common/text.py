"""Unicode-safe text normalization shared by ingestion and persistence."""

from __future__ import annotations

from typing import Any


def remove_nul_characters(value: str | None) -> str | None:
    """Remove only PostgreSQL-incompatible U+0000 characters."""

    if value is None:
        return None
    return value.replace("\x00", "")


def sanitize_nested_strings(value: Any) -> Any:
    """Recursively sanitize strings in JSON-compatible metadata."""

    if isinstance(value, str):
        return remove_nul_characters(value)
    if isinstance(value, list):
        return [sanitize_nested_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_nested_strings(item) for item in value)
    if isinstance(value, dict):
        return {sanitize_nested_strings(k): sanitize_nested_strings(v) for k, v in value.items()}
    return value


def assert_no_nul_characters(value: Any) -> None:
    """Raise before a PostgreSQL text/JSON value can contain U+0000."""

    if isinstance(value, str) and "\x00" in value:
        raise ValueError("NUL character reached PostgreSQL persistence")
    if isinstance(value, (list, tuple)):
        for item in value:
            assert_no_nul_characters(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            assert_no_nul_characters(key)
            assert_no_nul_characters(item)
