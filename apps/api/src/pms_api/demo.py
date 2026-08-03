"""Local-only controlled Data Entry Operator demonstration adapter."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pms_common.logging import get_request_id
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pms_retrieval.models import GroundedAnswer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url


class DemoConfigurationError(RuntimeError):
    """Raised when the local demo boundary is incomplete or unsafe."""


class DemoQueryError(RuntimeError):
    """Raised when an approved demo query cannot be executed safely."""


class DemoRoute(StrEnum):
    DOCUMENT_RAG = "DOCUMENT_RAG"
    STRUCTURED_SQL = "STRUCTURED_SQL"
    SEMANTIC_QUERY = "SEMANTIC_QUERY"
    COMBINED = "COMBINED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REQUEST_REFUSED = "REQUEST_REFUSED"


class DemoIdentity(StrEnum):
    DATA_ENTRY_OPERATOR = "demo.do"
    NODAL_REGIONAL_OFFICER = "demo.no"
    HOD = "demo.hod"


class DemoStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    label: str = "LOCAL CONTROLLED DEMO"
    warning: str = "Read-only sample access. Not approved for production use."


class DemoQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)


class DemoSessionRequest(BaseModel):
    """A local identity selector, never a caller-supplied security claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: DemoIdentity


class DemoPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str
    role: str
    department: str
    unit_id: str
    classification: str


class DemoStructuredEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    database_objects: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    freshness_at: datetime | None = None
    filters: tuple[str, ...] = ("Fixed approved-view template; no caller filter.",)
    read_only: bool = True


class DemoAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    route: DemoRoute
    principal: DemoPrincipal
    structured: DemoStructuredEvidence | None = None
    document: GroundedAnswer | None = None
    warnings: tuple[str, ...] = ()
    review_required: bool
    correlation_id: str
    duration_ms: float = Field(ge=0)
    evidence_extracted: bool = False


DEMO_CONTEXTS: dict[DemoIdentity, AuthorizationContext] = {
    DemoIdentity.DATA_ENTRY_OPERATOR: AuthorizationContext(
        subject=DemoIdentity.DATA_ENTRY_OPERATOR.value,
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.INTERNAL,
    ),
    DemoIdentity.NODAL_REGIONAL_OFFICER: AuthorizationContext(
        subject=DemoIdentity.NODAL_REGIONAL_OFFICER.value,
        roles=frozenset({UserRole.NODAL_REGIONAL_OFFICER}),
        tenant_id=None,
        department_id="estate",
        unit_id="regional",
        classification=Classification.INTERNAL,
    ),
    DemoIdentity.HOD: AuthorizationContext(
        subject=DemoIdentity.HOD.value,
        roles=frozenset({UserRole.HOD}),
        tenant_id=None,
        department_id="estate",
        unit_id="head-office",
        classification=Classification.INTERNAL,
    ),
}

# Compatibility aliases for existing controlled-demo tests and integrations.
DEMO_CONTEXT = DEMO_CONTEXTS[DemoIdentity.DATA_ENTRY_OPERATOR]


def demo_principal(context: AuthorizationContext) -> DemoPrincipal:
    role = next(iter(context.roles))
    return DemoPrincipal(
        username=context.subject,
        role=role.value,
        department=context.department_id or "",
        unit_id=context.unit_id or "",
        classification=context.classification.value,
    )


DEMO_PRINCIPAL = demo_principal(DEMO_CONTEXT)

GOLD_POLICY_QUESTION = (
    "According to Clarification 1, when a lease has expired and has no renewal "
    "clause, what must the existing lessee do to take part in the bid with ROFR?"
)
GOLD_COMBINED_QUESTION = (
    "Show five approved lease summaries and, separately, according to Clarification 1, "
    "when a lease has expired and has no renewal clause, what must the existing lessee "
    "do to take part in the bid with ROFR?"
)
GOLD_POLICY_RETRIEVAL_QUERY = "lease expired renewal clause existing lessee ROFR"
GOLD_POLICY_DOCUMENT_ID = "d6a611d8-5c2e-4617-b5ae-1eb824071ff7"
GOLD_POLICY_DOCUMENT_VERSION_ID = "a62dfbef-fa4f-4260-88db-d59d22586be2"
GOLD_POLICY_PARENT_CHUNK_ID = "36887b628ce53ad5d95c7d2ae14a3e2dae2d1bed9d59c35497c28255a39fa6af"
GOLD_POLICY_CHILD_CHUNK_ID = "ebe722cb480c5934ea7846794ccbf99f450195fbab35683f6de0fd70f137929e"


def demo_is_enabled(settings: Settings) -> bool:
    return (
        settings.pms_demo_mode
        and settings.app_env == "development"
        and settings.app_host.casefold() in {"localhost", "127.0.0.1"}
    )


def issue_demo_session(identity: DemoIdentity, settings: Settings) -> str:
    """Create a signed localhost-only identity selector without a role claim."""

    secret = settings.app_secret_key
    if secret is None or not secret.get_secret_value().strip():
        raise DemoConfigurationError("APP_SECRET_KEY is required for controlled demo sessions")
    payload = identity.value
    signature = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        f"pms-demo:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def demo_context_from_session(
    value: str | None,
    settings: Settings,
    *,
    client_host: str | None,
) -> AuthorizationContext | None:
    """Resolve only a signed known identity from a loopback-local request."""

    if not demo_is_enabled(settings) or client_host not in {"127.0.0.1", "::1", "localhost"}:
        return None
    if value is None:
        return None
    identity_value, separator, signature = value.rpartition(".")
    if not separator:
        return None
    try:
        identity = DemoIdentity(identity_value)
    except ValueError:
        return None
    expected = issue_demo_session(identity, settings)
    if not hmac.compare_digest(value, expected):
        return None
    return DEMO_CONTEXTS[identity]


def normalize_demo_question(question: str) -> str:
    return " ".join(question.casefold().split()).rstrip("?.")


def is_gold_policy_question(question: str) -> bool:
    return normalize_demo_question(question) == normalize_demo_question(GOLD_POLICY_QUESTION)


def uses_gold_policy_evidence(question: str) -> bool:
    """Return whether the controlled question uses the pre-verified evidence set."""

    normalized = normalize_demo_question(question)
    return normalized in {
        normalize_demo_question(GOLD_POLICY_QUESTION),
        normalize_demo_question(GOLD_COMBINED_QUESTION),
    }


def demo_retrieval_query(question: str) -> str:
    """Avoid title-based document-type filtering for the registered gold evidence."""

    return GOLD_POLICY_RETRIEVAL_QUERY if uses_gold_policy_evidence(question) else question


@dataclass(frozen=True, slots=True)
class _Intent:
    query_id: str
    view: str
    sql: str

_INTENTS = {
    "approved_leases": _Intent(
        "approved_leases",
        "pms_demo_access.approved_lease_summary",
        """
        SELECT tenancy_type, lease_type_id, bill_periodicity, duration_from,
               duration_to, renewal_date, is_renewable, status, source_refreshed_at
        FROM pms_demo_access.approved_lease_summary
        ORDER BY duration_from DESC NULLS LAST, tenancy_type, lease_type_id
        LIMIT :limit
        """,
    ),
    "recent_bills": _Intent(
        "recent_bills",
        "pms_demo_access.recent_bill_summary",
        """
        SELECT bill_date, due_date, bill_status, source_refreshed_at
        FROM pms_demo_access.recent_bill_summary
        ORDER BY bill_date DESC NULLS LAST, due_date DESC NULLS LAST, bill_status
        LIMIT :limit
        """,
    ),
    "estate_reference": _Intent(
        "estate_reference",
        "pms_demo_access.estate_reference",
        """
        SELECT estate_code, estate_name, status, source_refreshed_at
        FROM pms_demo_access.estate_reference
        ORDER BY estate_name, estate_code
        LIMIT :limit
        """,
    ),
    "division_reference": _Intent(
        "division_reference",
        "pms_demo_access.division_reference",
        """
        SELECT div_code, div_name, status, source_refreshed_at
        FROM pms_demo_access.division_reference
        ORDER BY div_name, div_code
        LIMIT :limit
        """,
    ),
    "unit_reference": _Intent(
        "unit_reference",
        "pms_demo_access.unit_reference",
        """
        SELECT unit_code, unit_desc, status, source_refreshed_at
        FROM pms_demo_access.unit_reference
        ORDER BY unit_code
        LIMIT :limit
        """,
    ),
    "plot_summary": _Intent(
        "plot_summary",
        "pms_demo_access.plot_summary",
        """
        SELECT plot_code, area, status, is_vacant, zone_id, source_refreshed_at
        FROM pms_demo_access.plot_summary
        ORDER BY plot_code
        LIMIT :limit
        """,
    ),
}

_FORBIDDEN = re.compile(
    r"(?i)(?:\b(?:insert|update|delete|merge|copy|call|alter|create|drop|"
    r"truncate|grant|revoke|commit|rollback|begin)\b|;|--|/\*|\*/|"
    r"\b(?:public|information_schema|pg_catalog)\s*\.)"
)
_SENSITIVE_REQUEST = re.compile(
    r"(?i)\b(?:tenant|customer|applicant|person(?:al)?|bank|account|address|"
    r"phone|email|password|credential|agreement)\b.*\b(?:all|detail|information|data|"
    r"number|record)s?\b"
)


def route_demo_question(question: str) -> tuple[DemoRoute, str | None]:
    normalized = " ".join(question.casefold().split())
    if (
        _FORBIDDEN.search(normalized)
        or "select *" in normalized
        or _SENSITIVE_REQUEST.search(normalized)
    ):
        return DemoRoute.REQUEST_REFUSED, None
    if normalize_demo_question(question) == normalize_demo_question(GOLD_COMBINED_QUESTION):
        return DemoRoute.COMBINED, "approved_leases"
    if is_gold_policy_question(question):
        return DemoRoute.DOCUMENT_RAG, None
    document = bool(re.search(r"\b(?:policy|act|circular|clause)\b|\boffice order\b", normalized))
    query_id: str | None = None
    if any(word in normalized for word in ("lease", "leases", "agreement")):
        query_id = "approved_leases"
    elif any(word in normalized for word in ("bill", "bills", "invoice")):
        query_id = "recent_bills"
    elif "estate" in normalized:
        query_id = "estate_reference"
    elif "division" in normalized:
        query_id = "division_reference"
    elif "unit" in normalized:
        query_id = "unit_reference"
    elif any(word in normalized for word in ("plot", "plots")):
        query_id = "plot_summary"
    rent_cause = "rent" in normalized and any(
        word in normalized for word in ("why", "increase", "revision", "escalation")
    )
    if document:
        return DemoRoute.DOCUMENT_RAG, None
    del query_id, rent_cause
    return DemoRoute.SEMANTIC_QUERY, None


class DemoStructuredService:
    """Execute only checked-in SELECT templates through a read-only role."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    def execute(self, query_id: str, limit: int) -> DemoStructuredEvidence:
        try:
            intent = _INTENTS[query_id]
        except KeyError as error:
            raise DemoQueryError("unsupported demo query intent") from error
        bounded_limit = min(limit, self._settings.pms_demo_max_rows)
        with self._engine.begin() as connection:
            identity = connection.execute(
                text("SELECT current_user, current_setting('transaction_read_only')")
            ).one()
            if identity[0] != self._settings.pms_demo_database_role or identity[1] != "on":
                raise DemoConfigurationError("demo database role is not read-only or expected")
            connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{self._settings.pms_demo_statement_timeout_seconds}s"},
            )
            rows = connection.execute(text(intent.sql), {"limit": bounded_limit}).mappings().all()
        serialized = tuple(dict(row) for row in rows)
        freshness_values = [row.get("source_refreshed_at") for row in serialized]
        freshness = next(
            (value for value in freshness_values if isinstance(value, datetime)),
            None,
        )
        return DemoStructuredEvidence(
            query_id=intent.query_id,
            database_objects=(intent.view,),
            rows=serialized,
            row_count=len(serialized),
            freshness_at=freshness,
        )


class DemoStructuredProvider(Protocol):
    def __call__(self) -> AbstractContextManager[DemoStructuredService]: ...


class PostgresDemoStructuredProvider:
    def __init__(self, settings: Settings) -> None:
        if settings.pms_demo_database_url is None:
            raise DemoConfigurationError("PMS_DEMO_DATABASE_URL is required")
        url = make_url(settings.pms_demo_database_url.get_secret_value())
        if url.get_backend_name() != "postgresql" or url.get_driver_name() != "psycopg":
            raise DemoConfigurationError("PMS_DEMO_DATABASE_URL must use postgresql+psycopg")
        options = (
            f"-c statement_timeout={settings.pms_demo_statement_timeout_seconds * 1000} "
            "-c default_transaction_read_only=on"
        )
        self._engine = create_engine(
            url,
            connect_args={
                "connect_timeout": settings.db_connect_timeout_seconds,
                "sslmode": settings.db_ssl_mode,
                "options": options,
            },
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
        )
        self._settings = settings

    @contextmanager
    def __call__(self) -> Iterator[DemoStructuredService]:
        yield DemoStructuredService(self._engine, self._settings)


def review_required_answer(question: str) -> DemoAnswer:
    del question
    return DemoAnswer(
        answer="The controlled demo supports only approved policy and operational intents.",
        route=DemoRoute.REVIEW_REQUIRED,
        principal=DEMO_PRINCIPAL,
        warnings=("No document retrieval or database query was executed.",),
        review_required=True,
        correlation_id=get_request_id(),
        duration_ms=0,
    )


def refused_answer() -> DemoAnswer:
    return DemoAnswer(
        answer="This request is not permitted in the controlled demo.",
        route=DemoRoute.REQUEST_REFUSED,
        principal=DEMO_PRINCIPAL,
        warnings=("No document retrieval or database query was executed.",),
        review_required=False,
        correlation_id=get_request_id(),
        duration_ms=0,
    )
