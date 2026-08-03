"""Validate the configured Phase 12 graph storage without inventing edges."""

from __future__ import annotations

import json
from datetime import date

from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_graph.models import GraphQuery
from pms_graph.repository import PostgresGraphRepository
from pms_graph.service import GraphRagService
from sqlalchemy import text

TARGET_REVISION = "20260731_0012"
GRAPH_TABLES = ("graph_node", "graph_edge")


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="phase12-configured-validator",
        roles=frozenset({UserRole.AUDITOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def main() -> int:
    settings = Settings()
    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            repository = PostgresGraphRepository(connection, _context())
            revision = str(
                connection.execute(
                    text("SELECT version_num FROM pms_app.alembic_version")
                ).scalar_one()
            )
            table_count = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*) FROM information_schema.tables
                        WHERE table_schema = 'pms_graph'
                          AND table_name = ANY(:table_names)
                        """
                    ),
                    {"table_names": list(GRAPH_TABLES)},
                ).scalar_one()
            )
            rls_count = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_class AS class
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = class.relnamespace
                        WHERE namespace.nspname = 'pms_graph'
                          AND class.relname = ANY(:table_names)
                          AND class.relrowsecurity
                          AND class.relforcerowsecurity
                        """
                    ),
                    {"table_names": list(GRAPH_TABLES)},
                ).scalar_one()
            )
            visible_nodes = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*) FROM pms_graph.graph_node
                        WHERE active AND verification_status = 'verified'
                        """
                    )
                ).scalar_one()
            )
            visible_edges = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*) FROM pms_graph.graph_edge
                        WHERE active AND verification_status = 'verified'
                        """
                    )
                ).scalar_one()
            )
            paths = repository.traverse(
                GraphQuery(
                    source_node_id="phase12-no-reviewed-source",
                    as_of_date=date(2026, 7, 31),
                )
            )
            answer = GraphRagService(repository).ask(
                GraphQuery(source_node_id="phase12-no-reviewed-source")
            )
    finally:
        engine.dispose()

    technical_passed = revision == TARGET_REVISION and table_count == 2 and rls_count == 2
    relationship_gate = bool(paths) and not answer.review_required
    result = {
        "phase": "12",
        "passed": technical_passed and relationship_gate,
        "technical_storage_passed": technical_passed,
        "relationship_gate": relationship_gate,
        "revision": revision,
        "graph_tables": table_count,
        "forced_rls_tables": rls_count,
        "visible_verified_nodes": visible_nodes,
        "visible_verified_edges": visible_edges,
        "empty_graph_answer_review_required": answer.review_required,
        "status": (
            "PASS"
            if technical_passed and relationship_gate
            else "PENDING_REVIEWED_GRAPH_INPUTS"
        ),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
