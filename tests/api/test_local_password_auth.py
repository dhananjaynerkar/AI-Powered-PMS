from __future__ import annotations

import asyncio
from typing import Any

import pms_api.app as api_module
import pytest
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app
from pms_api.local_auth import (
    LocalAuthService,
    create_scrypt_password_hash,
    verify_scrypt_password,
)
from pms_common.settings import Settings
from pydantic import SecretStr, ValidationError


def _settings() -> Settings:
    return Settings(
        app_env="development",
        app_host="127.0.0.1",
        local_password_auth_enabled=True,
        local_auth_data_entry_operator_username="demo.do",
        local_auth_data_entry_operator_password_hash=SecretStr(
            create_scrypt_password_hash("do-password")
        ),
        local_auth_nodal_regional_officer_username="demo.no",
        local_auth_nodal_regional_officer_password_hash=SecretStr(
            create_scrypt_password_hash("no-password")
        ),
        local_auth_hod_username="demo.hod",
        local_auth_hod_password_hash=SecretStr(create_scrypt_password_hash("hod-password")),
        local_auth_tenant_username="demo.tenant",
        local_auth_tenant_password_hash=SecretStr(
            create_scrypt_password_hash("tenant-password")
        ),
        local_auth_tenant_id="tenant-demo",
    )


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Settings]:
    settings = _settings()
    monkeypatch.setattr(api_module, "_settings", lambda: settings)
    return create_app(local_auth_service=LocalAuthService.from_settings(settings)), settings


def test_scrypt_hash_verification_rejects_wrong_password_and_format() -> None:
    encoded = create_scrypt_password_hash("correct password")
    assert verify_scrypt_password("correct password", encoded) is True
    assert verify_scrypt_password("wrong password", encoded) is False
    assert verify_scrypt_password("correct password", "not-a-scrypt-hash") is False


def test_local_auth_requires_complete_localhost_development_configuration() -> None:
    with pytest.raises(ValidationError, match="LOCAL_PASSWORD_AUTH_ENABLED is allowed only"):
        Settings(app_env="production", pms_demo_mode=False, local_password_auth_enabled=True)
    with pytest.raises(ValidationError, match="local password authentication requires"):
        Settings(local_password_auth_enabled=True)


def test_four_role_login_sets_http_only_cookie_and_builds_trusted_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _app(monkeypatch)

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            status = await client.get("/auth/local/status")
            response = await client.post(
                "/auth/local/login",
                json={
                    "username": "demo.do",
                    "password": "do-password",
                    "role": "Data Entry Operator",
                },
            )
            me = await client.get("/api/v1/me")
        assert status.json() == {
            "enabled": True,
            "roles": ["Data Entry Operator", "Nodal/Regional Officer", "HOD", "Tenant"],
        }
        assert response.status_code == 200
        assert "pms_local_access_token=" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "do-password" not in response.text
        assert me.json()["subject"] == "local.demo.do"
        assert me.json()["roles"] == ["Data Entry Operator"]

    asyncio.run(scenario())


def test_local_auth_rejects_role_mismatch_and_revokes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _app(monkeypatch)

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            mismatch = await client.post(
                "/auth/local/login",
                json={
                    "username": "demo.do",
                    "password": "do-password",
                    "role": "HOD",
                },
            )
            login = await client.post(
                "/auth/local/login",
                json={
                    "username": "demo.tenant",
                    "password": "tenant-password",
                    "role": "Tenant",
                },
            )
            me = await client.get("/api/v1/me")
            logout = await client.post("/auth/local/logout")
            after_logout = await client.get("/api/v1/me")
        assert mismatch.status_code == 401
        assert login.status_code == 200
        assert me.json()["tenant_id"] == "tenant-demo"
        assert logout.status_code == 204
        assert after_logout.status_code == 401

    asyncio.run(scenario())


def test_disabled_local_auth_has_no_login_surface() -> None:
    app = create_app()

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            status = await client.get("/auth/local/status")
            login = await client.post(
                "/auth/local/login",
                json={"username": "x", "password": "x", "role": "HOD"},
            )
        assert status.json() == {"enabled": False, "roles": []}
        assert login.status_code == 404

    asyncio.run(scenario())
