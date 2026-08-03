"""Authentication, authorization, document ACL, and audit contracts."""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import uuid4

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from sqlalchemy import Connection, text

from pms_common.logging import get_request_id
from pms_common.settings import Settings


class AuthenticationError(ValueError):
    """Raised when an access token cannot establish a trusted identity."""


class AuthorizationDenied(PermissionError):
    """Raised when a trusted identity lacks the required scope."""


class UserRole(StrEnum):
    """Keycloak realm roles approved by Phase 04."""

    TENANT = "Tenant"
    DATA_ENTRY_OPERATOR = "Data Entry Operator"
    NODAL_REGIONAL_OFFICER = "Nodal/Regional Officer"
    FINANCE_OFFICER = "Finance Officer"
    ESTATE_OFFICER = "Estate Officer"
    LEGAL_OFFICER = "Legal Officer"
    HOD = "HOD"
    AUDITOR = "Auditor"
    ADMINISTRATOR = "Administrator"


class Classification(StrEnum):
    """Ordered document classification clearance."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


CLASSIFICATION_RANK: Final = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


class Permission(StrEnum):
    """Application operations authorized before retrieval or database access."""

    TENANT_RECORD_READ = "tenant_record_read"
    PORT_WIDE_AGGREGATE = "port_wide_aggregate"
    DOCUMENT_SEARCH = "document_search"
    DATA_WRITE = "data_write"
    RULE_CALCULATION = "rule_calculation"
    AUDIT_READ = "audit_read"


PORT_WIDE_ROLES: Final = frozenset(
    {
        UserRole.DATA_ENTRY_OPERATOR,
        UserRole.NODAL_REGIONAL_OFFICER,
        UserRole.FINANCE_OFFICER,
        UserRole.ESTATE_OFFICER,
        UserRole.HOD,
        UserRole.AUDITOR,
        UserRole.ADMINISTRATOR,
    }
)

PERMISSION_ROLES: Final = {
    Permission.TENANT_RECORD_READ: frozenset(UserRole),
    Permission.PORT_WIDE_AGGREGATE: PORT_WIDE_ROLES,
    Permission.DOCUMENT_SEARCH: frozenset(UserRole),
    Permission.DATA_WRITE: frozenset(
        {
            UserRole.DATA_ENTRY_OPERATOR,
            UserRole.NODAL_REGIONAL_OFFICER,
            UserRole.ESTATE_OFFICER,
            UserRole.ADMINISTRATOR,
        }
    ),
    Permission.RULE_CALCULATION: frozenset(
        {
            UserRole.FINANCE_OFFICER,
            UserRole.ESTATE_OFFICER,
            UserRole.HOD,
            UserRole.AUDITOR,
            UserRole.ADMINISTRATOR,
        }
    ),
    Permission.AUDIT_READ: frozenset(
        {
            UserRole.AUDITOR,
            UserRole.ADMINISTRATOR,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Trusted identity and scope derived only from a verified token."""

    subject: str
    roles: frozenset[UserRole]
    tenant_id: str | None
    department_id: str | None
    classification: Classification
    unit_id: str | None = None

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise AuthenticationError("authenticated subject is required")
        if not self.roles:
            raise AuthenticationError("at least one approved role is required")
        if UserRole.TENANT in self.roles and not self.tenant_id:
            raise AuthenticationError("Tenant role requires a signed tenant claim")


def _claim_at_path(claims: Mapping[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


class JwtValidator:
    """Validate Keycloak RS256 tokens and build a trusted context."""

    def __init__(
        self,
        settings: Settings,
        *,
        signing_key_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._signing_key_resolver = signing_key_resolver or self._jwks_resolver(settings)

    @staticmethod
    def _jwks_resolver(settings: Settings) -> Callable[[str], Any]:
        ssl_context: ssl.SSLContext | None = None
        if not settings.keycloak_verify_ssl:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        client = PyJWKClient(
            settings.keycloak_jwks_url,
            timeout=settings.db_connect_timeout_seconds,
            ssl_context=ssl_context,
        )
        return lambda token: client.get_signing_key_from_jwt(token).key

    def validate(self, token: str) -> AuthorizationContext:
        """Return signed identity claims or fail with a generic error."""

        if not token.strip():
            raise AuthenticationError("bearer token is required")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != self._settings.jwt_algorithm:
                raise AuthenticationError("token algorithm is not allowed")
            claims = jwt.decode(
                token,
                key=self._signing_key_resolver(token),
                algorithms=[self._settings.jwt_algorithm],
                audience=self._settings.keycloak_audience,
                issuer=self._settings.keycloak_issuer,
                leeway=self._settings.jwt_clock_skew_seconds,
                options={
                    "require": ["sub", "exp", "iat", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except AuthenticationError:
            raise
        except (PyJWTError, OSError, KeyError, TypeError, ValueError) as error:
            raise AuthenticationError("token validation failed") from error

        subject = claims.get("sub")
        role_values = _claim_at_path(claims, self._settings.jwt_role_claim)
        if not isinstance(subject, str) or not isinstance(role_values, list):
            raise AuthenticationError("token is missing required identity claims")
        supported_roles = {role.value: role for role in UserRole}
        roles = frozenset(
            supported_roles[value]
            for value in role_values
            if isinstance(value, str) and value in supported_roles
        )
        tenant = _claim_at_path(claims, self._settings.jwt_tenant_claim)
        department = _claim_at_path(claims, self._settings.jwt_department_claim)
        unit = claims.get("unit_id")
        classification_value = _claim_at_path(
            claims,
            self._settings.jwt_classification_claim,
        )
        try:
            classification = Classification(
                classification_value
                if isinstance(classification_value, str)
                else Classification.PUBLIC
            )
        except ValueError as error:
            raise AuthenticationError("token classification is invalid") from error
        return AuthorizationContext(
            subject=subject,
            roles=roles,
            tenant_id=tenant if isinstance(tenant, str) and tenant else None,
            department_id=department if isinstance(department, str) and department else None,
            classification=classification,
            unit_id=unit if isinstance(unit, str) and unit else None,
        )


@dataclass(frozen=True, slots=True)
class CanonicalTenantMapping:
    """One active canonical mapping loaded from ``pms_app``."""

    subject: str
    canonical_tenant_id: str
    role: UserRole
    department_id: str | None = None


class AuthorizationService:
    """Default-deny authorization independent of frontend-supplied identity."""

    def require_permission(
        self,
        context: AuthorizationContext,
        permission: Permission,
    ) -> None:
        if not context.roles.intersection(PERMISSION_ROLES[permission]):
            raise AuthorizationDenied(f"permission denied: {permission}")

    def effective_tenant_id(
        self,
        context: AuthorizationContext,
        frontend_tenant_id: str | None = None,
    ) -> str | None:
        """Ignore untrusted frontend tenancy and use only the verified context."""

        del frontend_tenant_id
        return context.tenant_id

    def require_tenant_record(
        self,
        context: AuthorizationContext,
        record_tenant_id: str,
    ) -> None:
        self.require_permission(context, Permission.TENANT_RECORD_READ)
        if context.roles.intersection(PORT_WIDE_ROLES):
            return
        if context.tenant_id != record_tenant_id:
            raise AuthorizationDenied("tenant scope denied")

    def require_canonical_mapping(
        self,
        context: AuthorizationContext,
        mappings: Iterable[CanonicalTenantMapping],
    ) -> None:
        """Require signed tenant identity to match an active canonical mapping."""

        if UserRole.TENANT not in context.roles:
            return
        if not any(
            mapping.subject == context.subject
            and mapping.canonical_tenant_id == context.tenant_id
            and mapping.role == UserRole.TENANT
            for mapping in mappings
        ):
            raise AuthorizationDenied("canonical tenant mapping denied")


@dataclass(frozen=True, slots=True)
class DocumentChunkAccess:
    """ACL metadata evaluated before a chunk enters vector search."""

    chunk_id: str
    canonical_document_id: str
    tenant_id: str | None
    classification: Classification
    allowed_roles: frozenset[UserRole] = field(default_factory=frozenset)
    allowed_departments: frozenset[str] = field(default_factory=frozenset)


class DocumentAclService:
    """Filter chunk candidates before retrieval using trusted authorization."""

    _department_bypass_roles: Final = frozenset(
        {UserRole.AUDITOR, UserRole.ADMINISTRATOR}
    )

    def is_allowed(
        self,
        context: AuthorizationContext,
        chunk: DocumentChunkAccess,
    ) -> bool:
        if not context.roles.intersection(PERMISSION_ROLES[Permission.DOCUMENT_SEARCH]):
            return False
        if (
            chunk.tenant_id is not None
            and not context.roles.intersection(PORT_WIDE_ROLES)
            and context.tenant_id != chunk.tenant_id
        ):
            return False
        if CLASSIFICATION_RANK[context.classification] < CLASSIFICATION_RANK[
            chunk.classification
        ]:
            return False
        if chunk.allowed_roles and not context.roles.intersection(chunk.allowed_roles):
            return False
        return not (
            chunk.allowed_departments
            and not context.roles.intersection(self._department_bypass_roles)
            and context.department_id not in chunk.allowed_departments
        )

    def filter_authorized(
        self,
        context: AuthorizationContext,
        chunks: Iterable[DocumentChunkAccess],
    ) -> tuple[DocumentChunkAccess, ...]:
        """Return only candidates authorized before vector similarity runs."""

        return tuple(chunk for chunk in chunks if self.is_allowed(context, chunk))


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Security audit metadata; response and prompt bodies are intentionally absent."""

    event_id: str
    occurred_at: datetime
    subject: str
    roles: tuple[str, ...]
    tenant_id: str | None
    query_category: str
    entity_scope: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]
    prediction_version: str | None
    rule_version: str | None
    model_version: str | None
    result_status: str
    correlation_id: str


class AuditRecorder:
    """Minimal in-memory recorder used by callers and focused tests."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def create_audit_event(
    context: AuthorizationContext,
    *,
    query_category: str,
    entity_scope: Mapping[str, str],
    source_ids: Iterable[str] = (),
    prediction_version: str | None = None,
    rule_version: str | None = None,
    model_version: str | None = None,
    result_status: str,
) -> AuditEvent:
    """Create bounded audit metadata without prompt or response content."""

    return AuditEvent(
        event_id=str(uuid4()),
        occurred_at=datetime.now(UTC),
        subject=context.subject,
        roles=tuple(sorted(role.value for role in context.roles)),
        tenant_id=context.tenant_id,
        query_category=query_category,
        entity_scope=tuple(sorted(entity_scope.items())),
        source_ids=tuple(source_ids),
        prediction_version=prediction_version,
        rule_version=rule_version,
        model_version=model_version,
        result_status=result_status,
        correlation_id=get_request_id(),
    )


def apply_postgres_session_context(
    connection: Connection,
    context: AuthorizationContext,
) -> None:
    """Set transaction-local trusted PostgreSQL RLS variables."""

    values = {
        "subject": context.subject,
        "tenant_id": context.tenant_id or "",
        "roles": ",".join(sorted(role.value for role in context.roles)),
        "department_id": context.department_id or "",
        "unit_id": context.unit_id or "",
        "classification": context.classification.value,
    }
    for name, value in values.items():
        connection.execute(
            text("SELECT set_config(:setting_name, :setting_value, true)"),
            {
                "setting_name": f"pms.{name}",
                "setting_value": value,
            },
        )


def write_audit_event(connection: Connection, event: AuditEvent) -> None:
    """Insert one parameterized audit event without sensitive response content."""

    connection.execute(
        text(
            "INSERT INTO pms_audit.security_event "
            "(event_id, occurred_at, subject, roles, canonical_tenant_id, "
            "query_category, entity_scope, source_ids, prediction_version, "
            "rule_version, model_version, result_status, correlation_id) "
            "VALUES (:event_id, :occurred_at, :subject, :roles, "
            ":canonical_tenant_id, :query_category, CAST(:entity_scope AS jsonb), "
            "CAST(:source_ids AS jsonb), :prediction_version, :rule_version, "
            ":model_version, :result_status, :correlation_id)"
        ),
        {
            "event_id": event.event_id,
            "occurred_at": event.occurred_at,
            "subject": event.subject,
            "roles": list(event.roles),
            "canonical_tenant_id": event.tenant_id,
            "query_category": event.query_category,
            "entity_scope": json.dumps(dict(event.entity_scope), sort_keys=True),
            "source_ids": json.dumps(event.source_ids),
            "prediction_version": event.prediction_version,
            "rule_version": event.rule_version,
            "model_version": event.model_version,
            "result_status": event.result_status,
            "correlation_id": event.correlation_id,
        },
    )
