from __future__ import annotations

from pathlib import Path

from pms_common.migration_safety import validate_migration_source
from pms_common.settings import Settings
from pms_ingestion.parser_adapters import (
    _is_structured_table,
    _json_safe_payload,
    _normalize_docling,
    _normalize_opendataloader,
    default_parser_adapters,
)

ROOT = Path(__file__).parents[2]
MIGRATION = (
    ROOT
    / "db"
    / "migrations"
    / "versions"
    / "20260729_0005_document_parsing_status.py"
)


def test_phase06_migration_only_expands_application_status_constraint() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    validate_migration_source(source)
    assert 'down_revision: str | None = "20260729_0004"' in source
    assert "pms_extract_2010_2023" not in source
    assert "public." not in source
    assert 'SCHEMA = "pms_doc"' in source
    assert "review_required" in source
    assert "canonicalized" in source
    assert "chunk_ready" in source
    assert "indexed" in source


def test_phase06_settings_are_typed_and_bounded() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")

    assert settings.pdf_primary_parser == "opendataloader"
    assert settings.pdf_primary_mode == "deterministic"
    assert settings.pdf_max_retries == 2
    assert settings.pdf_citation_bbox_required is True
    assert settings.pdf_numeric_token_exact_match_required is True
    assert settings.canonical_schema_version == "1.0"


def test_production_parser_composition_is_opendataloader_only() -> None:
    adapters = default_parser_adapters(Settings(_env_file=None))

    assert tuple(adapter.name for adapter in adapters) == ("opendataloader",)


def test_table_verifier_rejects_wrapped_prose_and_accepts_dense_cells() -> None:
    prose_regions = [[None, "wrapped paragraph"], [None, "continued paragraph"]]
    dense_cells = [["Header A", "Header B"], ["Value A", "Value B"]]

    assert _is_structured_table(prose_regions) is False
    assert _is_structured_table(dense_cells) is True


def test_opendataloader_projection_uses_page_and_bbox_contract() -> None:
    output = _normalize_opendataloader(
        {
            "file name": "controlled.pdf",
            "number of pages": 1,
            "kids": [
                {
                    "id": 1,
                    "type": "heading",
                    "page number": 1,
                    "bounding box": [10, 20, 200, 50],
                    "heading level": 1,
                    "content": "Controlled heading",
                }
            ],
        },
        "# Controlled heading",
    )

    assert output.page_count == 1
    assert output.pages[0].blocks[0].text == "Controlled heading"
    assert output.pages[0].blocks[0].bounding_box is not None


def test_docling_projection_uses_current_text_and_table_collections() -> None:
    output = _normalize_docling(
        {
            "pages": {
                "1": {
                    "page_no": 1,
                    "size": {"width": 612, "height": 792},
                }
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": "section_header",
                    "text": "Controlled heading",
                    "level": 1,
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {
                                "l": 10,
                                "b": 700,
                                "r": 200,
                                "t": 730,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                }
            ],
            "tables": [
                {
                    "self_ref": "#/tables/0",
                    "label": "table",
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {
                                "l": 10,
                                "b": 500,
                                "r": 300,
                                "t": 650,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                    "data": {
                        "num_rows": 2,
                        "num_cols": 2,
                        "table_cells": [
                            {
                                "text": "Rate",
                                "start_row_offset_idx": 0,
                                "start_col_offset_idx": 0,
                                "column_header": True,
                            },
                            {
                                "text": "18%",
                                "start_row_offset_idx": 1,
                                "start_col_offset_idx": 0,
                            },
                        ],
                    },
                }
            ],
        },
        "# Controlled heading",
        "test",
    )

    heading, table = output.pages[0].blocks
    assert heading.kind.value == "heading"
    assert heading.heading_level == 1
    assert table.kind.value == "table"
    assert table.table_rows == 2
    assert table.table_columns == 2
    assert table.table_has_header is True
    assert "18%" in table.text


def test_parser_byte_payload_is_losslessly_json_serializable() -> None:
    payload = _json_safe_payload({"image": b"\xff\x00"})

    assert payload == {"image": {"encoding": "base64", "data": "/wA="}}
