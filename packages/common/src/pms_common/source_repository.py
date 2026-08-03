"""Generic read-only access to explicitly allowlisted extraction tables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import Engine, MetaData, Table, select

from pms_common.db_inspection import DatabaseInspection

MAX_SOURCE_ROWS = 500


class SourceQueryError(ValueError):
    """Raised when a source query violates the inspected allowlist."""


class ReadOnlySourceRepository:
    """Select named columns from inspected source tables through a read-only engine."""

    def __init__(
        self,
        engine: Engine,
        *,
        inspection: DatabaseInspection,
    ) -> None:
        self._engine = engine
        self._schema = inspection.extraction_schema
        self._columns_by_table: dict[str, frozenset[str]] = {}
        for column in inspection.columns:
            existing = self._columns_by_table.get(column.table_name, frozenset())
            self._columns_by_table[column.table_name] = existing | {column.column_name}

    def fetch_rows(
        self,
        *,
        table_name: str,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Fetch bounded rows with validated identifiers and parameterized values."""

        if table_name not in self._columns_by_table:
            raise SourceQueryError(f"table is not in the inspected allowlist: {table_name}")
        if not columns:
            raise SourceQueryError("at least one explicit column is required")
        if not 1 <= limit <= MAX_SOURCE_ROWS:
            raise SourceQueryError(f"limit must be between 1 and {MAX_SOURCE_ROWS}")

        allowed_columns = self._columns_by_table[table_name]
        requested = set(columns)
        filter_values = {} if filters is None else dict(filters)
        unknown = (requested | set(filter_values)) - allowed_columns
        if unknown:
            raise SourceQueryError("unknown columns: " + ", ".join(sorted(unknown)))

        table = Table(
            table_name,
            MetaData(),
            schema=self._schema,
            autoload_with=self._engine,
        )
        statement = select(*(table.c[name] for name in columns)).limit(limit)
        for name, value in filter_values.items():
            statement = statement.where(table.c[name] == value)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return [dict(row) for row in rows]

