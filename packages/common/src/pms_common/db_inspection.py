"""Metadata-only inspection and verification of the extraction schema."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import Engine, text

EXPECTED_TABLE_COUNT = 61
EXPECTED_SQL_PATH = Path("sql/extraction/04_ONE_CLICK_CLEAN_BUILD_AND_SUMMARY_V2_SAFE_DATES.sql")
_CREATE_TABLE_PATTERN = re.compile(
    r'CREATE\s+TABLE\s+"pms_extract_2010_2023"\."([^"]+)"',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ColumnMetadata:
    """One information-schema column record without row data."""

    table_name: str
    column_name: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    character_maximum_length: int | None
    numeric_precision: int | None
    numeric_scale: int | None


@dataclass(frozen=True, slots=True)
class DatabaseInspection:
    """Safe metadata report used by CLI verification and repository allowlists."""

    database_name: str
    connected_user: str
    postgres_version: str
    extraction_schema: str
    schema_exists: bool
    expected_table_count: int
    base_table_count: int
    expected_tables: tuple[str, ...]
    actual_tables: tuple[str, ...]
    missing_critical_tables: tuple[str, ...]
    unexpected_tables: tuple[str, ...]
    columns: tuple[ColumnMetadata, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable metadata-only representation."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Database health metadata without business data."""

    database_name: str
    connected_user: str
    postgres_version: str


def load_expected_tables(root: Path) -> tuple[str, ...]:
    """Load the expected extraction tables from the reviewed one-click SQL."""

    sql_path = root / EXPECTED_SQL_PATH
    names = tuple(sorted(set(_CREATE_TABLE_PATTERN.findall(sql_path.read_text(encoding="utf-8")))))
    if len(names) != EXPECTED_TABLE_COUNT:
        raise ValueError(
            f"expected {EXPECTED_TABLE_COUNT} extraction tables in {EXPECTED_SQL_PATH}; "
            f"found {len(names)}"
        )
    return names


def inspect_database(
    engine: Engine,
    *,
    extraction_schema: str,
    expected_tables: tuple[str, ...],
) -> DatabaseInspection:
    """Read PostgreSQL metadata only; never select business-table values."""

    with engine.connect() as connection:
        identity = connection.execute(
            text(
                "SELECT current_database() AS database_name, "
                "current_user AS connected_user, "
                "current_setting('server_version') AS postgres_version"
            )
        ).mappings().one()
        schema_exists = bool(
            connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = :schema_name"
                    ")"
                ),
                {"schema_name": extraction_schema},
            ).scalar_one()
        )
        table_rows = connection.execute(
            text(
                "SELECT table_name "
                "FROM information_schema.tables "
                "WHERE table_schema = :schema_name "
                "AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ),
            {"schema_name": extraction_schema},
        ).scalars()
        actual_tables = tuple(table_rows)
        column_rows = connection.execute(
            text(
                "SELECT table_name, column_name, ordinal_position, data_type, is_nullable, "
                "character_maximum_length, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema_name "
                "ORDER BY table_name, ordinal_position"
            ),
            {"schema_name": extraction_schema},
        ).mappings()
        columns = tuple(
            ColumnMetadata(
                table_name=str(row["table_name"]),
                column_name=str(row["column_name"]),
                ordinal_position=int(row["ordinal_position"]),
                data_type=str(row["data_type"]),
                is_nullable=row["is_nullable"] == "YES",
                character_maximum_length=row["character_maximum_length"],
                numeric_precision=row["numeric_precision"],
                numeric_scale=row["numeric_scale"],
            )
            for row in column_rows
        )

    expected_set = set(expected_tables)
    actual_set = set(actual_tables)
    return DatabaseInspection(
        database_name=str(identity["database_name"]),
        connected_user=str(identity["connected_user"]),
        postgres_version=str(identity["postgres_version"]),
        extraction_schema=extraction_schema,
        schema_exists=schema_exists,
        expected_table_count=len(expected_tables),
        base_table_count=len(actual_tables),
        expected_tables=expected_tables,
        actual_tables=actual_tables,
        missing_critical_tables=tuple(sorted(expected_set - actual_set)),
        unexpected_tables=tuple(sorted(actual_set - expected_set)),
        columns=columns,
    )


def verify_inspection(inspection: DatabaseInspection) -> tuple[str, ...]:
    """Return deterministic gate failures for a metadata inspection."""

    issues: list[str] = []
    major_text = inspection.postgres_version.split(".", 1)[0]
    try:
        postgres_major = int(major_text)
    except ValueError:
        issues.append("PostgreSQL major version could not be parsed")
    else:
        if postgres_major < 17:
            issues.append(f"PostgreSQL 17 or newer required; found {postgres_major}")
    if not inspection.schema_exists:
        issues.append(f"schema {inspection.extraction_schema} does not exist")
    if inspection.expected_table_count != EXPECTED_TABLE_COUNT:
        issues.append(
            f"expected-table configuration must contain {EXPECTED_TABLE_COUNT} tables"
        )
    if inspection.base_table_count != EXPECTED_TABLE_COUNT:
        issues.append(
            f"expected {EXPECTED_TABLE_COUNT} base tables; found {inspection.base_table_count}"
        )
    if inspection.missing_critical_tables:
        issues.append(
            "missing critical tables: " + ", ".join(inspection.missing_critical_tables)
        )
    return tuple(issues)


def write_inventory(inspection: DatabaseInspection, output_path: Path) -> None:
    """Write the metadata-only inventory as deterministic JSON."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(inspection.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def check_database_health(engine: Engine) -> DatabaseHealth:
    """Return connectivity and server identity without reading business data."""

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_database() AS database_name, "
                "current_user AS connected_user, "
                "current_setting('server_version') AS postgres_version"
            )
        ).mappings().one()
    return DatabaseHealth(
        database_name=str(row["database_name"]),
        connected_user=str(row["connected_user"]),
        postgres_version=str(row["postgres_version"]),
    )

