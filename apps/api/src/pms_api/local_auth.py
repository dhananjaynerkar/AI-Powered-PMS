"""Disabled-by-default localhost password authentication for the client demo."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Final, Literal

from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pydantic import BaseModel, ConfigDict, Field, SecretStr

LocalLoginRole = Literal[
    "Data Entry Operator",
    "Nodal/Regional Officer",
    "HOD",
    "Tenant",
]

_LOCAL_ROLES: Final = frozenset(
    {
        UserRole.DATA_ENTRY_OPERATOR,
        UserRole.NODAL_REGIONAL_OFFICER,
        UserRole.HOD,
        UserRole.TENANT,
    }
)


class LocalAuthenticationError(ValueError):
    """Raised for an invalid local login without revealing its cause."""


class LocalAuthLoginRequest(BaseModel):
    """A local demo credential request; the selected role is verified server-side."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._@-]+$")
    password: SecretStr = Field(min_length=1, max_length=512)
    role: LocalLoginRole


class LocalAuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    roles: tuple[LocalLoginRole, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalCredential:
    username: str
    password_hash: str
    role: UserRole
    tenant_id: str | None
    department_id: str | None
    unit_id: str | None


@dataclass(frozen=True, slots=True)
class _LocalSession:
    context: AuthorizationContext
    expires_at: float


def verify_scrypt_password(password: str, encoded_hash: str) -> bool:
    """Verify a password against the documented scrypt encoded-hash format."""

    try:
        algorithm, work_factor, block_size, parallelism, salt_text, digest_text = (
            encoded_hash.split("$")
        )
        if algorithm != "scrypt":
            return False
        n = int(work_factor)
        r = int(block_size)
        p = int(parallelism)
        if n < 2**14 or n > 2**18 or n & (n - 1) or not 1 <= r <= 32 or not 1 <= p <= 16:
            return False
        salt = base64.b64decode(salt_text.encode("ascii"), validate=True)
        expected = base64.b64decode(digest_text.encode("ascii"), validate=True)
        if len(salt) < 16 or len(expected) < 32:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, UnicodeEncodeError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def create_scrypt_password_hash(password: str) -> str:
    """Create an `.env`-ready scrypt hash without persisting the password."""

    n, r, p = 2**14, 8, 1
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n,
        r,
        p,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


class LocalAuthService:
    """Memory-only, short-lived opaque sessions for the local development server."""

    def __init__(self, credentials: tuple[LocalCredential, ...], *, ttl_minutes: int) -> None:
        self._credentials = credentials
        self._ttl_seconds = ttl_minutes * 60
        self._sessions: dict[bytes, _LocalSession] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalAuthService:
        credentials = (
            LocalCredential(
                settings.local_auth_data_entry_operator_username or "",
                _secret_value(settings.local_auth_data_entry_operator_password_hash),
                UserRole.DATA_ENTRY_OPERATOR,
                None,
                "estate",
                "land",
            ),
            LocalCredential(
                settings.local_auth_nodal_regional_officer_username or "",
                _secret_value(settings.local_auth_nodal_regional_officer_password_hash),
                UserRole.NODAL_REGIONAL_OFFICER,
                None,
                "estate",
                "regional",
            ),
            LocalCredential(
                settings.local_auth_hod_username or "",
                _secret_value(settings.local_auth_hod_password_hash),
                UserRole.HOD,
                None,
                "estate",
                "head-office",
            ),
            LocalCredential(
                settings.local_auth_tenant_username or "",
                _secret_value(settings.local_auth_tenant_password_hash),
                UserRole.TENANT,
                settings.local_auth_tenant_id,
                None,
                None,
            ),
        )
        return cls(credentials, ttl_minutes=settings.local_auth_token_ttl_minutes)

    def login(self, request: LocalAuthLoginRequest) -> tuple[str, AuthorizationContext]:
        selected_role = UserRole(request.role)
        if selected_role not in _LOCAL_ROLES:
            raise LocalAuthenticationError("invalid local credentials")
        credential = next(
            (
                item
                for item in self._credentials
                if item.role is selected_role
                and hmac.compare_digest(request.username, item.username)
            ),
            None,
        )
        if credential is None or not verify_scrypt_password(
            request.password.get_secret_value(), credential.password_hash
        ):
            raise LocalAuthenticationError("invalid local credentials")
        context = AuthorizationContext(
            subject=f"local.{credential.username}",
            roles=frozenset({credential.role}),
            tenant_id=credential.tenant_id,
            department_id=credential.department_id,
            classification=Classification.INTERNAL,
            unit_id=credential.unit_id,
        )
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_expired_locked()
            self._sessions[_token_digest(token)] = _LocalSession(
                context=context,
                expires_at=time.monotonic() + self._ttl_seconds,
            )
        return token, context

    def authenticate(self, token: str) -> AuthorizationContext | None:
        with self._lock:
            self._purge_expired_locked()
            session = self._sessions.get(_token_digest(token))
        return session.context if session is not None else None

    def revoke(self, token: str | None) -> None:
        if token is None:
            return
        with self._lock:
            self._sessions.pop(_token_digest(token), None)

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, item in self._sessions.items() if item.expires_at <= now]
        for key in expired:
            del self._sessions[key]


def _secret_value(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()
