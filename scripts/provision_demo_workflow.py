"""Provision an isolated, synthetic PMS workflow dataset for local demos.

The script never reads or copies source-row values. It clones only selected
extracted-table structures into ``pms_demo`` and inserts deterministic dummy
values. ``--apply`` is required because it creates a schema and tables.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql

DEMO_SCHEMA = "pms_demo"
SOURCE_SCHEMA = "pms_extract_2010_2023"
ROW_COUNT = 10
WORKFLOW_TABLES = (
    "dim_tenant_legacy",
    "dim_customer_legacy",
    "dim_plot",
    "bridge_letout_tenancy_plot",
    "dim_lease_particulars_snapshot",
    "fact_monthly_bills",
    "fact_payment_current",
)
SENSITIVE_COLUMN_PARTS = ("password", "passwd", "pwd", "secret", "token")


@dataclass(frozen=True)
class ColumnSpec:
    """One column copied from a selected extracted workflow table."""

    name: str
    data_type: str
    maximum_length: int | None


def _demo_customer_code(index: int) -> str:
    return f"DCT{index:03d}"


def _demo_plot_code(index: int) -> str:
    return f"DPLT-{index:03d}"


def _demo_tenancy_id(index: int) -> str:
    return f"DTEN-{index:03d}"


def _demo_bill_code(index: int) -> str:
    return f"DBILL-{index:03d}"


def _text_value(table_name: str, column_name: str, index: int) -> str:
    normalized = column_name.lower()
    if column_name == "Name" or normalized == "ind_org_name":
        return f"Demo Tenant {index:03d}"
    if normalized in {"customer_code", "customercode", "cust_code"}:
        return _demo_customer_code(index)
    if normalized == "plot_code" or normalized.startswith("plot_no_"):
        return _demo_plot_code(index)
    if normalized == "plot_id" and table_name != "dim_plot":
        return _demo_plot_code(index)
    if normalized == "tenancy_id":
        return _demo_tenancy_id(index)
    if normalized in {"agreement_number", "agreementno", "leaseagreementno"}:
        return f"DEMO-LEASE-{index:03d}"
    if normalized in {"bill_code", "billnumber"}:
        return _demo_bill_code(index)
    if "date" in normalized or normalized in {"periodfrom", "periodto"}:
        return f"2024-01-{index:02d}"
    if any(part in normalized for part in ("amount", "rent", "rate", "area", "interest")):
        return f"{1000 + index}.00"
    if normalized.startswith(("is_", "billable", "renewable", "on_hold")):
        return "Yes"
    if "status" in normalized:
        return "ACTIVE"
    if "description" in normalized or "remark" in normalized:
        return f"Synthetic local demo value {index:03d}"
    return f"DEMO-{table_name.upper()}-{column_name.upper()}-{index:03d}"


def demo_value(table_name: str, column: ColumnSpec, index: int) -> object:
    """Return a non-sensitive, type-compatible synthetic value for one column."""

    normalized = column.name.lower()
    if any(part in normalized for part in SENSITIVE_COLUMN_PARTS):
        raise ValueError(f"refusing to synthesize a sensitive column: {column.name}")
    if normalized == "tenant_id":
        return index
    if normalized == "customerid" or normalized == "customer_id":
        return 10000 + index
    if normalized == "applicant_id":
        return 20000 + index
    if normalized == "plot_id" and table_name == "dim_plot":
        return 30000 + index
    if normalized == "bill_id":
        return 70000 + index
    if normalized == "cash_bill_id":
        return 70000 + index
    if normalized == "payment_history_id":
        return 80000 + index
    if normalized == "lease_particulars_id":
        return 60000 + index
    if column.data_type in {"character varying", "character", "text"}:
        value = _text_value(table_name, column.name, index)
        if column.maximum_length is not None:
            return value[: column.maximum_length]
        return value
    if normalized.endswith("_id") or normalized == "id":
        return 90000 + index
    if column.data_type in {"smallint", "integer", "bigint"}:
        return index
    if column.data_type in {"numeric", "decimal", "real", "double precision", "money"}:
        return Decimal("1000.00") + Decimal(index)
    if column.data_type == "boolean":
        return True
    if column.data_type == "date":
        return date(2024, 1, index)
    if column.data_type.startswith("timestamp"):
        return datetime(2024, 1, index, 9, 0, 0)
    raise ValueError(f"unsupported demo column type: {column.data_type}")


def _columns(connection: psycopg.Connection[Any], table_name: str) -> tuple[ColumnSpec, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (SOURCE_SCHEMA, table_name),
        )
        return tuple(
            ColumnSpec(str(name), str(data_type), maximum_length)
            for name, data_type, maximum_length in cursor.fetchall()
        )


def _table_has_rows(connection: psycopg.Connection[Any], table_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{})").format(
                sql.Identifier(DEMO_SCHEMA),
                sql.Identifier(table_name),
            )
        )
        result = cursor.fetchone()
        if result is None:
            raise RuntimeError(f"existence check returned no result for {table_name}")
        return bool(result[0])


def _create_schema_and_tables(connection: psycopg.Connection[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(DEMO_SCHEMA))
        )
        for table_name in WORKFLOW_TABLES:
            cursor.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {}.{} (LIKE {}.{} INCLUDING DEFAULTS)").format(
                    sql.Identifier(DEMO_SCHEMA),
                    sql.Identifier(table_name),
                    sql.Identifier(SOURCE_SCHEMA),
                    sql.Identifier(table_name),
                )
            )


def _insert_rows(
    connection: psycopg.Connection[Any],
    table_name: str,
    columns: Sequence[ColumnSpec],
) -> None:
    column_names = [column.name for column in columns]
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(DEMO_SCHEMA),
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(name) for name in column_names),
        sql.SQL(", ").join(sql.Placeholder(name) for name in column_names),
    )
    rows = [
        {column.name: demo_value(table_name, column, index) for column in columns}
        for index in range(1, ROW_COUNT + 1)
    ]
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)


def _create_workflow_view(connection: psycopg.Connection[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE VIEW pms_demo.workflow_overview AS
            SELECT
                tenant."TenantID" AS tenant_id,
                tenant."Name" AS tenant_name,
                plot.plot_code,
                tenancy.tenancy_id,
                lease.cust_code,
                bill.bill_code,
                bill.final_amount AS billed_amount,
                payment.amount AS payment_amount,
                bill.due_date,
                payment.payment_date
            FROM pms_demo.dim_tenant_legacy AS tenant
            JOIN pms_demo.dim_customer_legacy AS customer
              ON customer.customerid = tenant."CustomerID"
            JOIN pms_demo.bridge_letout_tenancy_plot AS tenancy
              ON tenancy.tenant_id = tenant."TenantID"
             AND tenancy.customer_code = customer.customercode
            JOIN pms_demo.dim_plot AS plot
              ON plot.plot_code = tenancy.plot_id
            JOIN pms_demo.dim_lease_particulars_snapshot AS lease
              ON lease.cust_code = tenancy.customer_code
            JOIN pms_demo.fact_monthly_bills AS bill
              ON bill.tenancy_id = tenancy.tenancy_id
            JOIN pms_demo.fact_payment_current AS payment
              ON payment.bill_code = bill.bill_code
            """
        )


def _create_relationship_constraints(connection: psycopg.Connection[Any]) -> None:
    """Add explicit demo-only keys so DBeaver shows the workflow relationships."""

    def add_constraint(table_name: str, definition: str) -> str:
        return f"ALTER TABLE {DEMO_SCHEMA}.{table_name} ADD CONSTRAINT {definition}"

    statements = (
        add_constraint("dim_tenant_legacy", 'pk_demo_tenant PRIMARY KEY ("TenantID")'),
        add_constraint("dim_customer_legacy", "pk_demo_customer PRIMARY KEY (customerid)"),
        add_constraint("dim_customer_legacy", "uq_demo_customer_code UNIQUE (customercode)"),
        add_constraint("dim_plot", "pk_demo_plot PRIMARY KEY (plot_id)"),
        add_constraint("dim_plot", "uq_demo_plot_code UNIQUE (plot_code)"),
        add_constraint(
            "bridge_letout_tenancy_plot",
            "pk_demo_tenancy_plot PRIMARY KEY (sr_no)",
        ),
        add_constraint(
            "bridge_letout_tenancy_plot",
            "uq_demo_tenancy UNIQUE (tenancy_id)",
        ),
        add_constraint(
            "bridge_letout_tenancy_plot",
            "fk_demo_tenancy_tenant FOREIGN KEY (tenant_id) "
            'REFERENCES pms_demo.dim_tenant_legacy ("TenantID")',
        ),
        add_constraint(
            "bridge_letout_tenancy_plot",
            "fk_demo_tenancy_customer FOREIGN KEY (customer_code) "
            "REFERENCES pms_demo.dim_customer_legacy (customercode)",
        ),
        add_constraint(
            "bridge_letout_tenancy_plot",
            "fk_demo_tenancy_plot FOREIGN KEY (plot_id) "
            "REFERENCES pms_demo.dim_plot (plot_code)",
        ),
        add_constraint(
            "dim_lease_particulars_snapshot",
            "pk_demo_lease PRIMARY KEY (lease_particulars_id)",
        ),
        add_constraint(
            "dim_lease_particulars_snapshot",
            "fk_demo_lease_customer FOREIGN KEY (cust_code) "
            "REFERENCES pms_demo.dim_customer_legacy (customercode)",
        ),
        add_constraint("fact_monthly_bills", "pk_demo_bill PRIMARY KEY (bill_id)"),
        add_constraint("fact_monthly_bills", "uq_demo_bill_code UNIQUE (bill_code)"),
        add_constraint(
            "fact_monthly_bills",
            "fk_demo_bill_tenancy FOREIGN KEY (tenancy_id) "
            "REFERENCES pms_demo.bridge_letout_tenancy_plot (tenancy_id)",
        ),
        add_constraint(
            "fact_payment_current",
            "pk_demo_payment PRIMARY KEY (payment_history_id)",
        ),
        add_constraint(
            "fact_payment_current",
            "fk_demo_payment_bill_id FOREIGN KEY (cash_bill_id) "
            "REFERENCES pms_demo.fact_monthly_bills (bill_id)",
        ),
        add_constraint(
            "fact_payment_current",
            "fk_demo_payment_bill_code FOREIGN KEY (bill_code) "
            "REFERENCES pms_demo.fact_monthly_bills (bill_code)",
        ),
    )
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def provision(database_url: str, *, apply: bool) -> tuple[tuple[str, int, int], ...]:
    """Provision the isolated schema and return table, row and column counts."""

    if not apply:
        raise ValueError("refusing to change the database without --apply")
    normalized_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(normalized_url) as connection:
        _create_schema_and_tables(connection)
        if any(_table_has_rows(connection, table_name) for table_name in WORKFLOW_TABLES):
            raise ValueError(
                "pms_demo already contains workflow rows; refusing to append duplicates"
            )
        results: list[tuple[str, int, int]] = []
        for table_name in WORKFLOW_TABLES:
            columns = _columns(connection, table_name)
            _insert_rows(connection, table_name, columns)
            results.append((table_name, ROW_COUNT, len(columns)))
        _create_relationship_constraints(connection)
        _create_workflow_view(connection)
        return tuple(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PMS_DEMO_DATABASE_URL"),
        help="administrator PostgreSQL URL; may be supplied through PMS_DEMO_DATABASE_URL",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create pms_demo and insert synthetic rows",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or PMS_DEMO_DATABASE_URL is required")
    for table_name, rows, columns in provision(args.database_url, apply=args.apply):
        print(f"PASS {DEMO_SCHEMA}.{table_name} rows={rows} columns={columns}")
    print(f"PASS {DEMO_SCHEMA}.workflow_overview")


if __name__ == "__main__":
    main()
