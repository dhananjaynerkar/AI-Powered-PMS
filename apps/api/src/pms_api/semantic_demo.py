"""Local-model semantic planning for the controlled demonstration only.

The model receives a small, reviewed view catalog and returns a typed plan. It
never receives credentials, raw schema metadata, or permission to emit SQL.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
from pms_common.security import (
    AuthorizationContext,
    AuthorizationService,
    Permission,
    apply_postgres_session_context,
)
from pms_common.settings import Settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, text


class SemanticDemoError(RuntimeError):
    """Raised when a semantic demo request cannot safely execute."""


class SemanticPlanFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    column: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    operator: Literal["=", "!=", "<", "<=", ">", ">="]
    value: str | int | float | bool


class SemanticPlanOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    column: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    direction: Literal["ASC", "DESC"] = "ASC"


class SemanticPlan(BaseModel):
    """Typed plan accepted from the local model after independent validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    columns: tuple[str, ...] = Field(default=(), max_length=6)
    filters: tuple[SemanticPlanFilter, ...] = Field(default=(), max_length=4)
    aggregate: Literal["none", "count"] = "none"
    group_by: tuple[str, ...] = Field(default=(), max_length=3)
    order_by: tuple[SemanticPlanOrder, ...] = Field(default=(), max_length=3)
    limit: int = Field(default=10, ge=1, le=20)


@dataclass(frozen=True, slots=True)
class SemanticView:
    name: str
    description: str
    columns: tuple[str, ...]
    numeric_columns: frozenset[str] = frozenset()
    boolean_columns: frozenset[str] = frozenset()
    date_columns: frozenset[str] = frozenset()


_VIEWS: dict[str, SemanticView] = {
    "semantic_division_reference": SemanticView(
        "semantic_division_reference",
        "port divisions and operational status",
        ("div_code", "div_name", "status"),
    ),
    "semantic_estate_reference": SemanticView(
        "semantic_estate_reference",
        "port estates and operational status",
        ("estate_code", "estate_name", "status"),
    ),
    "semantic_unit_reference": SemanticView(
        "semantic_unit_reference",
        "port units and operational status",
        ("unit_code", "unit_desc", "status"),
    ),
    "semantic_plot_summary": SemanticView(
        "semantic_plot_summary",
        "plot code, area, status, vacancy flag and zone",
        ("plot_code", "area", "status", "is_vacant", "zone_id"),
        frozenset({"area", "zone_id"}),
        frozenset({"is_vacant"}),
    ),
    "semantic_approved_lease_summary": SemanticView(
        "semantic_approved_lease_summary",
        "approved lease type, billing periodicity, dates and renewal flag",
        (
            "tenancy_type",
            "lease_type_id",
            "bill_periodicity",
            "duration_from",
            "duration_to",
            "renewal_date",
            "is_renewable",
            "status",
        ),
        frozenset({"lease_type_id"}),
        frozenset({"is_renewable"}),
    ),
    "semantic_recent_bill_summary": SemanticView(
        "semantic_recent_bill_summary",
        "bill date, due date and bill status without individual amounts",
        ("bill_date", "due_date", "bill_status"),
        date_columns=frozenset({"bill_date", "due_date"}),
    ),
}

_FORBIDDEN_QUESTION = re.compile(
    r"(?i)(?:\b(?:insert|update|delete|merge|copy|call|alter|create|drop|"
    r"truncate|grant|revoke|commit|rollback|begin)\b|;|--|/\*|\*/|"
    r"\b(?:public|information_schema|pg_catalog)\s*\.)"
)
_SENSITIVE_QUESTION = re.compile(
    r"(?i)\b(?:tenant|customer|applicant|person(?:al)?|bank|account|address|"
    r"phone|email|password|credential|agreement)\b.*\b(?:all|detail|information|data|"
    r"number|record)s?\b"
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_QUESTION_DATE = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
_QUESTION_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


class LocalSemanticPlanner:
    """Ask the local model for a JSON plan, with one bounded request only."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def plan(self, question: str, *, limit: int) -> SemanticPlan:
        if _FORBIDDEN_QUESTION.search(question) or _SENSITIVE_QUESTION.search(question):
            raise SemanticDemoError("question is outside the controlled data scope")
        payload = {
            "model": self._settings.llm_primary_model,
            "stream": False,
            "think": False,
            "keep_alive": self._settings.llm_keep_alive,
            "format": SemanticPlan.model_json_schema(),
            "options": {
                "temperature": 0,
                "top_p": 1,
                "num_predict": 500,
                "num_ctx": self._settings.llm_context_window,
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only a JSON semantic query plan matching the provided schema. "
                        "Choose exactly one listed view. Never write SQL. Do not request "
                        "personal, tenant, customer, financial-account, address, credential, "
                        "or agreement-identifying data. Use aggregate=count only for counts. "
                        "For a total count without grouping, use an empty columns list. "
                        "Use only columns listed for the selected view. Date filters use "
                        "ISO dates (YYYY-MM-DD). For a whole year, use two filters: "
                        ">= YYYY-01-01 and < the following YYYY-01-01."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "maximum_rows": min(limit, 20),
                            "views": [
                                {
                                    "name": item.name,
                                    "description": item.description,
                                    "columns": item.columns,
                                }
                                for item in _VIEWS.values()
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        try:
            response = httpx.post(
                f"{self._settings.ollama_base_url.rstrip('/')}/api/chat",
                json=payload,
                timeout=self._settings.llm_request_timeout_seconds,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            plan = SemanticPlan.model_validate_json(str(content))
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as error:
            raise SemanticDemoError("local model did not return a valid semantic plan") from error
        bounded = plan.model_copy(update={"limit": min(plan.limit, limit, 20)})
        return _apply_question_date_scope(bounded, question)


class SemanticDemoResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    view: str
    rows: tuple[dict[str, Any], ...]
    row_count: int
    freshness_at: datetime | None
    plan: SemanticPlan


class SemanticDemoService:
    """Execute only independently checked model plans through governed views."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self._planner = LocalSemanticPlanner(settings)
        self._authorization = AuthorizationService()

    def ask(
        self,
        question: str,
        *,
        limit: int,
        context: AuthorizationContext,
    ) -> SemanticDemoResult:
        self._authorization.require_permission(context, Permission.PORT_WIDE_AGGREGATE)
        plan = self._planner.plan(question, limit=limit)
        sql, parameters = self._compile(plan)
        with self._engine.begin() as connection:
            apply_postgres_session_context(connection, context)
            connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{self._settings.text_to_sql_timeout_seconds}s"},
            )
            self._enforce_plan_cost(connection, sql, parameters)
            rows = tuple(dict(row) for row in connection.execute(text(sql), parameters).mappings())
        freshness = _freshness(rows)
        return SemanticDemoResult(
            answer=_render_answer(plan, rows),
            view=plan.view,
            rows=rows,
            row_count=len(rows),
            freshness_at=freshness,
            plan=plan,
        )

    def _compile(self, plan: SemanticPlan) -> tuple[str, dict[str, Any]]:
        view = _VIEWS.get(plan.view)
        if view is None:
            raise SemanticDemoError("model selected an unapproved semantic view")
        if plan.aggregate == "none" and not plan.columns:
            raise SemanticDemoError("a non-aggregate query must select a column")
        if plan.aggregate == "count" and not plan.group_by and plan.columns:
            raise SemanticDemoError("an ungrouped count must not select ordinary columns")
        requested = set(plan.columns)
        filter_columns = {item.column for item in plan.filters}
        group_columns = set(plan.group_by)
        order_columns = {item.column for item in plan.order_by if item.column != "record_count"}
        if not requested | filter_columns | group_columns | order_columns <= set(view.columns):
            raise SemanticDemoError("model selected a denied semantic column")
        if plan.aggregate == "count" and plan.group_by and not set(plan.group_by) <= requested:
            raise SemanticDemoError("count grouping columns must be selected")
        if plan.aggregate == "none" and plan.group_by:
            raise SemanticDemoError("grouping requires a supported aggregate")
        predicates: list[str] = []
        parameters: dict[str, Any] = {"limit": plan.limit}
        for index, item in enumerate(plan.filters):
            if item.column in view.boolean_columns and not isinstance(item.value, bool):
                raise SemanticDemoError("boolean filter requires a boolean value")
            if item.column in view.numeric_columns and not isinstance(item.value, (int, float)):
                raise SemanticDemoError("numeric filter requires a numeric value")
            if item.column in view.date_columns:
                if not isinstance(item.value, str) or not _ISO_DATE.fullmatch(item.value):
                    raise SemanticDemoError("date filter requires an ISO date")
            elif item.column not in view.numeric_columns and item.operator not in {"=", "!="}:
                raise SemanticDemoError("text filters allow equality only")
            parameter = f"value_{index}"
            predicates.append(f'"{item.column}" {item.operator} :{parameter}')
            parameters[parameter] = item.value
        select_items = [f'"{column}"' for column in plan.columns]
        if plan.aggregate == "count":
            select_items.append("count(*) AS record_count")
        sql = "SELECT " + ", ".join(select_items)
        sql += f' FROM pms_app."{view.name}"'
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if plan.aggregate == "count" and plan.group_by:
            sql += " GROUP BY " + ", ".join(f'"{column}"' for column in plan.group_by)
        if plan.order_by:
            sql += " ORDER BY " + ", ".join(
                ("record_count" if item.column == "record_count" else f'"{item.column}"')
                + " "
                + item.direction
                for item in plan.order_by
            )
        sql += " LIMIT :limit"
        return sql, parameters

    def _enforce_plan_cost(
        self,
        connection: Any,
        sql: str,
        parameters: Mapping[str, Any],
    ) -> None:
        result = connection.execute(
            text("EXPLAIN (FORMAT JSON) " + sql), dict(parameters)
        ).scalar_one()
        parsed = json.loads(result) if isinstance(result, str) else result
        try:
            cost = float(parsed[0]["Plan"]["Total Cost"])
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise SemanticDemoError("PostgreSQL returned an invalid plan estimate") from error
        if cost > self._settings.text_to_sql_max_plan_cost:
            raise SemanticDemoError("semantic query exceeds the configured plan-cost limit")


class SemanticDemoProvider(Protocol):
    def __call__(self) -> AbstractContextManager[SemanticDemoService]: ...


class PostgresSemanticDemoProvider:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    @contextmanager
    def __call__(self) -> Iterator[SemanticDemoService]:
        yield SemanticDemoService(self._engine, self._settings)


def _freshness(rows: tuple[dict[str, Any], ...]) -> datetime | None:
    values = [row.get("source_refreshed_at") for row in rows]
    return next((item for item in values if isinstance(item, datetime)), None)


def _apply_question_date_scope(plan: SemanticPlan, question: str) -> SemanticPlan:
    """Apply an explicit question time scope to a date-capable approved view.

    The date value is derived from the question; no business year is hard-coded.
    An explicit model-produced date predicate takes precedence.
    """

    view = _VIEWS.get(plan.view)
    if view is None or not view.date_columns:
        return plan
    if any(item.column in view.date_columns for item in plan.filters):
        return plan
    if len(plan.filters) > 2:
        return plan
    dates = tuple(dict.fromkeys(_QUESTION_DATE.findall(question)))
    if dates:
        return plan.model_copy(
            update={
                "filters": (
                    *plan.filters,
                    SemanticPlanFilter(
                        column=_primary_date_column(view), operator="=", value=dates[-1]
                    ),
                )
            }
        )
    years = tuple(dict.fromkeys(_QUESTION_YEAR.findall(question)))
    if not years:
        return plan
    start_year = min(int(value) for value in years)
    end_year = max(int(value) for value in years) + 1
    date_column = _primary_date_column(view)
    return plan.model_copy(
        update={
            "filters": (
                *plan.filters,
                SemanticPlanFilter(
                    column=date_column, operator=">=", value=f"{start_year:04d}-01-01"
                ),
                SemanticPlanFilter(
                    column=date_column, operator="<", value=f"{end_year:04d}-01-01"
                ),
            )
        }
    )


def _primary_date_column(view: SemanticView) -> str:
    return next(column for column in view.columns if column in view.date_columns)


def _render_answer(plan: SemanticPlan, rows: tuple[dict[str, Any], ...]) -> str:
    view = _VIEWS[plan.view]
    if not rows:
        return f"No matching records were found in the approved {view.description} view."
    if plan.aggregate == "count" and not plan.group_by:
        count = rows[0].get("record_count", 0)
        return f"The approved {view.description} query found {count} matching records."
    lines = []
    display_columns = plan.columns + (("record_count",) if plan.aggregate == "count" else ())
    for index, row in enumerate(rows, start=1):
        facts = "; ".join(
            f"{column.replace('_', ' ')}: {_display(row.get(column))}"
            for column in display_columns
            if column in row
        )
        lines.append(f"{index}. {facts}")
    return (
        f"The approved {view.description} query returned {len(rows)} result"
        f"{'s' if len(rows) != 1 else ''}: " + " ".join(lines)
    )


def _display(value: Any) -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, datetime):
        return value.astimezone(UTC).date().isoformat()
    return str(value)
