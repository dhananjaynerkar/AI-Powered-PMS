"""Structured JSON logging with correlation IDs and mandatory redaction."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

REDACTED: Final = "[REDACTED]"
OMITTED: Final = "[OMITTED]"
_REQUEST_ID: ContextVar[str] = ContextVar("pms_request_id", default="-")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "aadhaar",
    "pan",
    "otp",
    "session",
}
_SENSITIVE_KEY_PREFIXES = (
    "password",
    "passwd",
    "secret",
    "authorization",
    "cookie",
    "aadhaar",
    "pan",
    "otp",
    "session",
)
_SENSITIVE_KEY_SUFFIXES = (
    "_password",
    "_passwd",
    "_secret",
    "_token",
    "_authorization",
    "_cookie",
    "_api_key",
    "_aadhaar",
    "_pan",
    "_otp",
    "_session",
)
_OMITTED_KEYS = {
    "prompt",
    "model_prompt",
    "llm_input",
    "retrieved_text",
    "retrieved_document_text",
    "document_text",
    "context_text",
}
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)

_TEXT_PATTERNS = (
    (
        re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
        "[REDACTED_AADHAAR]",
    ),
    (
        re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE),
        "[REDACTED_PAN]",
    ),
    (
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        "[REDACTED_EMAIL]",
    ),
    (
        re.compile(
            r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)"
            r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"
        ),
        "[REDACTED_IP]",
    ),
    (
        re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)"),
        "[REDACTED_PHONE]",
    ),
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        "Bearer [REDACTED_TOKEN]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED_TOKEN]",
    ),
)
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<password>[^@\s]+)@",
    re.IGNORECASE,
)


def ensure_request_id(candidate: str | None = None) -> str:
    """Accept a bounded safe request ID or generate a UUID."""

    if candidate is not None and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def get_request_id() -> str:
    """Return the current correlation ID."""

    return _REQUEST_ID.get()


def set_request_id(request_id: str | None = None) -> Token[str]:
    """Set a safe correlation ID and return a reset token."""

    return _REQUEST_ID.set(ensure_request_id(request_id))


def reset_request_id(token: Token[str]) -> None:
    """Restore the previous correlation ID."""

    _REQUEST_ID.reset(token)


@contextmanager
def request_id_context(request_id: str | None = None) -> Iterator[str]:
    """Scope a request ID to one operation."""

    token = set_request_id(request_id)
    try:
        yield get_request_id()
    finally:
        reset_request_id(token)


def redact_text(value: str) -> str:
    """Redact common secret and PII forms from unstructured text."""

    redacted = _URL_CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@",
        value,
    )
    redacted = _INLINE_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}={REDACTED}", redacted)
    for pattern, replacement in _TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _is_omitted_key(key: str) -> bool:
    return (
        key in _OMITTED_KEYS
        or key.endswith("_prompt")
        or key.startswith("prompt_")
        or key.startswith("retrieved_")
    )


def _is_sensitive_key(key: str) -> bool:
    return (
        key in _SENSITIVE_KEYS
        or key.startswith(tuple(f"{prefix}_" for prefix in _SENSITIVE_KEY_PREFIXES))
        or key.endswith(_SENSITIVE_KEY_SUFFIXES)
    )


def redact_value(value: object, key: str | None = None) -> object:
    """Recursively sanitize values before serialization."""

    normalized_key = key.lower().replace("-", "_") if key is not None else None
    if normalized_key is not None and _is_omitted_key(normalized_key):
        return OMITTED
    if normalized_key is not None and _is_sensitive_key(normalized_key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


class JsonFormatter(logging.Formatter):
    """Serialize a sanitized log record as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": get_request_id(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["fields"] = redact_value(extras)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger for sanitized JSON output."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
