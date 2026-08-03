"""Run bounded Phase 14 security, citation, freshness and timing checks.

The validator is deliberately read-only against PostgreSQL. A live Keycloak
token is validated only when ``PMS_ACCESS_TOKEN`` is supplied; no credentials
or token are fabricated by this script.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from pms_common.database import create_database_engine
from pms_common.security import AuthorizationContext, Classification, JwtValidator, UserRole
from pms_common.settings import Settings
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERFORMANCE_PATH = PROJECT_ROOT / "artifacts/evaluation/phase14_performance.json"
EXPECTED_PROTECTED_HASH = (
    "6e9e7d2ec1fb8d3ddbf9193ab4e79c0c1e06927545d270d752db2a9870f7f442"
)


class PendingGate(RuntimeError):
    """An external gate could not be exercised without inventing evidence."""


def _admin_connection(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required for Phase 14 validation")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def _timed_samples(
    connection: Any,
    statement: str,
    parameters: dict[str, object] | None = None,
    *,
    count: int = 10,
) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        connection.execute(text(statement), parameters or {}).scalar()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max_ms": round(max(ordered), 3),
    }


def _set_context(connection: Any, context: AuthorizationContext) -> None:
    values = {
        "subject": context.subject,
        "tenant_id": context.tenant_id or "",
        "roles": ",".join(sorted(role.value for role in context.roles)),
        "department_id": context.department_id or "",
        "unit_id": context.unit_id or "",
        "classification": context.classification.value,
    }
    for key, value in values.items():
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": f"pms.{key}", "value": value},
        )


def _context(subject: str, role: UserRole, department: str | None) -> AuthorizationContext:
    return AuthorizationContext(
        subject=subject,
        roles=frozenset({role}),
        tenant_id="phase14-unmapped-tenant" if role is UserRole.TENANT else None,
        department_id=department,
        unit_id="land",
        classification=Classification.RESTRICTED,
    )


def _keycloak_gate(settings: Settings) -> None:
    def probe(url: str) -> float:
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status >= 500:
                    raise PendingGate(f"Keycloak returned HTTP {response.status}")
        except (urllib.error.URLError, TimeoutError) as error:
            raise PendingGate(f"Keycloak endpoint unavailable: {url}") from error
        return (time.perf_counter() - started) * 1000

    issuer_ms = probe(settings.keycloak_issuer)
    jwks_ms = probe(settings.keycloak_jwks_url)
    print(f"PASS keycloak_endpoints issuer_ms={issuer_ms:.3f} jwks_ms={jwks_ms:.3f}")
    token = os.environ.get("PMS_ACCESS_TOKEN", "").strip()
    if not token:
        raise PendingGate("PMS_ACCESS_TOKEN was not supplied; issued-token flow not tested")
    JwtValidator(settings).validate(token)
    print("PASS keycloak_issued_token=true")


def _database_gate(settings: Settings) -> dict[str, object]:
    with _admin_connection(settings) as connection:
        revision = connection.execute(
            "SELECT version_num FROM pms_app.alembic_version"
        ).fetchone()
        if revision is None or str(revision[0]) != "20260801_0013":
            raise RuntimeError(f"unexpected configured migration head: {revision}")
        protected = connection.execute(
            """
            SELECT table_schema, table_name, column_name, ordinal_position,
                   data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema IN ('public', 'pms_extract_2010_2023')
            ORDER BY table_schema, table_name, ordinal_position
            """
        ).fetchall()
        encoded = json.dumps(protected, default=str, separators=(",", ":")).encode()
        import hashlib

        protected_hash = hashlib.sha256(encoded).hexdigest()
        if protected_hash != EXPECTED_PROTECTED_HASH:
            raise RuntimeError("protected-schema metadata fingerprint changed")
        role = connection.execute(
            """
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
            FROM pg_roles WHERE rolname = 'pms_app_runtime'
            """
        ).fetchone()
        if role != (True, False, False, False, False):
            raise RuntimeError(f"runtime role is not least privilege: {role}")
        active_chunks = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE cardinality(page_numbers) > 0),
                   count(*) FILTER (WHERE jsonb_array_length(bounding_boxes) > 0)
            FROM pms_vector.document_chunk WHERE active
            """
        ).fetchone()
        statuses = connection.execute(
            "SELECT status, count(*) FROM pms_doc.document_record GROUP BY status ORDER BY status"
        ).fetchall()
        latest_snapshot = connection.execute(
            """
            SELECT feature_snapshot_id
            FROM pms_forecast.feature_snapshot
            WHERE target_name = 'monthly_cash_collection' AND leakage_status = 'safe'
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        selected_snapshot = connection.execute(
            """
            SELECT version.feature_snapshot_id
            FROM pms_forecast.training_run AS run
            JOIN pms_forecast.model_version AS version
              ON version.model_version_id = run.selected_model_version_id
            WHERE run.target_name = 'monthly_cash_collection'
              AND run.status = 'completed'
              AND run.selected_model_version_id IS NOT NULL
            ORDER BY run.completed_at DESC LIMIT 1
            """
        ).fetchone()
    if active_chunks is None or len(active_chunks) != 3:
        # The explicit checks below provide the useful error message; this guard
        # only protects against an unexpected aggregate result shape.
        raise RuntimeError("citation aggregate returned an invalid shape")
    if int(active_chunks[0]) != int(active_chunks[1]) or int(active_chunks[0]) != int(
        active_chunks[2]
    ):
        raise RuntimeError(f"active citation provenance is incomplete: {active_chunks}")
    stale = (
        latest_snapshot is not None
        and selected_snapshot is not None
        and latest_snapshot[0] != selected_snapshot[0]
    )
    print(f"PASS configured_revision={revision[0]}")
    print(f"PASS protected_schema_metadata_sha256={protected_hash}")
    print("PASS runtime_role=pms_app_runtime_non_elevated")
    print(f"PASS active_chunk_citations={active_chunks[0]}_of_{active_chunks[0]}")
    print(f"PASS document_statuses={dict(statuses)}")
    print(f"PASS stale_prediction_snapshot={stale}")
    return {
        "revision": str(revision[0]),
        "protected_schema_hash": protected_hash,
        "active_chunks": int(active_chunks[0]),
        "document_statuses": dict(statuses),
        "stale_prediction_snapshot": stale,
    }


def _rls_gate(settings: Settings) -> dict[str, object]:
    engine = create_database_engine(settings, read_only=True)
    try:
        with engine.begin() as connection:
            current_user = str(connection.execute(text("SELECT current_user")).scalar_one())
            if current_user != "pms_app_runtime":
                raise RuntimeError(f"configured runtime user is {current_user}")
            contexts = {
                "tenant": _context("phase14-tenant", UserRole.TENANT, None),
                "do": _context("phase14-do", UserRole.DATA_ENTRY_OPERATOR, None),
                "estate": _context("phase14-estate", UserRole.DATA_ENTRY_OPERATOR, "estate"),
                "legal": _context("phase14-legal", UserRole.LEGAL_OFFICER, "legal"),
                "finance": _context("phase14-finance", UserRole.FINANCE_OFFICER, "finance"),
            }
            observed: dict[str, dict[str, int]] = {}
            for name, context in contexts.items():
                _set_context(connection, context)
                observed[name] = {
                    "documents": int(
                        connection.execute(
                            text("SELECT count(*) FROM pms_doc.document_record")
                        ).scalar()
                    ),
                    "chunks": int(
                        connection.execute(
                            text("SELECT count(*) FROM pms_vector.document_chunk")
                        ).scalar()
                    ),
                    "predictions": int(
                        connection.execute(
                            text("SELECT count(*) FROM pms_forecast.prediction")
                        ).scalar()
                    ),
                }
            timings = {
                "rls_document_count": _timed_samples(
                    connection,
                    "SELECT count(*) FROM pms_doc.document_record",
                ),
                "rls_chunk_count": _timed_samples(
                    connection,
                    "SELECT count(*) FROM pms_vector.document_chunk",
                ),
            }
    finally:
        engine.dispose()
    if observed["tenant"]["predictions"] != 0:
        raise RuntimeError("tenant prediction access was not denied")
    if observed["do"]["predictions"] == 0:
        raise RuntimeError("Data Entry Operator could not read governed predictions")
    if observed["estate"]["chunks"] == 0:
        raise RuntimeError("authorized estate chunk access unexpectedly returned zero")
    if observed["legal"]["chunks"] != 0:
        raise RuntimeError("legal department received estate chunks")
    if observed["finance"]["predictions"] == 0:
        raise RuntimeError("Finance scope could not read authorized predictions")
    print(
        "PASS RLS/ACL tenant_prediction_denial=0 do_governed_reads=allowed "
        "legal_cross_department_chunks=0"
    )
    print(f"PASS RLS/ACL scoped_counts={observed}")
    return {"observed": observed, "timings_ms": timings}


def _failed_document_gate(database: dict[str, object]) -> None:
    statuses = database["document_statuses"]
    if not isinstance(statuses, dict):
        raise RuntimeError("document status aggregate is invalid")
    if "review_required" not in statuses and "failed" not in statuses:
        raise RuntimeError("no failed/review-required document lifecycle state is persisted")
    print(
        "PASS failed_document_lifecycle=bounded_failure_or_review_required "
        f"persisted_failed={statuses.get('failed', 0)} "
        f"persisted_review_required={statuses.get('review_required', 0)}"
    )


def _performance_gate(settings: Settings, rls: dict[str, object]) -> dict[str, object]:
    timings = rls["timings_ms"]
    if not isinstance(timings, dict):
        raise RuntimeError("performance timings are invalid")
    report: dict[str, object] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "machine": {"python": os.sys.version.split()[0]},
        "stages": timings,
        "unmeasured_stages": [
            "keycloak_token_exchange",
            "lexical_retrieval",
            "dense_vector_retrieval",
            "reranking",
            "llm_first_token",
            "generation",
            "ocr_parse",
            "upload_object_store",
        ],
        "note": (
            "Only available local database/RLS stages were measured; "
            "no target latency was invented."
        ),
    }
    PERFORMANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERFORMANCE_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"PASS performance_report={PERFORMANCE_PATH}")
    return report


def main() -> int:
    settings = Settings()
    pending: list[str] = []
    try:
        _keycloak_gate(settings)
    except PendingGate as error:
        pending.append(str(error))
        print(f"PENDING keycloak={error}")
    database = _database_gate(settings)
    rls = _rls_gate(settings)
    _failed_document_gate(database)
    _performance_gate(settings, rls)
    if database["stale_prediction_snapshot"]:
        pending.append("newer feature snapshot requires refresh-stale")
        print("PENDING stale prediction regeneration is required")
    else:
        print("PASS stale prediction regeneration check=FRESH")
    return 2 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
