from __future__ import annotations

import hashlib

from pms_common.text import (
    assert_no_nul_characters,
    remove_nul_characters,
    sanitize_nested_strings,
)


def test_removes_nul_from_english() -> None:
    assert remove_nul_characters("po\x00rt") == "port"


def test_removes_nul_from_devanagari_without_other_changes() -> None:
    assert remove_nul_characters("वर\x00ष्ठ पोर्ट") == "वरष्ठ पोर्ट"


def test_sanitizes_nested_json_metadata() -> None:
    value = {"heading_path": ["Sec\x00tion"], "box": {"label": "पोट\x00 ट्रस्ट"}}
    assert sanitize_nested_strings(value) == {
        "heading_path": ["Section"],
        "box": {"label": "पोट ट्रस्ट"},
    }


def test_clean_text_is_unchanged() -> None:
    value = "Normal Hindi हिन्दी and Marathi मराठी text."
    assert remove_nul_characters(value) == value


def test_hash_uses_sanitized_chunk_text() -> None:
    sanitized = remove_nul_characters("a\x00b")
    assert hashlib.sha256(sanitized.encode()).hexdigest() == hashlib.sha256(b"ab").hexdigest()


def test_persistence_guard_rejects_nul() -> None:
    try:
        assert_no_nul_characters({"text": "bad\x00value"})
    except ValueError:
        pass
    else:
        raise AssertionError("NUL persistence guard did not reject input")
