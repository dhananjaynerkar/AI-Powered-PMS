"""Focused offline tests for Phase 03 database components."""

from pathlib import Path
from typing import cast

import pytest
from pms_common.database import build_database_url, database_is_configured
from pms_common.db_inspection import (
    EXPECTED_TABLE_COUNT,
    ColumnMetadata,
    DatabaseInspection,
    load_expected_tables,
    verify_inspection,
)
from pms_common.settings import Settings
from pms_common.source_repository import ReadOnlySourceRepository, SourceQueryError
from sqlalchemy import Engine


def test_reviewed_extraction_sql_defines_exactly_61_tables() -> None:
    root = Path(__file__).parents[2]
    tables = load_expected_tables(root)
    assert len(tables) == EXPECTED_TABLE_COUNT
    assert len(set(tables)) == EXPECTED_TABLE_COUNT


def test_database_url_uses_psycopg_without_rendering_password() -> None:
    settings = Settings(
        _env_file=None,
        database_url=None,
        postgres_password="phase03-secret",
    )
    url = build_database_url(settings)
    assert url.drivername == "postgresql+psycopg"
    assert "phase03-secret" not in str(url)
    assert database_is_configured(settings)


def test_verify_inspection_accepts_phase_gate_metadata() -> None:
    expected = tuple(f"table_{number}" for number in range(EXPECTED_TABLE_COUNT))
    inspection = DatabaseInspection(
        database_name="pms",
        connected_user="pms_readonly",
        postgres_version="17.5",
        extraction_schema="pms_extract_2010_2023",
        schema_exists=True,
        expected_table_count=EXPECTED_TABLE_COUNT,
        base_table_count=EXPECTED_TABLE_COUNT,
        expected_tables=expected,
        actual_tables=expected,
        missing_critical_tables=(),
        unexpected_tables=(),
        columns=(),
    )
    assert verify_inspection(inspection) == ()


def _source_repository() -> ReadOnlySourceRepository:
    inspection = DatabaseInspection(
        database_name="pms",
        connected_user="pms_readonly",
        postgres_version="17.5",
        extraction_schema="pms_extract_2010_2023",
        schema_exists=True,
        expected_table_count=1,
        base_table_count=1,
        expected_tables=("customers",),
        actual_tables=("customers",),
        missing_critical_tables=(),
        unexpected_tables=(),
        columns=(
            ColumnMetadata(
                table_name="customers",
                column_name="customer_id",
                ordinal_position=1,
                data_type="integer",
                is_nullable=False,
                character_maximum_length=None,
                numeric_precision=32,
                numeric_scale=0,
            ),
        ),
    )
    return ReadOnlySourceRepository(cast(Engine, object()), inspection=inspection)


@pytest.mark.parametrize(
    ("table_name", "columns", "limit"),
    [
        ("unknown", ["customer_id"], 10),
        ("customers", [], 10),
        ("customers", ["unknown_column"], 10),
        ("customers", ["customer_id"], 501),
    ],
)
def test_source_repository_rejects_non_allowlisted_queries(
    table_name: str,
    columns: list[str],
    limit: int,
) -> None:
    with pytest.raises(SourceQueryError):
        _source_repository().fetch_rows(
            table_name=table_name,
            columns=columns,
            limit=limit,
        )
