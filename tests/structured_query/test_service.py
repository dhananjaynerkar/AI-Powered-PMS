from __future__ import annotations

from pathlib import Path

from pms_structured.models import (
    EntityDomain,
    QueryRoute,
    SemanticEntityHit,
    StructuredQuery,
    StructuredRecord,
)
from pms_structured.router import DeterministicRouter
from pms_structured.service import StructuredQueryService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeRepository:
    def __init__(self) -> None:
        self.executed: list[tuple[str, str | None]] = []

    def execute(
        self,
        template_id: str,
        *,
        canonical_entity_id: str | None,
        as_of_date: object,
        limit: int,
    ) -> tuple[StructuredRecord, ...]:
        del as_of_date, limit
        self.executed.append((template_id, canonical_entity_id))
        return ()

    def resolve_identity(
        self,
        domain: EntityDomain,
        *,
        source_schema: str,
        source_table: str,
        source_record_id: str,
    ) -> str | None:
        assert domain == EntityDomain.BILL
        assert source_schema == "pms_extract_2010_2023"
        assert source_table == "fact_monthly_bills"
        assert source_record_id == "44"
        return "bill-canonical-44"


def _service(repository: FakeRepository) -> StructuredQueryService:
    return StructuredQueryService(
        DeterministicRouter(
            PROJECT_ROOT / "config" / "query_router.yml",
            PROJECT_ROOT / "config" / "domain_synonyms.yml",
        ),
        repository,  # type: ignore[arg-type]
    )


def test_service_executes_only_a_structured_template() -> None:
    repository = FakeRepository()
    service = _service(repository)

    document = service.ask(StructuredQuery(question="What does the policy say?"))
    structured = service.ask(
        StructuredQuery(question="Show bill", canonical_entity_id="bill-1")
    )

    assert document.route == QueryRoute.DOCUMENT
    assert repository.executed == [("bills", "bill-1")]
    assert structured.template_id == "bills"


def test_semantic_hit_is_id_first_and_does_not_supply_exact_values() -> None:
    repository = FakeRepository()
    service = _service(repository)

    answer = service.ask_from_semantic_hit(
        "Show bill",
        SemanticEntityHit(
            entity_type=EntityDomain.BILL,
            source_schema="pms_extract_2010_2023",
            source_table="fact_monthly_bills",
            source_record_id="44",
        ),
    )

    assert answer.route == QueryRoute.STRUCTURED
    assert repository.executed == [("bills", "bill-canonical-44")]
