"""Produce a bounded, non-PII Phase 11 source profile from PostgreSQL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from pms_common.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/evaluation/phase11_source_profile.json"
SOURCE_TABLES = (
    "rent_slab",
    "rent_slab_sor",
    "additional_rent",
    "additional_rent_slab",
    "letout_tenancy_unit_mapping",
    "applicant_property_mapping",
    "m_taxes",
    "m_taxes_period",
    "m_tax_rates",
    "m_lbty_tax_rate",
    "m_tax_for_treecess_street_edu",
    "applicant_tax_mapping",
    "m_interest_type",
    "m_lbty_intrestrate",
    "m_interest_heads",
    "m_lbty_breach_rate_charges",
    "tgeneralbill",
    "monthly_heads_bill",
    "monthly_taxes_bill",
    "monthly_final_bills",
    "tpaymentmarking",
    "tdemandnotice",
    "tcreditnote",
    "verified_tenancy_data",
    "tenancy_agreement_mapping",
    "mtenant",
    "plot_fair_mkt_value",
    "plot_rr_land_value",
    "plot_sor_market_value",
)


def _connect(settings: Settings) -> psycopg.Connection[tuple[Any, ...]]:
    if settings.postgres_password is None:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        sslmode=settings.db_ssl_mode,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def main() -> int:
    settings = Settings()
    with _connect(settings) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '60s'")
        overview = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'),
              (SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'VIEW'),
              (SELECT sum(GREATEST(reltuples, 0))::bigint
                 FROM pg_class AS class
                 JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = 'public' AND class.relkind = 'r'),
              (SELECT sum(pg_total_relation_size(class.oid))::bigint
                 FROM pg_class AS class
                 JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = 'public' AND class.relkind = 'r')
            """
        ).fetchone()
        if overview is None:
            raise RuntimeError("database overview returned no row")
        selected = connection.execute(
            """
            SELECT class.relname, class.reltuples::bigint,
                   pg_total_relation_size(class.oid)
            FROM pg_class AS class
            JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = 'public' AND class.relkind = 'r'
              AND class.relname = ANY(%s)
            ORDER BY class.relname
            """,
            (list(SOURCE_TABLES),),
        ).fetchall()
        model_rows = connection.execute(
            """
            SELECT source_table, min(month_start), max(month_start), count(*),
                   count(DISTINCT month_start), sum(amount_total)
            FROM pms_extract_2010_2023.model_revenue_monthly_by_source
            GROUP BY source_table ORDER BY source_table
            """
        ).fetchall()
        blockers = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM pms_extract_2010_2023.fact_payment_legacy),
              (SELECT count(*) FROM pms_extract_2010_2023.fact_payment_legacy
                WHERE duedate IS NOT NULL),
              (SELECT count(*) FROM
                pms_extract_2010_2023.model_land_value_observations),
              (SELECT count(*) FROM pms_extract_2010_2023.fact_breach),
              (SELECT count(*) FROM
                pms_extract_2010_2023.model_revenue_monthly_by_source
                WHERE source_table = 'cash_revenue_data'
                  AND revenue_type IS NOT NULL)
            """
        ).fetchone()
    if blockers is None:
        raise RuntimeError("blocker profile returned no row")
    selected_names = {str(row[0]) for row in selected}
    public_relations = int(str(overview[0])) + int(str(overview[1]))
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "read-only, aggregate-only, no PII values",
        "database_overview": {
            "public_base_tables": int(overview[0]),
            "public_views": int(overview[1]),
            "public_relations": public_relations,
            "estimated_public_rows": int(overview[2]),
            "public_size_bytes": int(overview[3]),
        },
        "selected_source_tables": [
            {
                "table": str(name),
                "estimated_rows": max(0, int(estimated_rows)),
                "size_bytes": int(size_bytes),
            }
            for name, estimated_rows, size_bytes in selected
        ],
        "selected_source_tables_found": len(selected_names),
        "selected_source_tables_missing": sorted(set(SOURCE_TABLES) - selected_names),
        "curated_revenue_sources": [
            {
                "source_table": str(source),
                "from": str(period_from),
                "to": str(period_to),
                "aggregate_rows": int(row_count),
                "months": int(months),
                "amount_total": str(amount),
            }
            for source, period_from, period_to, row_count, months, amount in model_rows
        ],
        "verified_blockers": {
            "payment_rows": int(blockers[0]),
            "payment_rows_with_due_date": int(blockers[1]),
            "land_value_observations": int(blockers[2]),
            "breach_labels": int(blockers[3]),
            "cash_revenue_rows_with_charge_category": int(blockers[4]),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"PASS output={OUTPUT}")
    print(f"PASS selected_source_tables={len(selected_names)}/{len(SOURCE_TABLES)}")
    print(f"PASS public_relations={public_relations}")
    print(f"PASS payment_rows_with_due_date={blockers[1]}")
    print(f"PASS land_value_observations={blockers[2]}")
    print(f"PASS breach_labels={blockers[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
