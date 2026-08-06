from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts/evaluation/phase10_pdf_rule_evidence.json"


def test_pdf_evidence_artifact_is_complete_and_unapproved() -> None:
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    summary = payload["summary"]
    documents = payload["documents"]
    candidates = payload["evidence_candidates"]

    pdfs = tuple((ROOT / "data/inbox").rglob("*.pdf"))
    assert summary["pdf_count"] == len(pdfs)
    assert summary["parsed_pdf_count"] + summary["failure_count"] == len(pdfs)
    assert summary["parsed_pdf_count"] == len(documents)
    assert summary["evidence_candidate_count"] == len(candidates)
    assert summary["page_count"] == sum(item["page_count"] for item in documents)
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
