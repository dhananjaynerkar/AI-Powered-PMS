"""Deterministic router plus exact-fact structured-query orchestration."""

from __future__ import annotations

from pms_common.logging import get_request_id

from pms_structured.models import (
    QueryRoute,
    SemanticEntityHit,
    StructuredAnswer,
    StructuredQuery,
)
from pms_structured.repository import PostgresStructuredRepository
from pms_structured.router import DeterministicRouter


class StructuredQueryService:
    """Return exact SQL facts only for the STRUCTURED route."""

    def __init__(
        self,
        router: DeterministicRouter,
        repository: PostgresStructuredRepository,
    ) -> None:
        self._router = router
        self._repository = repository

    def ask(self, query: StructuredQuery) -> StructuredAnswer:
        decision = self._router.route(query.question)
        correlation_id = get_request_id()
        if decision.route == QueryRoute.CLARIFY:
            return StructuredAnswer(
                answer=decision.clarification or "Clarification is required.",
                route=decision.route,
                confidence="LOW",
                warnings=("No database query was executed.",),
                review_required=True,
                correlation_id=correlation_id,
            )
        if decision.route == QueryRoute.REFUSE:
            return StructuredAnswer(
                answer="The request cannot be executed safely.",
                route=decision.route,
                confidence="HIGH",
                warnings=("No database query was executed.",),
                review_required=False,
                correlation_id=correlation_id,
            )
        if decision.route != QueryRoute.STRUCTURED or decision.template_id is None:
            return StructuredAnswer(
                answer=f"Route selected: {decision.route.value}.",
                route=decision.route,
                confidence="HIGH",
                warnings=("No structured SQL template was executed.",),
                review_required=False,
                correlation_id=correlation_id,
            )
        records = self._repository.execute(
            decision.template_id,
            canonical_entity_id=query.canonical_entity_id,
            as_of_date=query.as_of_date,
            limit=query.limit,
        )
        return StructuredAnswer(
            answer=(
                f"Retrieved {len(records)} authorized record"
                f"{'' if len(records) == 1 else 's'} from an approved query."
            ),
            route=decision.route,
            template_id=decision.template_id,
            records=records,
            confidence="HIGH",
            warnings=() if records else ("No authorized matching record was found.",),
            review_required=False,
            correlation_id=correlation_id,
        )

    def ask_from_semantic_hit(
        self,
        question: str,
        hit: SemanticEntityHit,
        *,
        limit: int = 50,
    ) -> StructuredAnswer:
        """Use semantic search only for ID discovery, then re-read exact SQL facts."""

        canonical_id = self._repository.resolve_identity(
            hit.entity_type,
            source_schema=hit.source_schema,
            source_table=hit.source_table,
            source_record_id=hit.source_record_id,
        )
        if canonical_id is None:
            return StructuredAnswer(
                answer="The semantic hit has no authorized reviewed identity mapping.",
                route=QueryRoute.STRUCTURED,
                confidence="LOW",
                warnings=("No exact value was accepted from the semantic hit.",),
                review_required=True,
                correlation_id=get_request_id(),
            )
        return self.ask(
            StructuredQuery(
                question=question,
                canonical_entity_id=canonical_id,
                limit=limit,
            )
        )
