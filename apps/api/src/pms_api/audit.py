"""Audited query visibility and denial recording for the Phase 13 slice."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol

from pms_common.security import (
    AuthorizationContext,
    AuthorizationService,
    Permission,
    apply_postgres_session_context,
    create_audit_event,
    write_audit_event,
)
from sqlalchemy import Engine, text

from pms_api.schemas import AuditEventResponse


class AuditService(Protocol):
    def list_my_queries(self, limit: int) -> tuple[AuditEventResponse, ...]: ...

    def record_denied(self, query_category: str, reason_code: str) -> None: ...

    def record_demo_query(
        self,
        *,
        question: str,
        route: str,
        query_id: str | None,
        database_objects: tuple[str, ...],
        row_count: int,
        citation_ids: tuple[str, ...],
        rejection_reason: str | None,
        response_status: str,
        duration_ms: float,
    ) -> None: ...


class AuditServiceProvider(Protocol):
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> AbstractContextManager[AuditService]: ...


class PostgresAuditService:
    """Read only the caller's audit rows; write bounded denial metadata."""

    def __init__(self, connection: Any, context: AuthorizationContext) -> None:
        self._connection = connection
        self._context = context
        self._authorization = AuthorizationService()
        apply_postgres_session_context(connection, context)

    def list_my_queries(self, limit: int) -> tuple[AuditEventResponse, ...]:
        self._authorization.require_permission(self._context, Permission.AUDIT_READ)
        bounded_limit = min(max(limit, 1), 100)
        rows = self._connection.execute(
            text(
                """
                SELECT event_id, occurred_at, query_category, entity_scope,
                       source_ids, result_status, correlation_id
                FROM pms_audit.security_event
                WHERE subject = :subject
                ORDER BY occurred_at DESC
                LIMIT :limit
                """
            ),
            {"subject": self._context.subject, "limit": bounded_limit},
        ).mappings().all()
        return tuple(AuditEventResponse.model_validate(dict(row)) for row in rows)

    def record_denied(self, query_category: str, reason_code: str) -> None:
        apply_postgres_session_context(self._connection, self._context)
        write_audit_event(
            self._connection,
            create_audit_event(
                self._context,
                query_category=query_category,
                entity_scope={"reason_code": reason_code},
                source_ids=(),
                result_status="DENIED",
            ),
        )

    def record_demo_query(
        self,
        *,
        question: str,
        route: str,
        query_id: str | None,
        database_objects: tuple[str, ...],
        row_count: int,
        citation_ids: tuple[str, ...],
        rejection_reason: str | None,
        response_status: str,
        duration_ms: float,
    ) -> None:
        """Record bounded demo metadata without returned values or credentials."""

        apply_postgres_session_context(self._connection, self._context)
        write_audit_event(
            self._connection,
            create_audit_event(
                self._context,
                query_category="controlled_demo_query",
                entity_scope={
                    "question": question[:1000],
                    "route": route,
                    "approved_query_identifier": query_id or "",
                    "database_objects": ",".join(database_objects),
                    "row_count": str(row_count),
                    "document_citations": ",".join(citation_ids),
                    "rejection_reason": rejection_reason or "",
                    "response_status": response_status,
                    "duration_ms": f"{duration_ms:.3f}",
                },
                source_ids=citation_ids,
                result_status=response_status,
            ),
        )


class PostgresAuditServiceProvider:
    """Create one transaction-scoped audit service per request."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> Iterator[PostgresAuditService]:
        with self._engine.begin() as connection:
            yield PostgresAuditService(connection, context)
