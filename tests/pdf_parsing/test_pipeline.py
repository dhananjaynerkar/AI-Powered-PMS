from __future__ import annotations

from pms_common.settings import Settings
from pms_ingestion.parsing import BoundingBox, CanonicalDocument, ParsingEngine

from tests.pdf_parsing.support import FakeAdapter, FakeVerifier, metadata, output


def test_quality_failure_routes_to_bounded_fallback() -> None:
    primary = FakeAdapter(
        "primary",
        output(parser="primary").model_copy(
            update={
                "pages": (
                    output(parser="primary").pages[0].model_copy(
                        update={
                            "blocks": (
                                output(parser="primary")
                                .pages[0]
                                .blocks[0]
                                .model_copy(update={"bounding_box": None}),
                            )
                        }
                    ),
                )
            }
        ),
    )
    fallback = FakeAdapter("fallback", output(parser="fallback"))
    engine = ParsingEngine(
        Settings(_env_file=None),
        (primary, fallback),
        FakeVerifier(),
    )

    result = engine.run(metadata(), b"%PDF-1.7\n%%EOF")

    assert result.selected_parser == "fallback"
    assert result.fallback_used is True
    assert result.canonical is not None
    assert len(result.raw_outputs) == 2
    assert result.final_status == "canonicalized"


def test_transient_retries_are_bounded() -> None:
    adapter = FakeAdapter("primary", failures=2)
    settings = Settings(_env_file=None, pdf_max_retries=2)

    result = ParsingEngine(settings, (adapter,), FakeVerifier()).run(
        metadata(),
        b"%PDF-1.7\n%%EOF",
    )

    assert result.canonical is not None
    assert adapter.calls == 3
    assert [attempt.result for attempt in result.attempts].count("transient_error") == 2


def test_all_parsers_failing_requires_review_without_unbounded_retry() -> None:
    first = FakeAdapter("first", permanent_error=True)
    second = FakeAdapter("second", permanent_error=True)

    result = ParsingEngine(
        Settings(_env_file=None, pdf_max_retries=2),
        (first, second),
        FakeVerifier(),
    ).run(metadata(), b"%PDF-1.7\n%%EOF")

    assert result.canonical is None
    assert result.final_status == "review_required"
    assert first.calls == 1
    assert second.calls == 1


def test_low_ocr_confidence_routes_to_review() -> None:
    parsed = output(
        parser="ocr",
        ocr_confidence=0.5,
        bbox=BoundingBox(left=1, bottom=1, right=10, top=10),
    )
    result = ParsingEngine(
        Settings(_env_file=None),
        (FakeAdapter("ocr", parsed),),
        FakeVerifier(),
    ).run(metadata(), b"%PDF-1.7\n%%EOF")

    assert result.canonical is None
    assert result.final_status == "review_required"


def test_canonical_json_round_trip_preserves_nullable_contract_fields() -> None:
    result = ParsingEngine(
        Settings(_env_file=None),
        (FakeAdapter("primary", output(parser="primary")),),
        FakeVerifier(),
    ).run(metadata(), b"%PDF-1.7\n%%EOF")

    assert result.canonical is not None
    persisted = result.canonical.model_dump_json()
    restored = CanonicalDocument.model_validate_json(persisted)

    assert restored == result.canonical
