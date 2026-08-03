"""Checked-in SQL templates and a typed, identifier-allowlisted AST compiler."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pms_structured.models import ConstrainedSelectPlan


class SqlSafetyError(ValueError):
    """Raised before PostgreSQL sees a query outside the approved contract."""


@dataclass(frozen=True, slots=True)
class ApprovedTemplate:
    template_id: str
    domain: str
    view: str
    sql: str


class ApprovedTemplateRegistry:
    """Load and validate only files named by the checked-in manifest."""

    _forbidden = re.compile(
        r"\b(insert|update|delete|merge|drop|truncate|alter|grant|revoke|copy|program|call|do)\b",
        re.IGNORECASE,
    )
    _relation = re.compile(r"\b(?:from|join)\s+([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)", re.I)

    def __init__(self, directory: Path, *, max_joins: int = 8) -> None:
        self._directory = directory.resolve()
        self._max_joins = max_joins
        self._templates = self._load_manifest()

    def _load_manifest(self) -> dict[str, ApprovedTemplate]:
        try:
            manifest = json.loads((self._directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SqlSafetyError("approved SQL manifest is unavailable or invalid") from error
        raw_templates = manifest.get("templates") if isinstance(manifest, dict) else None
        if not isinstance(raw_templates, dict):
            raise SqlSafetyError("approved SQL manifest has no templates object")
        loaded: dict[str, ApprovedTemplate] = {}
        for template_id, raw in raw_templates.items():
            if not isinstance(template_id, str) or not isinstance(raw, dict):
                raise SqlSafetyError("invalid approved SQL manifest entry")
            filename = raw.get("file")
            domain = raw.get("domain")
            view = raw.get("view")
            if not all(isinstance(value, str) for value in (filename, domain, view)):
                raise SqlSafetyError(f"invalid approved SQL metadata: {template_id}")
            path = (self._directory / str(filename)).resolve()
            if path.parent != self._directory or path.suffix != ".sql":
                raise SqlSafetyError(f"unsafe approved SQL path: {template_id}")
            sql = path.read_text(encoding="utf-8").strip()
            self.validate(sql, expected_view=str(view))
            loaded[template_id] = ApprovedTemplate(
                template_id=template_id,
                domain=str(domain),
                view=str(view),
                sql=sql,
            )
        return loaded

    def validate(self, sql: str, *, expected_view: str) -> None:
        normalized = " ".join(sql.split())
        if not normalized.casefold().startswith(("select ", "with ")):
            raise SqlSafetyError("approved query must be SELECT-only")
        if ";" in sql or "--" in sql or "/*" in sql or "*/" in sql:
            raise SqlSafetyError("comments and multiple statements are forbidden")
        if self._forbidden.search(normalized):
            raise SqlSafetyError("mutating or privileged SQL is forbidden")
        if re.search(r"(?<!\w)\*(?!\w)", normalized):
            raise SqlSafetyError("SELECT star is forbidden")
        if len(re.findall(r"\bjoin\b", normalized, flags=re.I)) > self._max_joins:
            raise SqlSafetyError("approved query exceeds the join limit")
        relations = self._relation.findall(normalized)
        if relations != [("pms_app", expected_view)]:
            raise SqlSafetyError("approved query must use exactly one governed pms_app view")
        if not re.search(r"\blimit\s+:limit\b", normalized, flags=re.I):
            raise SqlSafetyError("approved query must enforce a bound limit")

    def get(self, template_id: str) -> ApprovedTemplate:
        try:
            return self._templates[template_id]
        except KeyError as error:
            raise SqlSafetyError("query template is not approved") from error

    @property
    def template_ids(self) -> frozenset[str]:
        return frozenset(self._templates)


class ConstrainedPlanCompiler:
    """Compile an internal typed AST against catalog-provided allowlists."""

    _operators = frozenset({"=", "<", "<=", ">", ">=", "!="})

    def __init__(self, approved_columns: dict[str, frozenset[str]]) -> None:
        self._approved_columns = approved_columns

    def compile(self, plan: ConstrainedSelectPlan) -> tuple[str, dict[str, Any]]:
        allowed = self._approved_columns.get(plan.view)
        if allowed is None:
            raise SqlSafetyError("analytical view is not approved")
        requested = set(plan.columns)
        filter_columns = {item[0] for item in plan.filters}
        order_columns = {item[0] for item in plan.order_by}
        if not requested | filter_columns | order_columns <= allowed:
            raise SqlSafetyError("analytical plan contains a denied column")
        params: dict[str, Any] = {"limit": plan.limit}
        predicates: list[str] = []
        for index, (column, operator, value) in enumerate(plan.filters):
            if operator not in self._operators:
                raise SqlSafetyError("analytical plan contains a denied operator")
            name = f"value_{index}"
            predicates.append(f'"{column}" {operator} :{name}')
            params[name] = value
        orders: list[str] = []
        for column, direction in plan.order_by:
            normalized_direction = direction.upper()
            if normalized_direction not in {"ASC", "DESC"}:
                raise SqlSafetyError("analytical plan contains a denied sort direction")
            orders.append(f'"{column}" {normalized_direction}')
        select_clause = ", ".join(f'"{column}"' for column in plan.columns)
        sql = f'SELECT {select_clause} FROM pms_app."{plan.view}"'
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if orders:
            sql += " ORDER BY " + ", ".join(orders)
        sql += " LIMIT :limit"
        return sql, params
