"""Live Phase 09 exact-value, RLS, routing, and catalog gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pms_common.security import (
    AuthorizationContext,
    Classification,
    UserRole,
)
from pms_common.settings import Settings
from pms_retrieval.embedding import BgeM3EmbeddingAdapter
from pms_structured.models import (
    ConstrainedSelectPlan,
    QueryRoute,
    StructuredQuery,
)
from pms_structured.repository import (
    PostgresCatalogRepository,
    PostgresStructuredRepository,
)
from pms_structured.router import DeterministicRouter
from pms_structured.service import StructuredQueryService
from pms_structured.templates import ApprovedTemplateRegistry
from sqlalchemy import URL, create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_REVISION = "20260730_0007"
RUNTIME_ROLE = "pms_app_runtime"


def _admin_url(settings: Settings) -> str:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_database,
    ).render_as_string(hide_password=False)


def _context(
    subject: str,
    role: UserRole,
    tenant_id: str | None,
) -> AuthorizationContext:
    return AuthorizationContext(
        subject=subject,
        roles=frozenset({role}),
        tenant_id=tenant_id,
        department_id="phase09-validation",
        unit_id="phase09-validation",
        classification=Classification.RESTRICTED,
    )


def _service(
    connection: object,
    context: AuthorizationContext,
    settings: Settings,
) -> StructuredQueryService:
    from sqlalchemy import Connection

    if not isinstance(connection, Connection):
        raise TypeError("SQLAlchemy connection is required")
    router = DeterministicRouter(
        PROJECT_ROOT / settings.query_router_config,
        PROJECT_ROOT / settings.domain_synonyms_config,
    )
    templates = ApprovedTemplateRegistry(
        PROJECT_ROOT / settings.sql_template_dir,
        max_joins=settings.text_to_sql_max_joins,
    )
    repository = PostgresStructuredRepository(
        connection,
        context,
        settings,
        templates,
    )
    return StructuredQueryService(router, repository)


def main() -> int:
    settings = Settings()
    engine = create_engine(_admin_url(settings), pool_pre_ping=True)
    checks: dict[str, object] = {}
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                revision = connection.execute(
                    text("SELECT version_num FROM pms_app.alembic_version")
                ).scalar_one()
                if revision != TARGET_REVISION:
                    raise RuntimeError(f"expected {TARGET_REVISION}; found {revision}")

                catalog = connection.execute(
                    text(
                        """
                        SELECT
                          count(*) FILTER (
                            WHERE table_kind = 'extracted_table'
                          ) AS extracted_tables,
                          count(*) FILTER (
                            WHERE table_kind = 'governed_view'
                          ) AS governed_views,
                          count(*) FILTER (
                            WHERE embedding IS NOT NULL
                          ) AS embeddings
                        FROM pms_catalog.semantic_table
                        """
                    )
                ).mappings().one()
                columns = connection.execute(
                    text(
                        """
                        SELECT count(*), count(DISTINCT semantic_class)
                        FROM pms_catalog.semantic_column
                        WHERE source_schema = 'pms_extract_2010_2023'
                        """
                    )
                ).one()
                if (
                    int(catalog["extracted_tables"]) != 61
                    or int(catalog["governed_views"]) != 9
                    or int(catalog["embeddings"]) != 70
                    or int(columns[0]) != 1010
                    or int(columns[1]) < 6
                ):
                    raise RuntimeError("semantic catalog completeness check failed")

                bills = connection.execute(
                    text(
                        """
                        SELECT bill_id::text AS bill_id, final_amount
                        FROM pms_extract_2010_2023.fact_monthly_bills AS bill
                        WHERE bill_id IS NOT NULL
                          AND NOT EXISTS (
                            SELECT 1
                            FROM pms_catalog.entity_identity_map AS identity_map
                            WHERE identity_map.entity_type = 'bill'
                              AND identity_map.source_schema =
                                'pms_extract_2010_2023'
                              AND identity_map.source_table = 'fact_monthly_bills'
                              AND identity_map.source_record_id = bill.bill_id::text
                          )
                        ORDER BY bill_id
                        LIMIT 2
                        """
                    )
                ).mappings().all()
                if len(bills) != 2:
                    raise RuntimeError("two unmapped bill records are required for validation")

                connection.execute(
                    text("SELECT set_config('pms.roles', 'Administrator', true)")
                )
                now = datetime.now(UTC)
                mapping_rows = []
                for index, bill in enumerate(bills, start=1):
                    mapping_rows.append(
                        {
                            "identity_map_id": str(uuid4()),
                            "canonical_entity_id": f"phase09-bill-{index}",
                            "owner": f"phase09-tenant-{index}",
                            "source_record_id": str(bill["bill_id"]),
                            "source_refreshed_at": now,
                        }
                    )
                connection.execute(
                    text(
                        """
                        INSERT INTO pms_catalog.entity_identity_map (
                          identity_map_id, entity_type, canonical_entity_id,
                          owner_canonical_tenant_id, source_schema, source_table,
                          source_record_id, source_refreshed_at, mapping_basis,
                          reviewed_by_subject
                        ) VALUES (
                          :identity_map_id, 'bill', :canonical_entity_id,
                          :owner, 'pms_extract_2010_2023', 'fact_monthly_bills',
                          :source_record_id, :source_refreshed_at,
                          'transactional Phase 09 validation mapping',
                          'phase09-validator'
                        )
                        """
                    ),
                    mapping_rows,
                )

                connection.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))
                tenant_one = _service(
                    connection,
                    _context(
                        "phase09-tenant-subject-1",
                        UserRole.TENANT,
                        "phase09-tenant-1",
                    ),
                    settings,
                )
                allowed = tenant_one.ask(
                    StructuredQuery(
                        question="Show bill",
                        canonical_entity_id="phase09-bill-1",
                        limit=1,
                    )
                )
                denied = tenant_one.ask(
                    StructuredQuery(
                        question="Show bill",
                        canonical_entity_id="phase09-bill-2",
                        limit=1,
                    )
                )
                finance = _service(
                    connection,
                    _context(
                        "phase09-finance-subject",
                        UserRole.FINANCE_OFFICER,
                        None,
                    ),
                    settings,
                )
                port_wide = finance.ask(
                    StructuredQuery(
                        question="Show bill",
                        canonical_entity_id="phase09-bill-2",
                        limit=1,
                    )
                )

                if len(allowed.records) != 1:
                    raise RuntimeError("authorized exact-row retrieval failed")
                actual_amount = allowed.records[0].values["final_amount"]
                if actual_amount != bills[0]["final_amount"]:
                    raise RuntimeError("structured result does not match source exact value")
                if denied.records:
                    raise RuntimeError("cross-tenant row was visible")
                if len(port_wide.records) != 1:
                    raise RuntimeError("authorized port-wide retrieval failed")
                if allowed.records[0].provenance.source_record_id != bills[0]["bill_id"]:
                    raise RuntimeError("source record provenance is incorrect")
                if allowed.records[0].provenance.freshness_at is None:
                    raise RuntimeError("source freshness metadata is absent")

                catalog_repository = PostgresCatalogRepository(
                    connection,
                    _context(
                        "phase09-finance-subject",
                        UserRole.FINANCE_OFFICER,
                        None,
                    ),
                    settings,
                )
                schema_matches = catalog_repository.retrieve_schema(
                    "bill final amount due date"
                )
                if not schema_matches or schema_matches[0].source_table != "bill_360":
                    raise RuntimeError("governed schema retrieval failed")
                query_vector = BgeM3EmbeddingAdapter(settings).embed(
                    ("bill final amount due date",)
                )[0]
                dense_schema_matches = catalog_repository.retrieve_schema(
                    "bill final amount due date",
                    query_embedding=query_vector,
                )
                if "bill_360" not in {
                    match.source_table for match in dense_schema_matches
                }:
                    raise RuntimeError("dense governed schema retrieval failed")
                analytical = catalog_repository.execute_plan(
                    ConstrainedSelectPlan(
                        view="bill_360",
                        columns=("canonical_entity_id", "final_amount"),
                        filters=(
                            ("canonical_entity_id", "=", "phase09-bill-2"),
                        ),
                        limit=1,
                    ),
                    reviewed=True,
                )
                if (
                    len(analytical) != 1
                    or analytical[0].values["final_amount"]
                    != bills[1]["final_amount"]
                ):
                    raise RuntimeError("constrained analytical plan exactness failed")

                router = DeterministicRouter(
                    PROJECT_ROOT / settings.query_router_config,
                    PROJECT_ROOT / settings.domain_synonyms_config,
                )
                routing = {
                    "structured": router.route("Show bill").route,
                    "document": router.route("Show the policy clause").route,
                    "rule": router.route("Calculate rent escalation").route,
                    "forecast": router.route("Forecast revenue").route,
                    "graph": router.route("Show relationship graph path").route,
                    "clarify": router.route("Please help").route,
                    "refuse": router.route("DROP TABLE bill").route,
                }
                expected_routes = {
                    "structured": QueryRoute.STRUCTURED,
                    "document": QueryRoute.DOCUMENT,
                    "rule": QueryRoute.RULE_CALCULATION,
                    "forecast": QueryRoute.FORECAST,
                    "graph": QueryRoute.GRAPH,
                    "clarify": QueryRoute.CLARIFY,
                    "refuse": QueryRoute.REFUSE,
                }
                if routing != expected_routes:
                    raise RuntimeError("deterministic routing matrix failed")

                checks = {
                    "configured_revision": revision,
                    "catalog_extracted_tables": int(catalog["extracted_tables"]),
                    "catalog_extracted_columns": int(columns[0]),
                    "catalog_governed_views": int(catalog["governed_views"]),
                    "catalog_embeddings": int(catalog["embeddings"]),
                    "semantic_classes": int(columns[1]),
                    "authorized_exact_rows": len(allowed.records),
                    "unauthorized_rows_visible": len(denied.records),
                    "port_wide_exact_rows": len(port_wide.records),
                    "schema_retrieval_top_match": schema_matches[0].source_table,
                    "dense_schema_contains_bill": True,
                    "constrained_plan_exact_rows": len(analytical),
                    "provenance_source_record_id": (
                        allowed.records[0].provenance.source_record_id
                    ),
                    "routing": {
                        key: value.value for key, value in routing.items()
                    },
                    "transactional_test_data_persisted": False,
                }
            finally:
                transaction.rollback()
    finally:
        engine.dispose()

    print(json.dumps({"status": "PASS", **checks}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
