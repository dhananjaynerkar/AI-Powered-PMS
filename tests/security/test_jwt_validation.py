"""JWT signature and trusted-claim validation tests."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pms_common.security import AuthenticationError, Classification, JwtValidator, UserRole
from pms_common.settings import Settings

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
PUBLIC_PEM = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        keycloak_issuer="http://keycloak.test/realms/pms",
        keycloak_audience="pms-api",
        jwt_clock_skew_seconds=0,
    )


def _token(**claim_overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "subject-a",
        "iss": "http://keycloak.test/realms/pms",
        "aud": "pms-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "tenant_id": "tenant-a",
        "department": "estate",
        "unit_id": "unit-a",
        "classification": "confidential",
        "realm_access": {"roles": ["Tenant"]},
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def test_valid_rs256_token_builds_trusted_context() -> None:
    context = JwtValidator(
        _settings(),
        signing_key_resolver=lambda token: PUBLIC_PEM,
    ).validate(_token())

    assert context.subject == "subject-a"
    assert context.tenant_id == "tenant-a"
    assert context.roles == frozenset({UserRole.TENANT})
    assert context.classification is Classification.CONFIDENTIAL
    assert context.unit_id == "unit-a"


def test_wrong_signature_is_rejected() -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_public = other_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    with pytest.raises(AuthenticationError, match="token validation failed"):
        JwtValidator(
            _settings(),
            signing_key_resolver=lambda token: other_public,
        ).validate(_token())


def test_wrong_audience_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="token validation failed"):
        JwtValidator(
            _settings(),
            signing_key_resolver=lambda token: PUBLIC_PEM,
        ).validate(_token(aud="other-api"))


def test_expired_token_is_rejected() -> None:
    now = datetime.now(UTC)

    with pytest.raises(AuthenticationError, match="token validation failed"):
        JwtValidator(
            _settings(),
            signing_key_resolver=lambda token: PUBLIC_PEM,
        ).validate(
            _token(
                iat=now - timedelta(minutes=10),
                exp=now - timedelta(minutes=5),
            )
        )


def test_non_rs256_algorithm_is_rejected() -> None:
    claims = jwt.decode(_token(), options={"verify_signature": False})
    token = jwt.encode(
        claims,
        "not-a-production-key-used-for-negative-testing",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError, match="algorithm is not allowed"):
        JwtValidator(
            _settings(),
            signing_key_resolver=lambda encoded: PUBLIC_PEM,
        ).validate(token)


def test_token_without_an_approved_role_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="at least one approved role"):
        JwtValidator(
            _settings(),
            signing_key_resolver=lambda token: PUBLIC_PEM,
        ).validate(_token(realm_access={"roles": ["unapproved-role"]}))


def test_tenant_role_without_signed_tenant_claim_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="Tenant role requires"):
        JwtValidator(
            _settings(),
            signing_key_resolver=lambda token: PUBLIC_PEM,
        ).validate(_token(tenant_id=None))
