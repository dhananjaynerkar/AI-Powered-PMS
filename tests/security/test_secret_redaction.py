from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID

from pms_common.logging import (
    OMITTED,
    REDACTED,
    JsonFormatter,
    get_request_id,
    redact_text,
    redact_value,
    request_id_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_sensitive_mapping_keys_are_redacted_recursively() -> None:
    value = {
        "user": "operator",
        "password": "secret-value",
        "nested": {"access_token": "token-value", "safe": "visible"},
    }

    sanitized = redact_value(value)

    assert sanitized == {
        "user": "operator",
        "password": REDACTED,
        "nested": {"access_token": REDACTED, "safe": "visible"},
    }


def test_innocent_keys_containing_pan_or_token_are_not_redacted() -> None:
    sanitized = redact_value({"company": "Port Authority", "token_count": 420})

    assert sanitized == {"company": "Port Authority", "token_count": 420}


def test_prompt_and_retrieved_text_are_omitted() -> None:
    sanitized = redact_value(
        {
            "user_prompt": "ignore previous instructions",
            "retrieved_chunks": ["restricted clause"],
            "event": "retrieval_complete",
        }
    )

    assert sanitized == {
        "user_prompt": OMITTED,
        "retrieved_chunks": OMITTED,
        "event": "retrieval_complete",
    }


def test_unstructured_pii_and_credentials_are_redacted() -> None:
    message = (
        "email officer@example.com phone +919876543210 aadhaar 1234 5678 9012 "
        "PAN ABCDE1234F ip 192.168.1.25 password=hunter2 "
        "url postgresql://user:dbpass@localhost/pms"
    )

    sanitized = redact_text(message)

    for sensitive in (
        "officer@example.com",
        "9876543210",
        "1234 5678 9012",
        "ABCDE1234F",
        "192.168.1.25",
        "hunter2",
        "dbpass",
    ):
        assert sensitive not in sanitized


def test_request_id_context_accepts_safe_value_and_resets() -> None:
    original = get_request_id()

    with request_id_context("request-123") as request_id:
        assert request_id == "request-123"
        assert get_request_id() == "request-123"

    assert get_request_id() == original


def test_invalid_request_id_is_replaced_by_uuid() -> None:
    with request_id_context("unsafe request id\n") as request_id:
        assert UUID(request_id)


def test_json_formatter_emits_sanitized_valid_json() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="pms.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="contact officer@example.com",
        args=(),
        exc_info=None,
    )
    record.password = "never-log-this"
    record.prompt = "hidden prompt"

    with request_id_context("request-456"):
        payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "request-456"
    assert payload["message"] == "contact [REDACTED_EMAIL]"
    assert payload["fields"]["password"] == REDACTED
    assert payload["fields"]["prompt"] == OMITTED
    assert "never-log-this" not in json.dumps(payload)


def test_gitignore_covers_secrets_environment_and_document_corpus() -> None:
    rules = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {
        ".env",
        ".env.*",
        "!.env.example",
        ".venv/",
        "data/inbox/*",
        "!data/inbox/README.md",
        "artifacts/",
        "logs/",
    } <= rules
