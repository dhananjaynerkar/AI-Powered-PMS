from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pms_api.app as app_module
import pytest
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app
from pms_api.demo import (
    _INTENTS,
    DEMO_CONTEXT,
    DemoIdentity,
    DemoQueryRequest,
    DemoRoute,
    DemoStructuredEvidence,
    DemoStructuredService,
    route_demo_question,
)
from pms_api.semantic_demo import SemanticDemoResult, SemanticPlan
from pms_common.settings import Settings
from pms_retrieval.embedding import EmbeddingModelUnavailable
from pms_retrieval.models import ChunkCitation, GroundedAnswer, SourceCitation
from pydantic import SecretStr, ValidationError


def _enabled_settings() -> Settings:
    return Settings(
        app_env="development",
        app_host="127.0.0.1",
        pms_demo_mode=True,
        pms_demo_database_url=SecretStr(
            "postgresql+psycopg://pms_demo_runtime:secret@127.0.0.1/postgres"
        ),
        app_secret_key=SecretStr("test-local-demo-session-secret"),
    )


class StubStructured:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def execute(self, query_id: str, limit: int) -> DemoStructuredEvidence:
        self.calls.append((query_id, limit))
        return DemoStructuredEvidence(
            query_id=query_id,
            database_objects=("pms_demo_access.approved_lease_summary",),
            rows=({"tenancy_type": "LEASE", "status": "APPROVED"},),
            row_count=1,
            freshness_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


class StubRag:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.lexical_only_calls: list[bool] = []

    def ask(
        self,
        question: str,
        *,
        use_extractive_fallback: bool = False,
        lexical_only: bool = False,
    ) -> GroundedAnswer:
        del use_extractive_fallback
        self.calls.append(question)
        self.lexical_only_calls.append(lexical_only)
        return GroundedAnswer(
            answer="Grounded policy answer.",
            sources=(
                SourceCitation(
                    source_id="source-1",
                    document_id="doc-1",
                    document_version_id="version-1",
                    document_title="Approved policy",
                    page_numbers=(2,),
                    section_number="1",
                    clause_number="1.1",
                    citations=(
                        ChunkCitation(block_id="block-1", page_number=2, bounding_box=None),
                    ),
                ),
            ),
            confidence="HIGH",
            review_required=False,
        )

class StubSemantic:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ask(self, question: str, *, limit: int, context: Any) -> SemanticDemoResult:
        assert context == DEMO_CONTEXT
        self.calls.append(question)
        return SemanticDemoResult(
            answer="The approved lease summary query returned 1 result: tenancy type: LEASE.",
            view="semantic_approved_lease_summary",
            rows=({"tenancy_type": "LEASE", "status": "APPROVED"},),
            row_count=1,
            freshness_at=datetime(2026, 1, 1, tzinfo=UTC),
            plan=SemanticPlan(
                view="semantic_approved_lease_summary",
                columns=("tenancy_type", "status"),
                limit=limit,
            ),
        )


class StubAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_demo_query(self, **values: Any) -> None:
        self.events.append(values)


def _app(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> tuple[Any, StubStructured, StubRag, StubSemantic, StubAudit]:
    structured = StubStructured()
    rag = StubRag()
    semantic = StubSemantic()
    audit = StubAudit()
    monkeypatch.setattr(app_module, "_settings", lambda: settings)

    @contextmanager
    def structured_provider() -> Iterator[StubStructured]:
        yield structured

    @contextmanager
    def rag_provider(context: Any) -> Iterator[StubRag]:
        assert context == DEMO_CONTEXT
        yield rag

    @contextmanager
    def audit_provider(context: Any) -> Iterator[StubAudit]:
        assert context == DEMO_CONTEXT
        yield audit

    @contextmanager
    def semantic_provider() -> Iterator[StubSemantic]:
        yield semantic

    app = create_app(
        rag_service_provider=rag_provider,
        audit_service_provider=audit_provider,
        demo_structured_provider=structured_provider,
        demo_semantic_provider=semantic_provider,
    )
    return app, structured, rag, semantic, audit


async def _select_demo_identity(
    client: AsyncClient,
    identity: DemoIdentity = DemoIdentity.DATA_ENTRY_OPERATOR,
) -> None:
    response = await client.post("/api/v1/demo/session", json={"identity": identity.value})
    assert response.status_code == 200


def test_demo_mode_is_disabled_by_default_and_kill_switch_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _, _, _ = _app(monkeypatch, Settings(pms_demo_mode=False))

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/demo/status")).json()["enabled"] is False
            assert (await client.get("/api/v1/demo/me")).status_code == 404
            assert (
                await client.post("/api/v1/demo/query", json={"question": "Show leases"})
            ).status_code == 404

    asyncio.run(scenario())


def test_demo_mode_cannot_run_in_production_or_nonlocal_host() -> None:
    with pytest.raises(ValidationError, match="allowed only in development"):
        Settings(
            app_env="production",
            pms_demo_mode=True,
            pms_demo_database_url=SecretStr("postgresql+psycopg://demo:x@localhost/db"),
        )
    with pytest.raises(ValidationError, match="requires localhost"):
        Settings(
            app_env="development",
            app_host="0.0.0.0",
            pms_demo_mode=True,
            pms_demo_database_url=SecretStr("postgresql+psycopg://demo:x@localhost/db"),
        )


def test_fixed_demo_principal_and_structured_route(monkeypatch: pytest.MonkeyPatch) -> None:
    app, structured, rag, semantic, audit = _app(monkeypatch, _enabled_settings())

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _select_demo_identity(client)
            me = await client.get("/api/v1/demo/me")
            response = await client.post(
                "/api/v1/demo/query", json={"question": "Show five active leases", "limit": 5}
            )
        assert me.json() == {
            "subject": "demo.do",
            "roles": ["Data Entry Operator"],
            "tenant_id": None,
            "department_id": "estate",
            "unit_id": "land",
            "classification": "internal",
        }
        assert response.status_code == 200
        assert response.json()["route"] == "SEMANTIC_QUERY"
        assert "approved lease summary" in response.json()["answer"]

    asyncio.run(scenario())
    assert structured.calls == []
    assert semantic.calls == ["Show five active leases"]
    assert rag.calls == []
    assert audit.events[0]["database_objects"] == (
        "pms_app.semantic_approved_lease_summary",
    )


def test_document_and_sql_routes_remain_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    app, structured, rag, _, _ = _app(monkeypatch, _enabled_settings())

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _select_demo_identity(client)
            document = await client.post(
                "/api/v1/demo/query", json={"question": "Explain the port land policy"}
            )
        assert document.json()["route"] == "DOCUMENT_RAG"
        assert document.json()["document"]["sources"][0]["document_title"] == "Approved policy"

    asyncio.run(scenario())
    assert structured.calls == []
    assert rag.calls == ["Explain the port land policy"]
    assert rag.lexical_only_calls == [False]


def test_rag_runtime_failure_returns_helpful_review_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _, _, audit = _app(monkeypatch, _enabled_settings())

    @contextmanager
    def unavailable_rag_provider(context: Any) -> Iterator[StubRag]:
        assert context == DEMO_CONTEXT
        raise EmbeddingModelUnavailable("private model path must not reach the browser")
        yield StubRag()

    app.state.rag_service_provider = unavailable_rag_provider

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _select_demo_identity(client)
            response = await client.post(
                "/api/v1/demo/query",
                json={"question": "Explain the indexed policy document"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "DOCUMENT_RAG"
        assert payload["review_required"] is True
        assert payload["answer"]
        assert "private model path" not in response.text
        assert payload["warnings"] == ["NO_VERIFIED_EVIDENCE_RETURNED"]

    asyncio.run(scenario())
    assert audit.events[-1]["rejection_reason"] == "EmbeddingModelUnavailable"
    assert audit.events[-1]["response_status"] == "REVIEW_REQUIRED"


def test_postgres_rag_provider_reuses_process_model_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    def resource(name: str) -> object:
        created.append(name)
        return object()

    monkeypatch.setattr(
        app_module,
        "BgeM3EmbeddingAdapter",
        lambda _settings: resource("embedder"),
    )
    monkeypatch.setattr(
        app_module,
        "BgeReranker",
        lambda _settings: resource("reranker"),
    )
    monkeypatch.setattr(
        app_module,
        "OllamaGenerator",
        lambda _settings: resource("generator"),
    )
    monkeypatch.setattr(
        app_module,
        "BgeM3Tokenizer",
        lambda _settings: resource("tokenizer"),
    )
    provider = app_module.PostgresRagServiceProvider(object(), _enabled_settings())

    first = provider._resources()
    second = provider._resources()

    assert second == first
    assert created == ["embedder", "reranker", "generator", "tokenizer"]


@pytest.mark.parametrize(
    "question",
    (
        "What does the indexed document say about reduction in interest?",
        "Summarize the indexed PDF about easement rights.",
        "What do the SoR Rules say about land rates?",
    ),
)
def test_document_words_route_to_rag(question: str) -> None:
    assert route_demo_question(question) == (DemoRoute.DOCUMENT_RAG, None)


@pytest.mark.parametrize(
    "question",
    (
        "DROP TABLE leases",
        "SELECT * FROM public.tenants",
        "Show rows from information_schema.tables",
        "Update every bill",
    ),
)
def test_write_and_unrestricted_table_requests_are_refused(question: str) -> None:
    assert route_demo_question(question) == (DemoRoute.REQUEST_REFUSED, None)


def test_unsafe_demo_question_returns_review_required_and_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, structured, rag, _, audit = _app(monkeypatch, _enabled_settings())

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _select_demo_identity(client)
            response = await client.post(
                "/api/v1/demo/query", json={"question": "Delete a lease record."}
            )
        assert response.status_code == 200
        assert response.json()["route"] == "REQUEST_REFUSED"
        assert response.json()["review_required"] is False

    asyncio.run(scenario())
    assert structured.calls == []
    assert rag.calls == []
    assert audit.events == [
        {
            "question": "Delete a lease record.",
            "route": "REQUEST_REFUSED",
            "query_id": None,
            "database_objects": (),
            "row_count": 0,
            "citation_ids": (),
            "rejection_reason": "prohibited_request",
            "response_status": "DENIED",
            "duration_ms": pytest.approx(audit.events[0]["duration_ms"]),
        }
    ]


def test_sensitive_personal_information_request_is_refused_before_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, structured, rag, _, audit = _app(monkeypatch, _enabled_settings())

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _select_demo_identity(client)
            response = await client.post(
                "/api/v1/demo/query",
                json={"question": "Show all tenant personal information and agreement details."},
            )
        assert response.json()["route"] == "REQUEST_REFUSED"

    asyncio.run(scenario())
    assert structured.calls == []
    assert rag.calls == []
    assert audit.events[0]["response_status"] == "DENIED"


def test_document_question_uses_the_real_retrieval_question_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, rag, _, _ = _app(monkeypatch, _enabled_settings())
    question = "According to the indexed rules, what is an easement?"

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _select_demo_identity(client)
            response = await client.post(
                "/api/v1/demo/query", json={"question": question}
            )
        assert response.status_code == 200
        assert response.json()["route"] == "DOCUMENT_RAG"
        assert response.json()["evidence_extracted"] is False

    asyncio.run(scenario())
    assert rag.calls == [question]


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def one(self) -> Any:
        return self._value

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._value


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "current_user" in sql:
            return _Result(("pms_demo_runtime", "on"))
        if "set_config" in sql:
            return _Result((None,))
        return _Result([{"tenancy_type": "LEASE", "source_refreshed_at": None}])


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self) -> Iterator[_Connection]:
        yield self.connection


def test_demo_sql_applies_timeout_row_limit_and_fixed_view() -> None:
    connection = _Connection()
    service = DemoStructuredService(_Engine(connection), _enabled_settings())
    result = service.execute("approved_leases", 20)
    assert result.row_count == 1
    assert connection.calls[1][1] == {"timeout": "5s"}
    assert connection.calls[2][1] == {"limit": 10}
    assert "pms_demo_access.approved_lease_summary" in connection.calls[2][0]


def test_safe_diagnostics_never_contains_demo_database_secret() -> None:
    diagnostics = _enabled_settings().safe_diagnostics()
    assert "pms_demo_database_url" not in diagnostics
    assert "secret" not in str(diagnostics)


def test_final_demo_sql_proposal_is_restricted_and_one_time_only() -> None:
    sql = Path("sql/demo/003_controlled_demo_access_FINAL_PROPOSAL.sql").read_text(
        encoding="utf-8"
    ).casefold()
    hardening_sql, main_and_rollback = sql.split("main_proposal_begin", maxsplit=1)
    main_sql, rollback_sql = main_and_rollback.split("rollback_sql_begin", maxsplit=1)
    assert "grant select on all tables in schema pms_demo_access" not in sql
    assert "pms_demo_access.approved_lease_summary" in sql
    assert "pms_demo_access.recent_bill_summary" in sql
    assert "grant select on all tables in schema pms_extract_2010_2023" not in sql
    assert "grant select on all tables in schema public" not in sql
    assert "grant all" not in sql
    assert "nobypassrls" in sql
    assert "create or replace" not in sql
    assert "if not exists" not in sql
    assert "select *" not in sql
    assert "cascade" not in sql
    assert "security_barrier = true" in sql
    assert "security_invoker = false" in sql
    assert "create schema pms_demo_access authorization pms_demo_view_owner" in sql
    assert "grant pms_demo_view_owner to pms_demo_runtime" not in sql
    positive_role_attribute = re.compile(
        r"(?m)^\s+(superuser|createdb|createrole|bypassrls|replication)\b"
    )
    assert positive_role_attribute.search(main_sql) is None
    assert "drop " not in main_sql
    assert "drop view" in rollback_sql
    assert "drop schema" in rollback_sql
    assert "drop role pms_demo_runtime" in rollback_sql
    assert "drop role pms_demo_view_owner" in rollback_sql
    assert "alter default privileges" not in sql
    assert "grant create" not in main_sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql
    assert "grant execute" not in sql
    assert "alter view" not in sql
    assert "owner to pms_demo_runtime" not in sql
    assert "agreement_number" not in sql
    assert "bill_code" not in sql
    assert "total_head_amount" not in sql
    assert "total_tax_amount" not in sql
    assert "final_amount" not in sql
    select_grants = {
        match.group("role"): match.group("objects")
        for match in re.finditer(
            r"grant select on(?P<objects>.*?)to (?P<role>pms_demo_[a-z_]+);",
            main_sql,
            re.DOTALL,
        )
    }
    assert select_grants["pms_demo_view_owner"].count("pms_extract_2010_2023.") == 7
    assert "pms_demo_access." not in select_grants["pms_demo_view_owner"]
    assert select_grants["pms_demo_runtime"].count("pms_demo_access.") == 6
    assert "pms_extract_2010_2023." not in select_grants["pms_demo_runtime"]
    assert "revoke create on schema public from public;" in hardening_sql
    assert "grant create on schema public to public;" in hardening_sql
    assert "has_schema_privilege('public', 'public', 'create')" in main_sql
    assert "public usage on public" in main_sql
    assert "public temp on database" in main_sql
    assert "runtime has unapproved database temp" not in main_sql
    assert "has_schema_privilege('pms_demo_runtime', 'public', 'usage')" not in main_sql
    assert "has_schema_privilege('pms_demo_runtime', 'public', 'create')" not in main_sql


def test_demo_templates_exclude_first_demo_identifiers_and_amounts() -> None:
    connection = _Connection()
    service = DemoStructuredService(_Engine(connection), _enabled_settings())
    service.execute("approved_leases", 5)
    lease_sql = connection.calls[2][0].casefold()
    assert "agreement_number" not in lease_sql
    assert "tenant_id" not in lease_sql
    assert "customer" not in lease_sql

    connection = _Connection()
    service = DemoStructuredService(_Engine(connection), _enabled_settings())
    service.execute("recent_bills", 5)
    bill_sql = connection.calls[2][0].casefold()
    assert "bill_code" not in bill_sql
    assert "amount" not in bill_sql
    assert "customer" not in bill_sql
    assert "tenant" not in bill_sql


def test_demo_templates_and_request_contract_cannot_supply_sql_controls() -> None:
    forbidden = re.compile(
        r"(?i)\b(?:create\s+temp|create|insert|update|delete|copy|call|do|alter|"
        r"drop|truncate|grant|revoke|commit|rollback|begin)\b"
    )
    assert set(DemoQueryRequest.model_fields) == {"question", "limit"}
    for intent in _INTENTS.values():
        assert forbidden.search(intent.sql) is None
        assert intent.sql.casefold().lstrip().startswith("select")
        assert "limit :limit" in intent.sql.casefold()
