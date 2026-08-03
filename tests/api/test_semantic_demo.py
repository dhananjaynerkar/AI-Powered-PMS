from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pms_api.semantic_demo import (
    LocalSemanticPlanner,
    SemanticDemoError,
    SemanticDemoService,
    SemanticPlan,
    SemanticPlanFilter,
    SemanticPlanOrder,
    _apply_question_date_scope,
    _render_answer,
)


def _service() -> SemanticDemoService:
    return SemanticDemoService(
        engine=SimpleNamespace(),
        settings=SimpleNamespace(),
    )


def test_compiler_uses_only_approved_view_and_bound_values() -> None:
    sql, parameters = _service()._compile(
        SemanticPlan(
            view="semantic_plot_summary",
            columns=("plot_code", "area", "status"),
            filters=(SemanticPlanFilter(column="is_vacant", operator="=", value=True),),
            limit=5,
        )
    )
    assert 'FROM pms_app."semantic_plot_summary"' in sql
    assert "is_vacant" in sql
    assert "true" not in sql.casefold()
    assert parameters == {"limit": 5, "value_0": True}


def test_compiler_rejects_unknown_columns_and_invalid_count_shape() -> None:
    service = _service()
    with pytest.raises(SemanticDemoError, match="denied semantic column"):
        service._compile(
            SemanticPlan(view="semantic_plot_summary", columns=("tenant_name",))
        )
    with pytest.raises(SemanticDemoError, match="ungrouped count"):
        service._compile(
            SemanticPlan(
                view="semantic_plot_summary",
                columns=("status",),
                aggregate="count",
            )
        )


def test_compiler_supports_a_bounded_year_range_on_bill_dates() -> None:
    sql, parameters = _service()._compile(
        SemanticPlan(
            view="semantic_recent_bill_summary",
            columns=("bill_date", "due_date", "bill_status"),
            filters=(
                SemanticPlanFilter(column="bill_date", operator=">=", value="2018-01-01"),
                SemanticPlanFilter(column="bill_date", operator="<", value="2019-01-01"),
            ),
            order_by=(SemanticPlanOrder(column="bill_date", direction="DESC"),),
            limit=5,
        )
    )
    assert '"bill_date" >= :value_0' in sql
    assert '"bill_date" < :value_1' in sql
    assert parameters == {
        "limit": 5,
        "value_0": "2018-01-01",
        "value_1": "2019-01-01",
    }


def test_compiler_rejects_invalid_date_filter() -> None:
    with pytest.raises(SemanticDemoError, match="ISO date"):
        _service()._compile(
            SemanticPlan(
                view="semantic_recent_bill_summary",
                columns=("bill_date",),
                filters=(SemanticPlanFilter(column="bill_date", operator=">=", value="2018"),),
            )
        )


def test_question_years_become_dynamic_bill_date_ranges() -> None:
    base = SemanticPlan(
        view="semantic_recent_bill_summary",
        columns=("bill_date", "bill_status"),
    )

    one_year = _apply_question_date_scope(base, "Show approved bills in 2011")
    range_of_years = _apply_question_date_scope(base, "Compare bills from 2011 to 2022")

    assert one_year.filters == (
        SemanticPlanFilter(column="bill_date", operator=">=", value="2011-01-01"),
        SemanticPlanFilter(column="bill_date", operator="<", value="2012-01-01"),
    )
    assert range_of_years.filters == (
        SemanticPlanFilter(column="bill_date", operator=">=", value="2011-01-01"),
        SemanticPlanFilter(column="bill_date", operator="<", value="2023-01-01"),
    )


def test_count_plan_and_text_answer_are_non_json() -> None:
    plan = SemanticPlan(view="semantic_division_reference", aggregate="count")
    sql, _ = _service()._compile(plan)
    assert 'FROM pms_app."semantic_division_reference"' in sql
    answer = _render_answer(plan, ({"record_count": 4},))
    assert answer == (
        "The approved port divisions and operational status query found 4 matching records."
    )
    assert "{" not in answer


def test_local_planner_accepts_only_typed_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "message": {
                    "content": (
                        '{"view":"semantic_estate_reference","columns":["estate_code",'
                        '"estate_name"],"filters":[],"aggregate":"none","group_by":[],'
                        '"order_by":[],"limit":20}'
                    )
                }
            }

    captured: dict[str, object] = {}

    def fake_post(*_: object, **kwargs: object) -> Response:
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("pms_api.semantic_demo.httpx.post", fake_post)
    settings = SimpleNamespace(
        llm_primary_model="local-model",
        llm_keep_alive="30m",
        llm_context_window=4096,
        llm_request_timeout_seconds=10,
        ollama_base_url="http://127.0.0.1:11434",
    )
    result = LocalSemanticPlanner(settings).plan("List estates", limit=5)
    assert result.view == "semantic_estate_reference"
    assert result.limit == 5
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["format"]
    assert "SQL" in str(payload["messages"])


def test_local_planner_refuses_raw_sql_and_sensitive_scope() -> None:
    settings = SimpleNamespace()
    planner = LocalSemanticPlanner(settings)
    with pytest.raises(SemanticDemoError, match="outside the controlled data scope"):
        planner.plan("SELECT * FROM public.tenants", limit=5)
    with pytest.raises(SemanticDemoError, match="outside the controlled data scope"):
        planner.plan("Show all tenant personal information", limit=5)


def test_semantic_migration_is_scoped_to_read_only_views() -> None:
    source = Path(
        "db/migrations/versions/20260803_0015_controlled_semantic_demo_views.py"
    ).read_text(encoding="utf-8").casefold()
    assert "pms_extract_2010_2023" in source
    assert "security_barrier = true" in source
    assert "security_invoker = false" in source
    assert "grant select on {schema}.{view_name}" in source
    assert "grant all" not in source
    assert "on schema public" not in source
    assert "create role" not in source
    assert "alter role" not in source
    assert "create schema" not in source
    assert "drop table" not in source
