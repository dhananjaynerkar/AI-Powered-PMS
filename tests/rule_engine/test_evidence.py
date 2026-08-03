from __future__ import annotations

from pms_rule_engine.evidence import (
    bounded_excerpt,
    discover_page_evidence,
    evidence_families,
    exact_tokens,
)


def test_page_evidence_requires_named_family_context_and_exact_token() -> None:
    evidence = discover_page_evidence(
        source_path="SOR/test.pdf",
        document_sha256="a" * 64,
        page_number=4,
        page_text=(
            "The rent rate is 6% per annum and will increase by 4% annually "
            "with effect from 01.10.2017."
        ),
    )

    assert len(evidence) == 1
    assert evidence[0].candidate_family == "rent"
    assert evidence[0].page_number == 4
    assert evidence[0].exact_tokens == ("6%", "4%", "01.10.2017")
    assert evidence[0].candidate_id == f"pdf:{'a' * 20}:p4:rent"


def test_multiple_explicit_families_remain_separate_unapproved_evidence() -> None:
    evidence = discover_page_evidence(
        source_path="SOR/test.pdf",
        document_sha256="b" * 64,
        page_number=5,
        page_text=(
            "Property tax is 10% of annual rent and interest is 15% per annum."
        ),
    )

    assert {item.candidate_family for item in evidence} == {
        "rent",
        "tax",
        "interest",
    }


def test_non_numeric_or_context_free_text_is_not_a_candidate() -> None:
    assert evidence_families("This mentions rent.") == ("rent",)
    assert exact_tokens("No exact value is stated.") == ()
    assert (
        discover_page_evidence(
            source_path="policy.pdf",
            document_sha256="c" * 64,
            page_number=1,
            page_text="This document discusses rent policy generally.",
        )
        == ()
    )


def test_excerpt_is_bounded_without_rewriting_tokens() -> None:
    text = "prefix " * 100 + "rate is 18% per annum effective 01.01.1992 " + "suffix " * 100
    excerpt = bounded_excerpt(text, maximum_characters=500)

    assert len(excerpt) <= 500
    assert "18%" in excerpt
    assert "01.01.1992" in excerpt
