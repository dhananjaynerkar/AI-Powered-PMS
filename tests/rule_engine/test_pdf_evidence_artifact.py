from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts/evaluation/phase10_pdf_rule_evidence.json"


def test_pdf_evidence_artifact_is_complete_and_unapproved() -> None:
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    summary = payload["summary"]
    candidates = payload["evidence_candidates"]

    assert summary["pdf_count"] == 94
    assert summary["parsed_pdf_count"] == 94
    assert summary["failure_count"] == 0
    assert summary["page_count"] == 3231
    assert summary["evidence_candidate_count"] == 310
    assert len(candidates) == 310
    assert {item["candidate_family"] for item in candidates} == {
        "rent",
        "tax",
        "interest",
    }
    assert all(item["candidate_status"] == "unapproved" for item in candidates)
    assert all(item["interpretation"] is None for item in candidates)
    assert all(len(item["document_sha256"]) == 64 for item in candidates)
    assert all(item["page_number"] >= 1 for item in candidates)
    assert all(item["exact_tokens"] for item in candidates)
