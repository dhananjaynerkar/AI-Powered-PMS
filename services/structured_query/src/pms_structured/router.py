"""Deterministic query taxonomy; no model chooses authorization or engine."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pms_structured.models import EntityDomain, QueryRoute, RouteDecision


class RouterConfigurationError(ValueError):
    """Raised when checked-in routing configuration is invalid."""


class DeterministicRouter:
    """Route explicit domain language using checked-in, reviewable rules."""

    def __init__(self, router_path: Path, synonyms_path: Path) -> None:
        self._config = self._load_object(router_path)
        self._synonyms = self._load_object(synonyms_path)
        templates = self._config.get("structured_templates")
        if not isinstance(templates, dict):
            raise RouterConfigurationError("structured_templates must be an object")
        self._templates = {str(key): str(value) for key, value in templates.items()}

    @staticmethod
    def _load_object(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RouterConfigurationError(f"invalid routing configuration: {path}") from error
        if not isinstance(value, dict):
            raise RouterConfigurationError(f"routing configuration must be an object: {path}")
        return value

    @staticmethod
    def _contains(text: str, phrase: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)", text) is not None

    def _matches(self, text: str, key: str) -> bool:
        values = self._synonyms.get(key, ())
        return isinstance(values, list) and any(
            isinstance(value, str) and self._contains(text, value)
            for value in values
        )

    def route(self, question: str) -> RouteDecision:
        normalized = " ".join(question.casefold().split())
        refuse_patterns = self._config.get("refuse_patterns", ())
        if isinstance(refuse_patterns, list) and any(
            isinstance(value, str) and value.casefold() in normalized
            for value in refuse_patterns
        ):
            return RouteDecision(
                route=QueryRoute.REFUSE,
                reason_code="unsafe_or_instructional_input",
            )

        structured = [
            EntityDomain(domain)
            for domain in self._templates
            if self._matches(normalized, domain)
        ]
        document = self._matches(normalized, "policy")
        forecast = self._matches(normalized, "prediction")
        calculation = any(
            self._contains(normalized, phrase)
            for phrase in ("calculate", "calculation", "tax calculation", "rent escalation")
        )
        graph = any(
            self._contains(normalized, phrase)
            for phrase in ("relationship", "related to", "impact path", "graph path")
        )

        route_count = sum((bool(structured), document, forecast, calculation, graph))
        if route_count > 1:
            return RouteDecision(
                route=QueryRoute.HYBRID,
                reason_code="multiple_evidence_engines_required",
            )
        if document:
            return RouteDecision(route=QueryRoute.DOCUMENT, reason_code="document_domain")
        if forecast:
            return RouteDecision(route=QueryRoute.FORECAST, reason_code="forecast_domain")
        if calculation:
            return RouteDecision(
                route=QueryRoute.RULE_CALCULATION,
                reason_code="deterministic_calculation_domain",
            )
        if graph:
            return RouteDecision(route=QueryRoute.GRAPH, reason_code="relationship_domain")
        if len(structured) > 1:
            return RouteDecision(
                route=QueryRoute.CLARIFY,
                reason_code="multiple_structured_domains",
                needs_clarification=True,
                clarification=(
                    "Specify one record type: "
                    + ", ".join(item.value for item in structured)
                ),
            )
        if len(structured) == 1:
            domain = structured[0]
            return RouteDecision(
                route=QueryRoute.STRUCTURED,
                domain=domain,
                template_id=self._templates[domain.value],
                reason_code="approved_structured_template",
            )
        return RouteDecision(
            route=QueryRoute.CLARIFY,
            reason_code="no_supported_domain",
            needs_clarification=True,
            clarification=(
                "Specify whether this concerns a tenant, tenancy, plot, agreement, bill, "
                "payment, outstanding amount, inspection, legal case, policy, calculation, "
                "relationship, or forecast."
            ),
        )
