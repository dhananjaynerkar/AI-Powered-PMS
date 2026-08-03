"""Deterministic semantic-catalog profiling for extracted tables and governed views."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Connection, text


class MetadataEmbedder(Protocol):
    model: str
    revision: str

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True, slots=True)
class CatalogRefreshResult:
    extracted_tables: int
    extracted_columns: int
    governed_views: int
    governed_columns: int
    metadata_embeddings: int
    join_paths: int


_SENSITIVE = re.compile(
    r"(aadhaar|aadhar|pan(?:_no|_number)?|password|secret|token|otp|"
    r"mobile|phone|email|session|full_address|postal_address)",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(
    r"(^id$|_id$|^id_|_code$|^code$|_no$|_number$|_ref$|_key$)",
    re.IGNORECASE,
)
_DATE = re.compile(r"(date|timestamp|^year$|_year$|valid_from|valid_to|periodfrom|periodto)")
_NAME = re.compile(r"(name|title|owner)")
_NARRATIVE = re.compile(
    r"(remark|description|purpose|observation|opinion|breach|ground|clause|"
    r"note|comment|reason|summary|detail|text|narrative|reservation)"
)
_NUMERIC_TYPES = frozenset(
    {
        "smallint",
        "integer",
        "bigint",
        "numeric",
        "decimal",
        "real",
        "double precision",
        "money",
    }
)


def classify_column(column_name: str, data_type: str) -> tuple[str, bool, bool]:
    """Return semantic class, sensitivity, and value-embedding eligibility."""

    normalized = column_name.casefold()
    sensitive = bool(_SENSITIVE.search(normalized))
    if sensitive:
        return "sensitive", True, False
    if _IDENTIFIER.search(normalized):
        return "identifier", False, False
    if _DATE.search(normalized) or data_type in {
        "date",
        "timestamp with time zone",
        "timestamp without time zone",
        "time with time zone",
        "time without time zone",
    }:
        return "date", False, False
    if _NARRATIVE.search(normalized):
        return "narrative", False, True
    if _NAME.search(normalized):
        return "name", False, False
    if data_type in _NUMERIC_TYPES:
        return "measure", False, False
    return "category", False, False


def _description(identifier: str) -> str:
    return identifier.replace("_", " ").strip().capitalize()


def _vector_literal(values: Sequence[float]) -> str:
    normalized = tuple(float(value) for value in values)
    if len(normalized) != 1024 or not all(math.isfinite(value) for value in normalized):
        raise ValueError("catalog embedding must contain 1024 finite values")
    return "[" + ",".join(format(value, ".9g") for value in normalized) + "]"


class SemanticCatalogBuilder:
    """Refresh metadata only; no extracted row values are embedded."""

    def __init__(
        self,
        *,
        extract_schema: str,
        governed_views: Sequence[str],
    ) -> None:
        self._extract_schema = extract_schema
        self._governed_views = tuple(governed_views)

    def refresh(
        self,
        connection: Connection,
        *,
        embedder: MetadataEmbedder | None = None,
    ) -> CatalogRefreshResult:
        rows = connection.execute(
            text(
                """
                SELECT cols.table_schema, cols.table_name, cols.column_name,
                       cols.ordinal_position, cols.data_type,
                       CASE
                         WHEN cols.table_schema = :extract_schema
                           THEN 'extracted_table'
                         ELSE 'governed_view'
                       END AS table_kind,
                       COALESCE(relation.reltuples::bigint, 0) AS estimated_rows
                FROM information_schema.columns AS cols
                LEFT JOIN pg_namespace AS namespace
                  ON namespace.nspname = cols.table_schema
                LEFT JOIN pg_class AS relation
                  ON relation.relnamespace = namespace.oid
                 AND relation.relname = cols.table_name
                WHERE cols.table_schema = :extract_schema
                   OR (
                     cols.table_schema = 'pms_app'
                     AND cols.table_name = ANY(:governed_views)
                   )
                ORDER BY cols.table_schema, cols.table_name,
                         cols.ordinal_position
                """
            ),
            {
                "extract_schema": self._extract_schema,
                "governed_views": list(self._governed_views),
            },
        ).mappings().all()
        grouped: dict[tuple[str, str, str, int], list[dict[str, object]]] = {}
        for row in rows:
            key = (
                str(row["table_schema"]),
                str(row["table_name"]),
                str(row["table_kind"]),
                int(row["estimated_rows"]),
            )
            grouped.setdefault(key, []).append(dict(row))

        connection.execute(text("DELETE FROM pms_catalog.semantic_table"))
        profiled_at = datetime.now(UTC)
        search_documents: list[tuple[str, str]] = []
        extracted_columns = 0
        governed_columns = 0
        for (schema, table_name, table_kind, estimated_rows), columns in grouped.items():
            catalog_table_id = f"{schema}.{table_name}"
            classified: list[tuple[dict[str, object], str, bool, bool]] = []
            for column in columns:
                semantic_class, sensitive, embedding_eligible = classify_column(
                    str(column["column_name"]),
                    str(column["data_type"]),
                )
                classified.append(
                    (column, semantic_class, sensitive, embedding_eligible)
                )
            search_text = (
                f"{_description(table_name)}. Schema {schema}. "
                + "Columns: "
                + ", ".join(
                    f"{item[0]['column_name']} ({item[1]})" for item in classified
                )
            )
            approved = table_kind == "governed_view"
            connection.execute(
                text(
                    """
                    INSERT INTO pms_catalog.semantic_table (
                      catalog_table_id, source_schema, source_table, table_kind,
                      business_description, row_count, freshness_at, search_text,
                      approved_for_query, profiled_at
                    ) VALUES (
                      :catalog_table_id, :source_schema, :source_table, :table_kind,
                      :description, :row_count, NULL, :search_text,
                      :approved, :profiled_at
                    )
                    """
                ),
                {
                    "catalog_table_id": catalog_table_id,
                    "source_schema": schema,
                    "source_table": table_name,
                    "table_kind": table_kind,
                    "description": _description(table_name),
                    "row_count": max(0, estimated_rows),
                    "search_text": search_text,
                    "approved": approved,
                    "profiled_at": profiled_at,
                },
            )
            for column, semantic_class, sensitive, embedding_eligible in classified:
                column_name = str(column["column_name"])
                connection.execute(
                    text(
                        """
                        INSERT INTO pms_catalog.semantic_column (
                          catalog_column_id, catalog_table_id, source_schema,
                          source_table, source_column, ordinal_position, data_type,
                          semantic_class, sensitive, embedding_eligible,
                          approved_for_query, business_description
                        ) VALUES (
                          :catalog_column_id, :catalog_table_id, :source_schema,
                          :source_table, :source_column, :ordinal_position, :data_type,
                          :semantic_class, :sensitive, :embedding_eligible,
                          :approved, :description
                        )
                        """
                    ),
                    {
                        "catalog_column_id": f"{catalog_table_id}.{column_name}",
                        "catalog_table_id": catalog_table_id,
                        "source_schema": schema,
                        "source_table": table_name,
                        "source_column": column_name,
                        "ordinal_position": int(str(column["ordinal_position"])),
                        "data_type": str(column["data_type"]),
                        "semantic_class": semantic_class,
                        "sensitive": sensitive,
                        "embedding_eligible": embedding_eligible,
                        "approved": approved and not sensitive,
                        "description": _description(column_name),
                    },
                )
            if table_kind == "extracted_table":
                extracted_columns += len(columns)
            else:
                governed_columns += len(columns)
            search_documents.append((catalog_table_id, search_text))

        embedded = self._embed_metadata(connection, search_documents, embedder)
        join_paths = self._refresh_join_paths(connection)
        extracted_tables = sum(key[2] == "extracted_table" for key in grouped)
        governed_views = sum(key[2] == "governed_view" for key in grouped)
        return CatalogRefreshResult(
            extracted_tables=extracted_tables,
            extracted_columns=extracted_columns,
            governed_views=governed_views,
            governed_columns=governed_columns,
            metadata_embeddings=embedded,
            join_paths=join_paths,
        )

    @staticmethod
    def _embed_metadata(
        connection: Connection,
        documents: Sequence[tuple[str, str]],
        embedder: MetadataEmbedder | None,
    ) -> int:
        if embedder is None:
            return 0
        count = 0
        batch_size = 8
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            vectors = embedder.embed(tuple(item[1] for item in batch))
            if len(vectors) != len(batch):
                raise ValueError("catalog embedding count does not match metadata count")
            for (catalog_table_id, _), vector in zip(batch, vectors, strict=True):
                connection.execute(
                    text(
                        """
                        UPDATE pms_catalog.semantic_table
                        SET embedding_model = :model,
                            embedding_revision = :revision,
                            embedding = CAST(:embedding AS pms_vector.vector)
                        WHERE catalog_table_id = :catalog_table_id
                        """
                    ),
                    {
                        "model": embedder.model,
                        "revision": embedder.revision,
                        "embedding": _vector_literal(vector),
                        "catalog_table_id": catalog_table_id,
                    },
                )
                count += 1
        return count

    def _refresh_join_paths(self, connection: Connection) -> int:
        measurements = (
            (
                "applicant_tenancy_to_applicant",
                "bridge_applicant_tenancy",
                "applicant_id",
                "dim_applicant_safe",
                "applicant_id",
                "many_to_one",
                True,
                "Integer key types align; live coverage is measured.",
            ),
            (
                "applicant_tenancy_to_billable_tenancy",
                "bridge_applicant_tenancy",
                "tenancy_id",
                "dim_billable_tenancy",
                "tenancy_id",
                "many_to_one_optional",
                True,
                "Text key types align; unmatched legacy records remain visible as absent.",
            ),
            (
                "billable_tenancy_to_property_lease",
                "dim_billable_tenancy",
                "tenancy_id",
                "dim_property_lease",
                "tenancy_id",
                "one_to_many",
                True,
                "Text key types align and live coverage is measured.",
            ),
            (
                "monthly_bill_to_current_payment",
                "fact_monthly_bills",
                "bill_code",
                "fact_payment_current",
                "bill_code",
                "unapproved",
                False,
                "Measured coverage is insufficient for an authoritative bill-payment join.",
            ),
            (
                "inspection_to_breach",
                "fact_inspection",
                "inspection_rpt_id",
                "fact_breach",
                "inspection_rpt_id",
                "unapproved",
                False,
                "Measured coverage is insufficient for an authoritative inspection-breach join.",
            ),
        )
        connection.execute(text("DELETE FROM pms_catalog.join_path"))
        for (
            path_id,
            left_table,
            left_column,
            right_table,
            right_column,
            relationship,
            approved,
            reason,
        ) in measurements:
            counts = connection.execute(
                text(
                    f"""
                    SELECT count(*) AS left_rows,
                           count(*) FILTER (
                             WHERE EXISTS (
                               SELECT 1
                               FROM {self._extract_schema}.{right_table} AS right_side
                               WHERE right_side.{right_column} = left_side.{left_column}
                             )
                           ) AS matched_rows
                    FROM {self._extract_schema}.{left_table} AS left_side
                    WHERE left_side.{left_column} IS NOT NULL
                    """
                )
            ).mappings().one()
            left_rows = int(counts["left_rows"])
            matched_rows = int(counts["matched_rows"])
            ratio = matched_rows / left_rows if left_rows else 0
            connection.execute(
                text(
                    """
                    INSERT INTO pms_catalog.join_path (
                      join_path_id, left_schema, left_table, left_column,
                      right_schema, right_table, right_column, relationship,
                      measured_left_rows, measured_matched_rows, match_ratio,
                      approved, review_reason
                    ) VALUES (
                      :join_path_id, :schema, :left_table, :left_column,
                      :schema, :right_table, :right_column, :relationship,
                      :left_rows, :matched_rows, :ratio, :approved, :reason
                    )
                    """
                ),
                {
                    "join_path_id": path_id,
                    "schema": self._extract_schema,
                    "left_table": left_table,
                    "left_column": left_column,
                    "right_table": right_table,
                    "right_column": right_column,
                    "relationship": relationship,
                    "left_rows": left_rows,
                    "matched_rows": matched_rows,
                    "ratio": ratio,
                    "approved": approved,
                    "reason": reason,
                },
            )
        return len(measurements)
