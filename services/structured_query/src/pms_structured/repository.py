"""PostgreSQL execution boundary for approved structured queries."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pms_common.security import (
    AuthorizationContext,
    AuthorizationService,
    Permission,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from pms_common.settings import Settings
from sqlalchemy import Connection, text

from pms_structured.models import (
    CatalogTableMatch,
    ConstrainedSelectPlan,
    EntityDomain,
    SourceProvenance,
    StructuredRecord,
)
from pms_structured.templates import (
    ApprovedTemplate,
    ApprovedTemplateRegistry,
    ConstrainedPlanCompiler,
)


class StructuredQueryError(RuntimeError):
    """Raised when governed execution cannot safely produce facts."""


class QueryCostExceeded(StructuredQueryError):
    """Raised before execution when PostgreSQL estimates an excessive plan."""


class PostgresStructuredRepository:
    """Execute fixed templates after RLS context, EXPLAIN, and bounded controls."""

    _provenance_fields = frozenset(
        {"source_schema", "source_table", "source_record_id", "source_refreshed_at"}
    )

    def __init__(
        self,
        connection: Connection,
        context: AuthorizationContext,
        settings: Settings,
        templates: ApprovedTemplateRegistry,
    ) -> None:
        self._connection = connection
        self._context = context
        self._settings = settings
        self._templates = templates
        self._authorization = AuthorizationService()
        apply_postgres_session_context(connection, context)

    def resolve_identity(
        self,
        domain: EntityDomain,
        *,
        source_schema: str,
        source_table: str,
        source_record_id: str,
    ) -> str | None:
        """Resolve a reviewed semantic hit to an ACL-visible canonical ID."""

        self._authorization.require_permission(
            self._context,
            Permission.TENANT_RECORD_READ,
        )
        return self._connection.execute(
            text(
                """
                SELECT canonical_entity_id
                FROM pms_catalog.entity_identity_map
                WHERE entity_type = :entity_type
                  AND source_schema = :source_schema
                  AND source_table = :source_table
                  AND source_record_id = :source_record_id
                  AND active
                """
            ),
            {
                "entity_type": domain.value,
                "source_schema": source_schema,
                "source_table": source_table,
                "source_record_id": source_record_id,
            },
        ).scalar_one_or_none()

    def execute(
        self,
        template_id: str,
        *,
        canonical_entity_id: str | None,
        as_of_date: date | None,
        limit: int,
    ) -> tuple[StructuredRecord, ...]:
        self._authorization.require_permission(
            self._context,
            Permission.TENANT_RECORD_READ,
        )
        template = self._templates.get(template_id)
        bounded_limit = min(limit, self._settings.text_to_sql_max_rows)
        parameters = {
            "canonical_entity_id": canonical_entity_id,
            "as_of_date": as_of_date,
            "limit": bounded_limit,
        }
        self._enforce_plan_cost(template, parameters)
        self._connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{self._settings.text_to_sql_timeout_seconds}s"},
        )
        rows = self._connection.execute(text(template.sql), parameters).mappings().all()
        records = tuple(self._record(dict(row)) for row in rows)
        event = create_audit_event(
            self._context,
            query_category="structured_query",
            entity_scope={
                "template_id": template_id,
                **(
                    {"canonical_entity_id": canonical_entity_id}
                    if canonical_entity_id is not None
                    else {}
                ),
            },
            source_ids=(record.provenance.source_record_id for record in records),
            result_status="ALLOWED",
        )
        write_audit_event(self._connection, event)
        return records

    def _enforce_plan_cost(
        self,
        template: ApprovedTemplate,
        parameters: Mapping[str, Any],
    ) -> None:
        result = self._connection.execute(
            text("EXPLAIN (FORMAT JSON) " + template.sql),
            dict(parameters),
        ).scalar_one()
        parsed: Any = json.loads(result) if isinstance(result, str) else result
        try:
            total_cost = float(parsed[0]["Plan"]["Total Cost"])
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise StructuredQueryError("PostgreSQL returned an invalid EXPLAIN plan") from error
        if total_cost > self._settings.text_to_sql_max_plan_cost:
            raise QueryCostExceeded(
                f"query plan cost {total_cost:.2f} exceeds configured threshold"
            )

    @classmethod
    def _record(cls, row: Mapping[str, Any]) -> StructuredRecord:
        refreshed = row.get("source_refreshed_at")
        if refreshed is not None and not isinstance(refreshed, datetime):
            raise StructuredQueryError("source freshness metadata is invalid")
        provenance = SourceProvenance(
            source_schema=str(row["source_schema"]),
            source_table=str(row["source_table"]),
            source_record_id=str(row["source_record_id"]),
            freshness_at=refreshed,
        )
        return StructuredRecord(
            values={
                key: value
                for key, value in row.items()
                if key not in cls._provenance_fields
            },
            provenance=provenance,
        )


class PostgresCatalogRepository:
    """Retrieve governed schema metadata and execute only typed analytical ASTs."""

    _provenance = (
        "source_schema",
        "source_table",
        "source_record_id",
        "source_refreshed_at",
    )

    def __init__(
        self,
        connection: Connection,
        context: AuthorizationContext,
        settings: Settings,
    ) -> None:
        self._connection = connection
        self._context = context
        self._settings = settings
        self._authorization = AuthorizationService()
        apply_postgres_session_context(connection, context)

    def retrieve_schema(
        self,
        question: str,
        *,
        query_embedding: Sequence[float] | None = None,
    ) -> tuple[CatalogTableMatch, ...]:
        """Return only approved governed views and non-sensitive columns."""

        if not self._settings.semantic_catalog_enabled:
            raise StructuredQueryError("semantic catalog retrieval is disabled")
        self._authorization.require_permission(
            self._context,
            Permission.TENANT_RECORD_READ,
        )
        vector = (
            self._vector_literal(query_embedding)
            if query_embedding is not None
            else None
        )
        rows = self._connection.execute(
            text(
                """
                SELECT catalog.source_schema, catalog.source_table,
                       catalog.business_description,
                       COALESCE(
                         array_agg(
                           catalog_column.source_column
                           ORDER BY catalog_column.ordinal_position
                         ) FILTER (
                           WHERE catalog_column.source_column IS NOT NULL
                         ),
                         ARRAY[]::text[]
                       ) AS approved_columns,
                       CASE
                         WHEN CAST(:query_embedding AS text) IS NOT NULL
                           THEN 1 - (
                             catalog.embedding
                             OPERATOR(pms_vector.<=>)
                             CAST(:query_embedding AS pms_vector.vector)
                           )
                         ELSE ts_rank_cd(
                           to_tsvector('simple', catalog.search_text),
                           plainto_tsquery('simple', :question)
                         )
                       END AS score
                FROM pms_catalog.approved_semantic_table AS catalog
                LEFT JOIN pms_catalog.approved_semantic_column AS catalog_column
                  ON catalog_column.catalog_table_id = catalog.catalog_table_id
                WHERE (
                    CAST(:query_embedding AS text) IS NOT NULL
                    AND catalog.embedding IS NOT NULL
                  )
                  OR (
                    CAST(:query_embedding AS text) IS NULL
                    AND to_tsvector('simple', catalog.search_text)
                      @@ plainto_tsquery('simple', :question)
                  )
                GROUP BY catalog.catalog_table_id, catalog.source_schema,
                         catalog.source_table, catalog.business_description,
                         catalog.search_text, catalog.embedding
                ORDER BY score DESC, catalog.source_table
                LIMIT :limit
                """
            ),
            {
                "question": question,
                "query_embedding": vector,
                "limit": self._settings.semantic_catalog_top_k_tables,
            },
        ).mappings().all()
        return tuple(
            CatalogTableMatch(
                source_schema=str(row["source_schema"]),
                source_table=str(row["source_table"]),
                business_description=str(row["business_description"]),
                approved_columns=tuple(str(item) for item in row["approved_columns"]),
                score=float(row["score"]),
            )
            for row in rows
        )

    def execute_plan(
        self,
        plan: ConstrainedSelectPlan,
        *,
        reviewed: bool,
    ) -> tuple[StructuredRecord, ...]:
        """Execute an internal typed plan; natural-language SQL is not accepted."""

        if not self._settings.text_to_sql_enabled:
            raise StructuredQueryError("constrained analytical queries are disabled")
        if self._settings.text_to_sql_human_review_for_high_risk and not reviewed:
            raise StructuredQueryError("analytical plan requires recorded review")
        self._authorization.require_permission(
            self._context,
            Permission.TENANT_RECORD_READ,
        )
        rows = self._connection.execute(
            text(
                """
                SELECT source_table, source_column
                FROM pms_catalog.approved_semantic_column
                WHERE source_schema = 'pms_app'
                ORDER BY source_table, ordinal_position
                """
            )
        ).mappings().all()
        allowlists: dict[str, set[str]] = {}
        for row in rows:
            allowlists.setdefault(str(row["source_table"]), set()).add(
                str(row["source_column"])
            )
        allowed = allowlists.get(plan.view, set())
        missing_provenance = [name for name in self._provenance if name not in allowed]
        if missing_provenance:
            raise StructuredQueryError("governed view lacks required provenance")
        columns = tuple(dict.fromkeys((*plan.columns, *self._provenance)))
        if len(columns) > self._settings.text_to_sql_max_columns:
            raise StructuredQueryError("analytical plan exceeds the column limit")
        bounded_plan = plan.model_copy(
            update={
                "columns": columns,
                "limit": min(plan.limit, self._settings.text_to_sql_max_rows),
            }
        )
        compiler = ConstrainedPlanCompiler(
            {name: frozenset(values) for name, values in allowlists.items()}
        )
        sql, parameters = compiler.compile(bounded_plan)
        self._enforce_plan_cost(sql, parameters)
        self._connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{self._settings.text_to_sql_timeout_seconds}s"},
        )
        result = self._connection.execute(text(sql), parameters).mappings().all()
        records = tuple(
            PostgresStructuredRepository._record(dict(row)) for row in result
        )
        event = create_audit_event(
            self._context,
            query_category="constrained_analytical_query",
            entity_scope={"view": plan.view},
            source_ids=(record.provenance.source_record_id for record in records),
            result_status="ALLOWED",
        )
        write_audit_event(self._connection, event)
        return records

    def _enforce_plan_cost(self, sql: str, parameters: Mapping[str, Any]) -> None:
        result = self._connection.execute(
            text("EXPLAIN (FORMAT JSON) " + sql),
            dict(parameters),
        ).scalar_one()
        parsed: Any = json.loads(result) if isinstance(result, str) else result
        try:
            total_cost = float(parsed[0]["Plan"]["Total Cost"])
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise StructuredQueryError("PostgreSQL returned an invalid EXPLAIN plan") from error
        if total_cost > self._settings.text_to_sql_max_plan_cost:
            raise QueryCostExceeded(
                f"query plan cost {total_cost:.2f} exceeds configured threshold"
            )

    @staticmethod
    def _vector_literal(values: Sequence[float]) -> str:
        vector = tuple(float(value) for value in values)
        if len(vector) != 1024 or not all(math.isfinite(value) for value in vector):
            raise StructuredQueryError(
                "semantic catalog query vector must contain 1024 finite values"
            )
        return "[" + ",".join(format(value, ".9g") for value in vector) + "]"
