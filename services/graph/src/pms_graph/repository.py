"""RLS-scoped PostgreSQL adjacency graph repository."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from pms_common.security import (
    AuthorizationContext,
    AuthorizationDenied,
    AuthorizationService,
    Permission,
    UserRole,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from sqlalchemy import Connection, bindparam, text

from pms_graph.models import (
    GraphEdgeEvidence,
    GraphEdgeInput,
    GraphNodeInput,
    GraphPath,
    GraphQuery,
    GraphVerificationStatus,
)


class GraphRepositoryError(RuntimeError):
    """Raised when a graph operation cannot produce a verified result."""


class PostgresGraphRepository:
    """Read verified graph paths and write only explicitly reviewed candidates."""

    def __init__(self, connection: Connection, context: AuthorizationContext) -> None:
        self._connection = connection
        self._context = context
        self._authorization = AuthorizationService()
        apply_postgres_session_context(connection, context)

    def add_candidate_node(self, node: GraphNodeInput) -> None:
        self._require_data_writer()
        if node.verification_status is not GraphVerificationStatus.CANDIDATE:
            raise GraphRepositoryError("ingestion can only create candidate nodes")
        self._connection.execute(
            text(
                """
                INSERT INTO pms_graph.graph_node (
                  node_id, node_type, canonical_entity_id,
                  owner_canonical_tenant_id, source_schema, source_table,
                  source_record_id, source_document_id, source_chunk_id,
                  source_clause, source_page, valid_from, valid_to,
                  security_classification, verification_status, confidence,
                  metadata, created_by_subject, created_at, active
                ) VALUES (
                  :node_id, :node_type, :canonical_entity_id,
                  :owner_canonical_tenant_id, :source_schema, :source_table,
                  :source_record_id, :source_document_id, :source_chunk_id,
                  :source_clause, :source_page, :valid_from, :valid_to,
                  :security_classification, 'candidate', :confidence,
                  CAST(:metadata AS jsonb), :created_by_subject, :created_at, true
                )
                """
            ),
            _node_parameters(node),
        )

    def add_candidate_edge(self, edge: GraphEdgeInput) -> None:
        self._require_data_writer()
        if edge.verification_status is not GraphVerificationStatus.CANDIDATE:
            raise GraphRepositoryError("ingestion can only create candidate edges")
        self._connection.execute(
            text(
                """
                INSERT INTO pms_graph.graph_edge (
                  edge_id, from_node_id, to_node_id, edge_type,
                  owner_canonical_tenant_id, source_schema, source_table,
                  source_record_id, source_document_id, source_chunk_id,
                  source_clause, source_page, valid_from, valid_to,
                  security_classification, verification_status, confidence,
                  metadata, created_by_subject, created_at, active
                ) VALUES (
                  :edge_id, :from_node_id, :to_node_id, :edge_type,
                  :owner_canonical_tenant_id, :source_schema, :source_table,
                  :source_record_id, :source_document_id, :source_chunk_id,
                  :source_clause, :source_page, :valid_from, :valid_to,
                  :security_classification, 'candidate', :confidence,
                  CAST(:metadata AS jsonb), :created_by_subject, :created_at, true
                )
                """
            ),
            _edge_parameters(edge),
        )

    def approve_node(self, node_id: str) -> None:
        self._require_reviewer()
        self._approve("graph_node", "node_id", node_id)

    def approve_edge(self, edge_id: str) -> None:
        self._require_reviewer()
        self._approve("graph_edge", "edge_id", edge_id)

    def traverse(self, query: GraphQuery) -> tuple[GraphPath, ...]:
        self._authorization.require_permission(
            self._context,
            Permission.TENANT_RECORD_READ,
        )
        as_of = query.as_of_date or date.today()
        rows = self._connection.execute(
            text(
                """
                WITH RECURSIVE walk AS (
                  SELECT
                    node.node_id AS current_node_id,
                    ARRAY[node.node_id]::text[] AS node_ids,
                    ARRAY[]::text[] AS edge_ids,
                    0 AS depth
                  FROM pms_graph.graph_node AS node
                  WHERE node.node_id = :source_node_id
                    AND node.active
                    AND node.verification_status = 'verified'
                    AND (node.valid_from IS NULL OR node.valid_from <= :as_of_date)
                    AND (node.valid_to IS NULL OR :as_of_date < node.valid_to)
                  UNION ALL
                  SELECT
                    next_node.node_id,
                    walk.node_ids || next_node.node_id,
                    walk.edge_ids || edge.edge_id,
                    walk.depth + 1
                  FROM walk
                  JOIN pms_graph.graph_edge AS edge
                    ON edge.from_node_id = walk.current_node_id
                   AND edge.active
                   AND edge.verification_status = 'verified'
                   AND (edge.valid_from IS NULL OR edge.valid_from <= :as_of_date)
                   AND (edge.valid_to IS NULL OR :as_of_date < edge.valid_to)
                  JOIN pms_graph.graph_node AS next_node
                    ON next_node.node_id = edge.to_node_id
                   AND next_node.active
                   AND next_node.verification_status = 'verified'
                   AND (next_node.valid_from IS NULL OR next_node.valid_from <= :as_of_date)
                   AND (next_node.valid_to IS NULL OR :as_of_date < next_node.valid_to)
                  WHERE walk.depth < :max_hops
                    AND NOT next_node.node_id = ANY(walk.node_ids)
                )
                SELECT node_ids, edge_ids, depth
                FROM walk
                WHERE depth > 0
                  AND (
                    CAST(:target_node_id AS text) IS NULL
                    OR current_node_id = CAST(:target_node_id AS text)
                  )
                ORDER BY depth, node_ids
                LIMIT :limit
                """
            ),
            {
                "source_node_id": query.source_node_id,
                "target_node_id": query.target_node_id,
                "as_of_date": as_of,
                "max_hops": query.max_hops,
                "limit": query.limit,
            },
        ).mappings().all()
        paths = tuple(self._path(row) for row in rows)
        event = create_audit_event(
            self._context,
            query_category="graph_traversal",
            entity_scope={
                "source_node_id": query.source_node_id,
                "max_hops": str(query.max_hops),
            },
            source_ids=(edge_id for path in paths for edge_id in path.edge_ids),
            result_status="ALLOWED" if paths else "REVIEW_REQUIRED",
        )
        write_audit_event(self._connection, event)
        return paths

    def _path(self, row: Any) -> GraphPath:
        node_ids = tuple(str(value) for value in row["node_ids"])
        edge_ids = tuple(str(value) for value in row["edge_ids"])
        evidence = self._edge_evidence(edge_ids)
        return GraphPath(
            node_ids=node_ids,
            edge_ids=edge_ids,
            depth=int(row["depth"]),
            evidence=evidence,
        )

    def _edge_evidence(self, edge_ids: Sequence[str]) -> tuple[GraphEdgeEvidence, ...]:
        if not edge_ids:
            return ()
        statement = text(
            """
            SELECT edge_id, edge_type, from_node_id, to_node_id,
                   source_schema, source_table, source_record_id,
                   source_document_id, source_chunk_id, source_clause,
                   source_page, valid_from, valid_to
            FROM pms_graph.graph_edge
            WHERE edge_id IN :edge_ids
              AND active
              AND verification_status = 'verified'
            ORDER BY array_position(:ordered_edge_ids, edge_id)
            """
        ).bindparams(
            bindparam("edge_ids", expanding=True),
            bindparam("ordered_edge_ids"),
        )
        rows = self._connection.execute(
            statement,
            {"edge_ids": list(edge_ids), "ordered_edge_ids": list(edge_ids)},
        ).mappings().all()
        return tuple(GraphEdgeEvidence(**dict(row)) for row in rows)

    def _approve(self, table: str, key: str, value: str) -> None:
        if table not in {"graph_node", "graph_edge"} or key not in {
            "node_id",
            "edge_id",
        }:
            raise GraphRepositoryError("invalid graph approval target")
        self._connection.execute(
            text(
                f"""
                UPDATE pms_graph.{table}
                SET verification_status = 'verified',
                    reviewed_by_subject = :reviewed_by_subject,
                    reviewed_at = :reviewed_at,
                    confidence = NULL
                WHERE {key} = :value
                  AND verification_status = 'candidate'
                """
            ),
            {
                "value": value,
                "reviewed_by_subject": self._context.subject,
                "reviewed_at": datetime.now(UTC),
            },
        )

    def _require_data_writer(self) -> None:
        self._authorization.require_permission(self._context, Permission.DATA_WRITE)

    def _require_reviewer(self) -> None:
        if not self._context.roles.intersection(
            {UserRole.AUDITOR, UserRole.ADMINISTRATOR}
        ):
            raise AuthorizationDenied("graph verification requires Auditor or Administrator")


def _node_parameters(node: GraphNodeInput) -> dict[str, Any]:
    return {
        **node.model_dump(exclude={"verification_status", "reviewed_by_subject"}),
        "node_type": node.node_type.value,
        "metadata": _json(node.metadata),
        "created_at": datetime.now(UTC),
    }


def _edge_parameters(edge: GraphEdgeInput) -> dict[str, Any]:
    return {
        **edge.model_dump(exclude={"verification_status", "reviewed_by_subject"}),
        "edge_type": edge.edge_type.value,
        "metadata": _json(edge.metadata),
        "created_at": datetime.now(UTC),
    }


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))
