"""Versioned APIs with injected persistence and trusted identity."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pms_case_workflow.models import CreateCase
from pms_case_workflow.repository import PostgresCaseStore
from pms_case_workflow.service import (
    CaseAccessDenied,
    CaseNotFound,
    CaseWorkflowError,
    CaseWorkflowService,
)
from pms_common.database import create_database_engine
from pms_common.logging import get_request_id
from pms_common.security import (
    AuthenticationError,
    AuthorizationContext,
    AuthorizationDenied,
    Classification,
    JwtValidator,
    UserRole,
)
from pms_common.settings import Settings
from pms_context import ArtifactReference, ContextEngine, EvidenceReference
from pms_ingestion.factory import create_document_service
from pms_ingestion.scanner import MalwareDetected, MalwareScannerError
from pms_ingestion.service import (
    DocumentIntegrityError,
    DocumentNotFound,
    DocumentService,
    DocumentServiceError,
)
from pms_ingestion.storage import MinioObjectStore, ObjectStorageError
from pms_ingestion.validation import UploadValidationError
from pms_retrieval.generation import GenerationError
from pms_retrieval.models import GroundedAnswer
from pms_retrieval.rag import HybridRagService, PostgresRagRepository
from pms_rule_engine.engine import RuleCalculationEngine
from pms_rule_engine.models import (
    CalculationResult,
    LeaseCalculationRequest,
    ReplayCalculationRequest,
)
from pms_rule_engine.repository import PostgresRuleRepository, RuleRepositoryError
from pms_rule_engine.service import RuleCalculationService
from pms_structured.models import StructuredAnswer, StructuredQuery
from pms_structured.repository import (
    PostgresStructuredRepository,
    StructuredQueryError,
)
from pms_structured.router import DeterministicRouter
from pms_structured.service import StructuredQueryService
from pms_structured.templates import ApprovedTemplateRegistry, SqlSafetyError
from sqlalchemy import Engine
from starlette.responses import RedirectResponse, Response

from pms_api.audit import AuditService, AuditServiceProvider, PostgresAuditServiceProvider
from pms_api.demo import (
    DEMO_CONTEXTS,
    GOLD_POLICY_CHILD_CHUNK_ID,
    GOLD_POLICY_DOCUMENT_ID,
    GOLD_POLICY_DOCUMENT_VERSION_ID,
    GOLD_POLICY_PARENT_CHUNK_ID,
    DemoAnswer,
    DemoConfigurationError,
    DemoPrincipal,
    DemoQueryError,
    DemoQueryRequest,
    DemoRoute,
    DemoSessionRequest,
    DemoStatus,
    DemoStructuredEvidence,
    DemoStructuredProvider,
    PostgresDemoStructuredProvider,
    demo_context_from_session,
    demo_is_enabled,
    demo_principal,
    demo_retrieval_query,
    issue_demo_session,
    refused_answer,
    review_required_answer,
    route_demo_question,
    uses_gold_policy_evidence,
)
from pms_api.local_auth import (
    LocalAuthenticationError,
    LocalAuthLoginRequest,
    LocalAuthService,
    LocalAuthStatus,
)
from pms_api.schemas import (
    AuditEventResponse,
    CaseResponse,
    CreateCaseRequest,
    DocumentResponse,
    DocumentUploadResponse,
    HandoffRequest,
    HealthResponse,
    MeResponse,
    MessageRequest,
    MessageResponse,
    PolicyQueryRequest,
    RemarksRequest,
    TimelineResponse,
)
from pms_api.semantic_demo import (
    PostgresSemanticDemoProvider,
    SemanticDemoError,
    SemanticDemoProvider,
)


class ServiceProvider(Protocol):
    """Create one transaction-scoped workflow service per request."""

    def __call__(
        self,
        context: AuthorizationContext,
    ) -> AbstractContextManager[CaseWorkflowService]: ...


class DocumentServiceProvider(Protocol):
    """Create one authorization-scoped document service per request."""

    def __call__(
        self,
        context: AuthorizationContext,
    ) -> AbstractContextManager[DocumentService]: ...


class StructuredServiceProvider(Protocol):
    """Create one authorization-scoped structured-query service per request."""

    def __call__(
        self,
        context: AuthorizationContext,
    ) -> AbstractContextManager[StructuredQueryService]: ...


class RuleServiceProvider(Protocol):
    """Create one authorization-scoped rule calculation service per request."""

    def __call__(
        self,
        context: AuthorizationContext,
    ) -> AbstractContextManager[RuleCalculationService]: ...


class RagServiceProvider(Protocol):
    """Create an authorization-scoped hybrid document retrieval service."""

    def __call__(
        self,
        context: AuthorizationContext,
    ) -> AbstractContextManager[HybridRagService]: ...


class PostgresServiceProvider:
    """Create a transaction-scoped service backed by PostgreSQL."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    @contextmanager
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> Iterator[CaseWorkflowService]:
        with self._engine.begin() as connection:
            yield CaseWorkflowService(
                PostgresCaseStore(connection, context),
                context,
                context_engine=ContextEngine(
                    recent_message_window=self._settings.case_recent_message_window,
                    retrieved_message_top_k=self._settings.case_retrieved_message_top_k,
                ),
            )


class PostgresDocumentServiceProvider:
    """Create transaction-scoped registry access with one private object client."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self._object_store = MinioObjectStore(settings)

    @contextmanager
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> Iterator[DocumentService]:
        with self._engine.begin() as connection:
            yield create_document_service(
                connection,
                context,
                self._settings,
                object_store=self._object_store,
            )


class PostgresStructuredServiceProvider:
    """Create a transaction-scoped governed-query service."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self._router = DeterministicRouter(
            Path(settings.query_router_config),
            Path(settings.domain_synonyms_config),
        )
        self._templates = ApprovedTemplateRegistry(
            Path(settings.sql_template_dir),
            max_joins=settings.text_to_sql_max_joins,
        )

    @contextmanager
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> Iterator[StructuredQueryService]:
        with self._engine.begin() as connection:
            repository = PostgresStructuredRepository(
                connection,
                context,
                self._settings,
                self._templates,
            )
            yield StructuredQueryService(self._router, repository)


class PostgresRuleServiceProvider:
    """Create a transaction-scoped deterministic rule service."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    @contextmanager
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> Iterator[RuleCalculationService]:
        with self._engine.begin() as connection:
            yield RuleCalculationService(
                PostgresRuleRepository(connection, context),
                context,
                RuleCalculationEngine(
                    calculation_version=self._settings.rule_calculation_version,
                    max_segments=self._settings.rule_max_segments,
                ),
            )


class PostgresRagServiceProvider:
    """Create a transaction-bounded hybrid retrieval service."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    @contextmanager
    def __call__(
        self,
        context: AuthorizationContext,
    ) -> Iterator[HybridRagService]:
        yield HybridRagService(
            PostgresRagRepository(self._engine, context),
            context,
            self._settings,
        )


bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
LoginPortal = Literal["staff", "tenant"]
STAFF_PORTAL_ROLES = frozenset(
    {
        UserRole.DATA_ENTRY_OPERATOR,
        UserRole.NODAL_REGIONAL_OFFICER,
        UserRole.HOD,
    }
)


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _validator() -> JwtValidator:
    return JwtValidator(_settings())


def get_authorization_context(
    request: Request,
    credentials: BearerCredentials,
) -> AuthorizationContext:
    """Validate a bearer header or the server-issued HttpOnly access cookie."""

    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
        local_auth = cast(
            LocalAuthService | None,
            getattr(request.app.state, "local_auth_service", None),
        )
        if local_auth is not None:
            local_context = local_auth.authenticate(token)
            if local_context is not None:
                return local_context
    if token is None:
        local_auth = cast(
            LocalAuthService | None,
            getattr(request.app.state, "local_auth_service", None),
        )
        local_token = request.cookies.get("pms_local_access_token")
        if local_auth is not None and local_token:
            local_context = local_auth.authenticate(local_token)
            if local_context is not None:
                return local_context
        token = request.cookies.get("pms_access_token")
    if not token:
        demo_context = demo_context_from_session(
            request.cookies.get("pms_demo_session"),
            _settings(),
            client_host=request.client.host if request.client is not None else None,
        )
        if demo_context is not None:
            return demo_context
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        return _validator().validate(token)
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error


TrustedContext = Annotated[AuthorizationContext, Depends(get_authorization_context)]


def get_demo_context(request: Request) -> AuthorizationContext:
    settings = _settings()
    if not demo_is_enabled(settings):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    context = demo_context_from_session(
        request.cookies.get("pms_demo_session"),
        settings,
        client_host=request.client.host if request.client is not None else None,
    )
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return context


DemoContext = Annotated[AuthorizationContext, Depends(get_demo_context)]


def get_case_service(
    request: Request,
    context: TrustedContext,
) -> Generator[CaseWorkflowService, None, None]:
    provider: ServiceProvider | None = getattr(
        request.app.state,
        "case_service_provider",
        None,
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="case service is not configured",
        )
    with provider(context) as service:
        yield service


CaseService = Annotated[CaseWorkflowService, Depends(get_case_service)]


def get_document_service(
    request: Request,
    context: TrustedContext,
) -> Generator[DocumentService, None, None]:
    provider: DocumentServiceProvider | None = getattr(
        request.app.state,
        "document_service_provider",
        None,
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="document service is not configured",
        )
    with provider(context) as service:
        yield service


DocumentServiceDependency = Annotated[DocumentService, Depends(get_document_service)]


def get_structured_service(
    request: Request,
    context: TrustedContext,
) -> Generator[StructuredQueryService, None, None]:
    provider: StructuredServiceProvider | None = getattr(
        request.app.state,
        "structured_service_provider",
        None,
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="structured query service is not configured",
        )
    with provider(context) as service:
        yield service


StructuredServiceDependency = Annotated[
    StructuredQueryService,
    Depends(get_structured_service),
]


def get_rule_service(
    request: Request,
    context: TrustedContext,
) -> Generator[RuleCalculationService, None, None]:
    provider: RuleServiceProvider | None = getattr(
        request.app.state,
        "rule_service_provider",
        None,
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rule calculation service is not configured",
        )
    with provider(context) as service:
        yield service


RuleServiceDependency = Annotated[
    RuleCalculationService,
    Depends(get_rule_service),
]


def get_rag_service(
    request: Request,
    context: TrustedContext,
) -> Generator[HybridRagService, None, None]:
    provider: RagServiceProvider | None = getattr(
        request.app.state,
        "rag_service_provider",
        None,
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="document retrieval service is not configured",
        )
    with provider(context) as service:
        yield service


RagServiceDependency = Annotated[HybridRagService, Depends(get_rag_service)]


def get_audit_service(
    request: Request,
    context: TrustedContext,
) -> Generator[AuditService | None, None, None]:
    provider: AuditServiceProvider | None = getattr(
        request.app.state,
        "audit_service_provider",
        None,
    )
    if provider is None:
        yield None
        return
    with provider(context) as service:
        yield service


AuditServiceDependency = Annotated[
    AuditService | None,
    Depends(get_audit_service),
]


def _case_error(error: Exception) -> HTTPException:
    if isinstance(error, CaseNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
    if isinstance(error, CaseAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="access denied")
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _document_error(error: Exception) -> HTTPException:
    if isinstance(error, DocumentNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    if isinstance(error, AuthorizationDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="access denied")
    if isinstance(error, UploadValidationError | MalwareDetected | ValueError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, MalwareScannerError | ObjectStorageError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="document safety service is unavailable",
        )
    if isinstance(error, DocumentIntegrityError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="document integrity validation failed",
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="document operation failed")


async def _read_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    content = bytearray()
    while chunk := await upload.read(min(1024 * 1024, max_bytes + 1)):
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="upload exceeds the configured size limit",
            )
    return bytes(content)


def _oauth_callback_url(settings: Settings) -> str:
    return (
        f"{settings.app_host}:{settings.app_port}/auth/callback"
        if settings.app_host.startswith("http")
        else f"http://{settings.app_host}:{settings.app_port}/auth/callback"
    )


def _oauth_state_value(state: str, settings: Settings) -> str:
    secret = settings.app_secret_key
    if secret is None or not secret.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APP_SECRET_KEY is required for browser login",
        )
    signature = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        state.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{state}.{signature}"


def _valid_oauth_state(cookie_value: str | None, state: str, settings: Settings) -> bool:
    if not cookie_value or not state:
        return False
    signed_state = _oauth_state_value(state, settings)
    return hmac.compare_digest(cookie_value, signed_state)


def _exchange_oauth_code(settings: Settings, code: str) -> str:
    client_secret = settings.keycloak_client_secret
    if client_secret is None or not client_secret.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak client secret is not configured",
        )
    endpoint = (
        f"{settings.keycloak_base_url.rstrip('/')}/realms/{settings.keycloak_realm}"
        "/protocol/openid-connect/token"
    )
    form = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": settings.keycloak_client_id,
            "client_secret": client_secret.get_secret_value(),
            "code": code,
            "redirect_uri": _oauth_callback_url(settings),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Keycloak token exchange failed",
        ) from error
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Keycloak did not return an access token",
        )
    return access_token


def _is_loopback_request(request: Request) -> bool:
    """Keep local password login unreachable from a non-loopback client."""

    return request.client is not None and request.client.host in {"127.0.0.1", "::1", "localhost"}


def create_app(
    service_provider: ServiceProvider | None = None,
    document_service_provider: DocumentServiceProvider | None = None,
    structured_service_provider: StructuredServiceProvider | None = None,
    rule_service_provider: RuleServiceProvider | None = None,
    rag_service_provider: RagServiceProvider | None = None,
    audit_service_provider: AuditServiceProvider | None = None,
    demo_structured_provider: DemoStructuredProvider | None = None,
    demo_semantic_provider: SemanticDemoProvider | None = None,
    local_auth_service: LocalAuthService | None = None,
    *,
    upload_max_bytes: int | None = None,
) -> FastAPI:
    """Create the API without a hidden global database connection."""

    app = FastAPI(title="AI Powered PMS", version="0.1.0")
    app.state.case_service_provider = service_provider
    app.state.document_service_provider = document_service_provider
    app.state.structured_service_provider = structured_service_provider
    app.state.rule_service_provider = rule_service_provider
    app.state.rag_service_provider = rag_service_provider
    app.state.audit_service_provider = audit_service_provider
    app.state.demo_structured_provider = demo_structured_provider
    app.state.demo_semantic_provider = demo_semantic_provider
    app.state.local_auth_service = local_auth_service
    app.state.upload_max_bytes = upload_max_bytes or Settings().upload_max_mb * 1024 * 1024

    @app.get("/auth/login", include_in_schema=False)
    def auth_login(portal: LoginPortal = "staff") -> RedirectResponse:
        settings = _settings()
        if not settings.keycloak_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Keycloak login is disabled",
            )
        if (
            settings.keycloak_client_secret is None
            or not settings.keycloak_client_secret.get_secret_value().strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Keycloak client secret is not configured",
            )
        state = f"{portal}.{secrets.token_urlsafe(32)}"
        callback = _oauth_callback_url(settings)
        query = urllib.parse.urlencode(
            {
                "client_id": settings.keycloak_client_id,
                "response_type": "code",
                "scope": "openid",
                "redirect_uri": callback,
                "state": state,
            }
        )
        endpoint = (
            f"{settings.keycloak_base_url.rstrip('/')}/realms/{settings.keycloak_realm}"
            "/protocol/openid-connect/auth"
        )
        response = RedirectResponse(
            f"{endpoint}?{query}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.set_cookie(
            "pms_oauth_state",
            _oauth_state_value(state, settings),
            max_age=600,
            httponly=True,
            secure=settings.app_env == "production",
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/auth/local/status", response_model=LocalAuthStatus, include_in_schema=False)
    def local_auth_status() -> LocalAuthStatus:
        return LocalAuthStatus(
            enabled=getattr(app.state, "local_auth_service", None) is not None,
            roles=(
                "Data Entry Operator",
                "Nodal/Regional Officer",
                "HOD",
                "Tenant",
            )
            if getattr(app.state, "local_auth_service", None) is not None
            else (),
        )

    @app.post("/auth/local/login", response_model=MeResponse, include_in_schema=False)
    def local_auth_login(
        payload: LocalAuthLoginRequest,
        response: Response,
        request: Request,
    ) -> MeResponse:
        settings = _settings()
        service: LocalAuthService | None = getattr(app.state, "local_auth_service", None)
        if service is None or not _is_loopback_request(request):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            token, context = service.login(payload)
        except LocalAuthenticationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid username, password, or role",
            ) from error
        response.set_cookie(
            "pms_local_access_token",
            token,
            max_age=settings.local_auth_token_ttl_minutes * 60,
            httponly=True,
            secure=False,
            samesite="lax",
            path="/",
        )
        return MeResponse(
            subject=context.subject,
            roles=tuple(sorted(context.roles, key=lambda role: role.value)),
            tenant_id=context.tenant_id,
            department_id=context.department_id,
            unit_id=context.unit_id,
            classification=context.classification,
        )

    @app.post("/auth/local/logout", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
    def local_auth_logout(request: Request) -> Response:
        service: LocalAuthService | None = getattr(app.state, "local_auth_service", None)
        if service is not None:
            service.revoke(request.cookies.get("pms_local_access_token"))
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie("pms_local_access_token", path="/")
        return response

    @app.get("/auth/callback", include_in_schema=False)
    def auth_callback(
        request: Request,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> RedirectResponse:
        settings = _settings()
        if error or not code or not state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Keycloak login was not completed",
            )
        if not _valid_oauth_state(request.cookies.get("pms_oauth_state"), state, settings):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid login state",
            )
        access_token = _exchange_oauth_code(settings, code)
        try:
            context = _validator().validate(access_token)
        except AuthenticationError as auth_error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Keycloak returned an invalid access token",
            ) from auth_error
        portal, separator, _ = state.partition(".")
        if not separator or portal not in {"staff", "tenant"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid login portal",
            )
        if portal == "tenant" and UserRole.TENANT not in context.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Selected portal does not match the signed Keycloak role",
            )
        if portal == "staff" and not context.roles.intersection(STAFF_PORTAL_ROLES):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Selected portal does not match the signed Keycloak role",
            )
        response = RedirectResponse(
            "http://127.0.0.1:5173/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.set_cookie(
            "pms_access_token",
            access_token,
            max_age=3600,
            httponly=True,
            secure=settings.app_env == "production",
            samesite="lax",
            path="/",
        )
        response.delete_cookie("pms_oauth_state", path="/")
        return response

    @app.get("/auth/logout", include_in_schema=False)
    def auth_logout(request: Request) -> RedirectResponse:
        service: LocalAuthService | None = getattr(app.state, "local_auth_service", None)
        if service is not None:
            service.revoke(request.cookies.get("pms_local_access_token"))
        response = RedirectResponse(
            "http://127.0.0.1:5173/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.delete_cookie("pms_access_token", path="/")
        response.delete_cookie("pms_local_access_token", path="/")
        response.delete_cookie("pms_oauth_state", path="/")
        return response

    @app.get("/health/live", response_model=HealthResponse)
    def health_live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    def health_ready() -> HealthResponse:
        configured = all(
            getattr(app.state, name, None) is not None
            for name in (
                "case_service_provider",
                "structured_service_provider",
                "rag_service_provider",
            )
        )
        return HealthResponse(status="ready" if configured else "not_ready")

    @app.get("/api/v1/demo/status", response_model=DemoStatus)
    def demo_status() -> DemoStatus:
        return DemoStatus(enabled=demo_is_enabled(_settings()))

    @app.post("/api/v1/demo/session", response_model=DemoPrincipal)
    def demo_session(request: DemoSessionRequest, response: Response) -> DemoPrincipal:
        settings = _settings()
        if not demo_is_enabled(settings):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        response.set_cookie(
            "pms_demo_session",
            issue_demo_session(request.identity, settings),
            max_age=3600,
            httponly=True,
            secure=False,
            samesite="lax",
            path="/",
        )
        return demo_principal(DEMO_CONTEXTS[request.identity])

    @app.delete("/api/v1/demo/session", status_code=status.HTTP_204_NO_CONTENT)
    def demo_logout() -> Response:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie("pms_demo_session", path="/")
        return response

    @app.get("/api/v1/demo/me", response_model=MeResponse)
    def demo_me(context: DemoContext) -> MeResponse:
        return MeResponse(
            subject=context.subject,
            roles=tuple(context.roles),
            tenant_id=None,
            department_id=context.department_id,
            unit_id=context.unit_id,
            classification=context.classification,
        )

    @app.post("/api/v1/demo/query", response_model=DemoAnswer)
    def demo_query(request: DemoQueryRequest, context: DemoContext) -> DemoAnswer:
        started = time.perf_counter()
        structured_provider: DemoStructuredProvider | None = getattr(
            app.state, "demo_structured_provider", None
        )
        semantic_provider: SemanticDemoProvider | None = getattr(
            app.state, "demo_semantic_provider", None
        )
        rag_provider: RagServiceProvider | None = getattr(app.state, "rag_service_provider", None)
        audit_provider: AuditServiceProvider | None = getattr(
            app.state, "audit_service_provider", None
        )
        if audit_provider is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="controlled demo services are not configured",
            )
        route, query_id = route_demo_question(request.question)
        if (
            route in {DemoRoute.STRUCTURED_SQL, DemoRoute.COMBINED}
            and structured_provider is None
        ) or (
            route in {DemoRoute.DOCUMENT_RAG, DemoRoute.COMBINED} and rag_provider is None
        ) or (route is DemoRoute.SEMANTIC_QUERY and semantic_provider is None):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="controlled demo services are not configured",
            )
        if route in {DemoRoute.REVIEW_REQUIRED, DemoRoute.REQUEST_REFUSED}:
            result = (
                refused_answer()
                if route is DemoRoute.REQUEST_REFUSED
                else review_required_answer(request.question)
            ).model_copy(
                update={
                    "principal": demo_principal(context),
                    "duration_ms": (time.perf_counter() - started) * 1000,
                }
            )
            with audit_provider(context) as audit:
                audit.record_demo_query(
                    question=request.question,
                    route=result.route.value,
                    query_id=None,
                    database_objects=(),
                    row_count=0,
                    citation_ids=(),
                    rejection_reason=(
                        "prohibited_request"
                        if route is DemoRoute.REQUEST_REFUSED
                        else "unsupported_request"
                    ),
                    response_status=(
                        "DENIED" if route is DemoRoute.REQUEST_REFUSED else "REVIEW_REQUIRED"
                    ),
                    duration_ms=result.duration_ms,
                )
            return result
        structured = None
        document = None
        semantic_result = None
        try:
            if route is DemoRoute.SEMANTIC_QUERY:
                if semantic_provider is None:
                    raise DemoConfigurationError("semantic provider is not configured")
                with semantic_provider() as service:
                    semantic_result = service.ask(
                        request.question,
                        limit=request.limit,
                        context=context,
                    )
                structured = DemoStructuredEvidence(
                    query_id="semantic_plan",
                    database_objects=(f"pms_app.{semantic_result.view}",),
                    rows=semantic_result.rows,
                    row_count=semantic_result.row_count,
                    freshness_at=semantic_result.freshness_at,
                    filters=(
                        "A local model produced a validated typed plan; SQL was compiled "
                        "server-side from the approved view catalog.",
                    ),
                )
            if route in {DemoRoute.STRUCTURED_SQL, DemoRoute.COMBINED}:
                if query_id is None:
                    raise DemoQueryError("approved query identifier is missing")
                if structured_provider is None:
                    raise DemoConfigurationError("structured provider is not configured")
                with structured_provider() as service:
                    structured = service.execute(query_id, request.limit)
            if route in {DemoRoute.DOCUMENT_RAG, DemoRoute.COMBINED}:
                if rag_provider is None:
                    raise DemoConfigurationError("RAG provider is not configured")
                with rag_provider(context) as service:
                    if uses_gold_policy_evidence(request.question):
                        document = service.answer_verified_extractive_evidence(
                            demo_retrieval_query(request.question),
                            document_id=GOLD_POLICY_DOCUMENT_ID,
                            document_version_id=GOLD_POLICY_DOCUMENT_VERSION_ID,
                            parent_chunk_id=GOLD_POLICY_PARENT_CHUNK_ID,
                            child_chunk_id=GOLD_POLICY_CHILD_CHUNK_ID,
                        )
                    else:
                        document = service.ask(demo_retrieval_query(request.question))
        except AuthorizationDenied as error:
            with audit_provider(context) as audit:
                audit.record_demo_query(
                    question=request.question,
                    route=route.value,
                    query_id=query_id,
                    database_objects=(),
                    row_count=0,
                    citation_ids=(),
                    rejection_reason="authorization_denied",
                    response_status="DENIED",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="access denied"
            ) from error
        except (
            DemoConfigurationError,
            DemoQueryError,
            GenerationError,
            SemanticDemoError,
        ) as error:
            with audit_provider(context) as audit:
                audit.record_demo_query(
                    question=request.question,
                    route=route.value,
                    query_id=query_id,
                    database_objects=(),
                    row_count=0,
                    citation_ids=(),
                    rejection_reason=type(error).__name__,
                    response_status="ERROR",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="controlled demo query could not be completed",
            ) from error
        citation_ids = tuple(source.source_id for source in document.sources) if document else ()
        database_objects = structured.database_objects if structured else ()
        row_count = structured.row_count if structured else 0
        review_required = bool(document and document.review_required)
        if route is DemoRoute.COMBINED:
            answer = (
                "Structured facts and policy evidence are shown separately. "
                "They do not establish a causal link without a reviewed lease-to-clause mapping."
            )
        elif document is not None:
            answer = document.answer
        elif semantic_result is not None:
            answer = semantic_result.answer
        elif query_id == "approved_leases" and "active" in request.question.casefold():
            answer = (
                f"Retrieved {row_count} approved lease "
                f"row{'s' if row_count != 1 else ''}. The source does not expose a "
                "verified active status, so current activity is not asserted."
            )
        else:
            answer = (
                f"Retrieved {row_count} row{'s' if row_count != 1 else ''} "
                "from an approved read-only query."
            )
        warnings: list[str] = []
        if route is DemoRoute.COMBINED:
            warnings.append("Combined evidence is not a verified causal determination.")
        if query_id == "approved_leases" and "active" in request.question.casefold():
            warnings.append(
                "APPROVED is shown as the source status; text-form lease dates were not "
                "used to infer current activity."
            )
        result = DemoAnswer(
            answer=answer,
            route=route,
            principal=demo_principal(context),
            structured=structured,
            document=document,
            warnings=tuple(warnings),
            review_required=review_required,
            correlation_id=get_request_id(),
            duration_ms=(time.perf_counter() - started) * 1000,
            evidence_extracted=bool(
                document
                and {
                    "EXTRACTIVE_EVIDENCE_FALLBACK",
                    "VERIFIED_EXTRACTIVE_DEMO_EVIDENCE",
                }.intersection(document.warnings)
            ),
        )
        with audit_provider(context) as audit:
            audit.record_demo_query(
                question=request.question,
                route=route.value,
                query_id=query_id,
                database_objects=database_objects,
                row_count=row_count,
                citation_ids=citation_ids,
                rejection_reason=None,
                response_status="REVIEW_REQUIRED" if review_required else "ALLOWED",
                duration_ms=result.duration_ms,
            )
        return result

    @app.get("/api/v1/me", response_model=MeResponse)
    def me(context: TrustedContext) -> MeResponse:
        return MeResponse(
            subject=context.subject,
            roles=tuple(sorted(context.roles, key=lambda role: role.value)),
            tenant_id=context.tenant_id,
            department_id=context.department_id,
            unit_id=context.unit_id,
            classification=context.classification,
        )

    @app.post("/api/v1/policy/query", response_model=GroundedAnswer)
    def policy_query(
        request: PolicyQueryRequest,
        service: RagServiceDependency,
    ) -> GroundedAnswer:
        try:
            return service.ask(
                request.question,
                response_language=request.response_language,
                include_trace=request.include_trace,
                today=request.as_of_date,
            )
        except AuthorizationDenied as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="access denied",
            ) from error
        except GenerationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="document generation service is unavailable",
            ) from error

    @app.post("/api/v1/query", response_model=StructuredAnswer)
    def query(
        request: StructuredQuery,
        service: StructuredServiceDependency,
        audit: AuditServiceDependency,
    ) -> object:
        try:
            return service.ask(request)
        except AuthorizationDenied as error:
            if audit is not None:
                audit.record_denied("STRUCTURED_QUERY", "AUTHORIZATION_DENIED")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="access denied",
            ) from error
        except (StructuredQueryError, SqlSafetyError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(error),
            ) from error

    @app.get(
        "/api/v1/audit/my-queries",
        response_model=tuple[AuditEventResponse, ...],
    )
    def audit_my_queries(
        limit: int = Query(default=50, ge=1, le=100),
        service: AuditServiceDependency = None,
    ) -> tuple[AuditEventResponse, ...]:
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="audit service is not configured",
            )
        try:
            return service.list_my_queries(limit)
        except AuthorizationDenied as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="access denied",
            ) from error

    @app.post("/api/v1/calculations/lease", response_model=CalculationResult)
    def calculate_lease(
        request: LeaseCalculationRequest,
        service: RuleServiceDependency,
    ) -> object:
        try:
            return service.calculate(request)
        except AuthorizationDenied as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="access denied",
            ) from error
        except RuleRepositoryError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @app.post(
        "/api/v1/calculations/lease/{calculation_id}/replay",
        response_model=CalculationResult,
    )
    def replay_lease(
        calculation_id: str,
        replay: ReplayCalculationRequest,
        service: RuleServiceDependency,
    ) -> object:
        try:
            return service.replay(calculation_id, replay=replay)
        except AuthorizationDenied as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="access denied",
            ) from error
        except RuleRepositoryError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error

    @app.post(
        "/api/v1/documents",
        response_model=DocumentUploadResponse,
        status_code=201,
    )
    async def upload_document(
        service: DocumentServiceDependency,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form(min_length=1, max_length=300)],
        document_id: Annotated[str | None, Form(max_length=200)] = None,
        classification: Annotated[Classification, Form()] = Classification.INTERNAL,
    ) -> object:
        try:
            content = await _read_bounded(file, app.state.upload_max_bytes)
            result = service.upload(
                title=title,
                filename=file.filename or "",
                mime_type=file.content_type or "application/octet-stream",
                content=content,
                classification=classification,
                document_id=document_id,
            )
            return DocumentUploadResponse(
                document=DocumentResponse.from_metadata(result.document),
                duplicate=result.duplicate,
            )
        except (
            AuthorizationDenied,
            DocumentServiceError,
            MalwareDetected,
            MalwareScannerError,
            ObjectStorageError,
            UploadValidationError,
            ValueError,
        ) as error:
            raise _document_error(error) from error

    @app.get("/api/v1/documents/{document_id}", response_model=DocumentResponse)
    def document_metadata(
        document_id: str,
        service: DocumentServiceDependency,
    ) -> object:
        try:
            return service.metadata(document_id)
        except (AuthorizationDenied, DocumentServiceError) as error:
            raise _document_error(error) from error

    @app.get("/api/v1/documents/{document_id}/content")
    def document_content(
        document_id: str,
        service: DocumentServiceDependency,
    ) -> Response:
        try:
            retrieved = service.retrieve(document_id)
        except (AuthorizationDenied, DocumentServiceError, ObjectStorageError) as error:
            raise _document_error(error) from error
        return Response(
            content=retrieved.content,
            media_type=retrieved.document.mime_type,
            headers={
                "X-Content-SHA256": retrieved.document.checksum_sha256,
                "X-Document-Version": str(retrieved.document.version_number),
            },
        )

    @app.post("/api/v1/cases", response_model=CaseResponse, status_code=201)
    def create_case(
        request: CreateCaseRequest,
        service: CaseService,
    ) -> object:
        try:
            return service.create_case(
                CreateCase(
                    title=request.title,
                    objective=request.objective,
                    initial_message=request.initial_message,
                    unit_id=request.unit_id,
                    classification=request.classification,
                )
            )
        except (CaseWorkflowError, CaseAccessDenied) as error:
            raise _case_error(error) from error

    @app.get("/api/v1/cases", response_model=tuple[CaseResponse, ...])
    def list_cases(
        service: CaseService,
    ) -> object:
        return service.list_cases()

    @app.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
    def get_case(
        case_id: str,
        service: CaseService,
    ) -> object:
        try:
            return service.get_case(case_id)
        except (CaseNotFound, CaseAccessDenied) as error:
            raise _case_error(error) from error

    @app.get("/api/v1/cases/{case_id}/timeline", response_model=TimelineResponse)
    def timeline(
        case_id: str,
        service: CaseService,
    ) -> object:
        try:
            return service.timeline(case_id)
        except (CaseNotFound, CaseAccessDenied) as error:
            raise _case_error(error) from error

    @app.post(
        "/api/v1/cases/{case_id}/messages",
        response_model=MessageResponse,
        status_code=201,
    )
    def add_message(
        case_id: str,
        request: MessageRequest,
        service: CaseService,
    ) -> object:
        try:
            return service.add_message(
                case_id,
                body=request.body,
                supersedes_message_id=request.supersedes_message_id,
                evidence=tuple(
                    EvidenceReference(item.reference_type, item.reference_id, item.version)
                    for item in request.evidence
                ),
                artifacts=tuple(
                    ArtifactReference(
                        item.artifact_id,
                        item.version,
                        item.review_status,
                    )
                    for item in request.artifacts
                ),
            )
        except (CaseWorkflowError, CaseAccessDenied) as error:
            raise _case_error(error) from error

    def handoff(
        method_name: str,
        case_id: str,
        request: HandoffRequest,
        service: CaseWorkflowService,
    ) -> object:
        try:
            method = getattr(service, method_name)
            return method(
                case_id,
                assigned_subject=request.assigned_subject,
                remarks=request.remarks,
            )
        except (CaseWorkflowError, CaseAccessDenied) as error:
            raise _case_error(error) from error

    @app.post("/api/v1/cases/{case_id}/submit-to-no", response_model=CaseResponse)
    def submit_to_no(
        case_id: str,
        request: HandoffRequest,
        service: CaseService,
    ) -> object:
        return handoff("submit_to_no", case_id, request, service)

    @app.post("/api/v1/cases/{case_id}/submit-to-hod", response_model=CaseResponse)
    def submit_to_hod(
        case_id: str,
        request: HandoffRequest,
        service: CaseService,
    ) -> object:
        return handoff("submit_to_hod", case_id, request, service)

    @app.post("/api/v1/cases/{case_id}/escalate", response_model=CaseResponse)
    def escalate(
        case_id: str,
        request: HandoffRequest,
        service: CaseService,
    ) -> object:
        return handoff("escalate", case_id, request, service)

    def remarks_action(
        method_name: str,
        case_id: str,
        request: RemarksRequest,
        service: CaseWorkflowService,
    ) -> object:
        try:
            method = getattr(service, method_name)
            return method(case_id, remarks=request.remarks)
        except (CaseWorkflowError, CaseAccessDenied) as error:
            raise _case_error(error) from error

    @app.post("/api/v1/cases/{case_id}/return-to-do", response_model=CaseResponse)
    def return_to_do(
        case_id: str,
        request: RemarksRequest,
        service: CaseService,
    ) -> object:
        return remarks_action("return_to_do", case_id, request, service)

    @app.post("/api/v1/cases/{case_id}/verify", response_model=CaseResponse)
    def verify(
        case_id: str,
        request: RemarksRequest,
        service: CaseService,
    ) -> object:
        return remarks_action("verify", case_id, request, service)

    @app.post("/api/v1/cases/{case_id}/return-to-no", response_model=CaseResponse)
    def return_to_no(
        case_id: str,
        request: RemarksRequest,
        service: CaseService,
    ) -> object:
        return remarks_action("return_to_no", case_id, request, service)

    @app.post("/api/v1/cases/{case_id}/approve", response_model=CaseResponse)
    def approve(
        case_id: str,
        request: RemarksRequest,
        service: CaseService,
    ) -> object:
        return remarks_action("approve", case_id, request, service)

    @app.post("/api/v1/cases/{case_id}/reject", response_model=CaseResponse)
    def reject(
        case_id: str,
        request: RemarksRequest,
        service: CaseService,
    ) -> object:
        return remarks_action("reject", case_id, request, service)

    return app


def create_runtime_app() -> FastAPI:
    """Wire the local PostgreSQL runtime without opening a connection at import."""

    settings = Settings()
    engine = create_database_engine(settings, read_only=False)
    document_provider: DocumentServiceProvider | None = None
    if (
        settings.minio_enabled
        and settings.minio_access_key is not None
        and settings.minio_secret_key is not None
        and settings.minio_access_key.get_secret_value().strip()
        and settings.minio_secret_key.get_secret_value().strip()
    ):
        document_provider = PostgresDocumentServiceProvider(engine, settings)
    rag_provider = PostgresRagServiceProvider(engine, settings)
    audit_provider = PostgresAuditServiceProvider(engine)
    demo_provider: DemoStructuredProvider | None = None
    semantic_provider: SemanticDemoProvider | None = None
    local_auth_service: LocalAuthService | None = None
    if demo_is_enabled(settings):
        demo_provider = PostgresDemoStructuredProvider(settings)
        semantic_provider = PostgresSemanticDemoProvider(engine, settings)
    if settings.local_password_auth_enabled:
        local_auth_service = LocalAuthService.from_settings(settings)
    return create_app(
        PostgresServiceProvider(engine, settings),
        document_provider,
        PostgresStructuredServiceProvider(engine, settings),
        PostgresRuleServiceProvider(engine, settings),
        rag_provider,
        audit_provider,
        demo_structured_provider=demo_provider,
        demo_semantic_provider=semantic_provider,
        local_auth_service=local_auth_service,
        upload_max_bytes=settings.upload_max_mb * 1024 * 1024,
    )
