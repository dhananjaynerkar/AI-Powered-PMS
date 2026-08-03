from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pms_structured.models import ConstrainedSelectPlan, StructuredQuery
from pms_structured.templates import (
    ApprovedTemplateRegistry,
    ConstrainedPlanCompiler,
    SqlSafetyError,
)
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_all_approved_templates_are_select_only_and_bounded() -> None:
    registry = ApprovedTemplateRegistry(PROJECT_ROOT / "sql" / "approved_queries")

    assert registry.template_ids == {
        "tenant_profile",
        "tenancy_profile",
        "plot_profile",
        "current_agreement",
        "bills",
        "payments",
        "outstanding",
        "inspections",
        "legal_cases",
    }


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM pms_app.bill_360 LIMIT :limit",
        "SELECT * FROM pms_app.bill_360 LIMIT :limit",
        "SELECT bill_code FROM pms_extract_2010_2023.fact_monthly_bills LIMIT :limit",
        "SELECT bill_code FROM pms_app.bill_360; DROP TABLE x",
    ],
)
def test_template_validator_rejects_unsafe_sql(sql: str) -> None:
    registry = ApprovedTemplateRegistry(PROJECT_ROOT / "sql" / "approved_queries")

    with pytest.raises(SqlSafetyError):
        registry.validate(sql, expected_view="bill_360")


def test_http_query_contract_forbids_raw_sql() -> None:
    with pytest.raises(ValidationError):
        StructuredQuery.model_validate(
            {
                "question": "show bills",
                "sql": "SELECT * FROM pms_extract_2010_2023.fact_monthly_bills",
            }
        )


def test_internal_ast_compiler_uses_only_allowlisted_identifiers_and_binds() -> None:
    compiler = ConstrainedPlanCompiler(
        {"bill_360": frozenset({"bill_date", "final_amount", "bill_status"})}
    )
    sql, params = compiler.compile(
        ConstrainedSelectPlan(
            view="bill_360",
            columns=("bill_date", "final_amount"),
            filters=(("bill_date", ">=", date(2026, 1, 1)),),
            order_by=(("bill_date", "DESC"),),
            limit=25,
        )
    )

    assert "pms_app.\"bill_360\"" in sql
    assert ":value_0" in sql
    assert params["value_0"] == date(2026, 1, 1)
    assert params["limit"] == 25
    assert "2026-01-01" not in sql


def test_internal_ast_rejects_denied_columns() -> None:
    compiler = ConstrainedPlanCompiler(
        {"bill_360": frozenset({"bill_date", "final_amount"})}
    )

    with pytest.raises(SqlSafetyError, match="denied column"):
        compiler.compile(
            ConstrainedSelectPlan(
                view="bill_360",
                columns=("password",),
            )
        )
