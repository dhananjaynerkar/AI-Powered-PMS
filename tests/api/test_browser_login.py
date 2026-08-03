from __future__ import annotations

import asyncio
from typing import Any

import pms_api.app as api_module
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_common.settings import Settings
from pydantic import SecretStr


class _StubValidator:
    def __init__(self, role: UserRole = UserRole.HOD) -> None:
        self._role = role

    def validate(self, token: str) -> AuthorizationContext:
        assert token == "test-access-token"
        return AuthorizationContext(
            subject="browser-user",
            roles=frozenset({self._role}),
            tenant_id="tenant-demo" if self._role is UserRole.TENANT else None,
            department_id="estate",
            unit_id="land",
            classification=Classification.INTERNAL,
        )


def _settings() -> Settings:
    return Settings(
        app_host="127.0.0.1",
        app_port=8000,
        app_secret_key=SecretStr("test-app-secret"),
        keycloak_base_url="http://127.0.0.1:8080",
        keycloak_client_secret=SecretStr("test-client-secret"),
    )


def test_browser_login_builds_keycloak_redirect(monkeypatch: Any) -> None:
    monkeypatch.setattr(api_module, "_settings", lambda: _settings())
    response_location: str

    async def scenario() -> None:
        nonlocal response_location
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 303
        response_location = response.headers["location"]
        assert "protocol/openid-connect/auth" in response_location
        assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Fauth%2Fcallback" in response_location
        assert "pms_oauth_state=" in response.headers["set-cookie"]

    asyncio.run(scenario())
    assert "client_id=pms-api" in response_location
    assert "state=staff." in response_location


def test_browser_session_cookie_is_accepted(monkeypatch: Any) -> None:
    monkeypatch.setattr(api_module, "_validator", lambda: _StubValidator())

    async def scenario() -> None:
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            client.cookies.set("pms_access_token", "test-access-token")
            response = await client.get("/api/v1/me")
        assert response.status_code == 200
        assert response.json()["subject"] == "browser-user"

    asyncio.run(scenario())


def test_callback_sets_http_only_access_cookie(monkeypatch: Any) -> None:
    settings = _settings()
    monkeypatch.setattr(api_module, "_settings", lambda: settings)
    monkeypatch.setattr(api_module, "_exchange_oauth_code", lambda *_: "test-access-token")
    monkeypatch.setattr(api_module, "_validator", lambda: _StubValidator())

    async def scenario() -> None:
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            login = await client.get("/auth/login", follow_redirects=False)
            state_cookie = client.cookies.get("pms_oauth_state")
            assert state_cookie is not None
            state = login.headers["location"].split("state=", maxsplit=1)[1]
            callback = await client.get(
                f"/auth/callback?code=one-time-code&state={state}",
                follow_redirects=False,
            )
        assert callback.status_code == 303
        assert "pms_access_token=" in callback.headers["set-cookie"]
        assert "HttpOnly" in callback.headers["set-cookie"]

    asyncio.run(scenario())


def test_callback_rejects_tenant_in_staff_portal(monkeypatch: Any) -> None:
    settings = _settings()
    monkeypatch.setattr(api_module, "_settings", lambda: settings)
    monkeypatch.setattr(api_module, "_exchange_oauth_code", lambda *_: "test-access-token")
    monkeypatch.setattr(api_module, "_validator", lambda: _StubValidator(UserRole.TENANT))

    async def scenario() -> None:
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            login = await client.get("/auth/login?portal=staff", follow_redirects=False)
            state = login.headers["location"].split("state=", maxsplit=1)[1]
            callback = await client.get(
                f"/auth/callback?code=one-time-code&state={state}",
                follow_redirects=False,
            )
        assert callback.status_code == 403

    asyncio.run(scenario())


def test_callback_accepts_tenant_in_tenant_portal(monkeypatch: Any) -> None:
    settings = _settings()
    monkeypatch.setattr(api_module, "_settings", lambda: settings)
    monkeypatch.setattr(api_module, "_exchange_oauth_code", lambda *_: "test-access-token")
    monkeypatch.setattr(api_module, "_validator", lambda: _StubValidator(UserRole.TENANT))

    async def scenario() -> None:
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            login = await client.get("/auth/login?portal=tenant", follow_redirects=False)
            state = login.headers["location"].split("state=", maxsplit=1)[1]
            callback = await client.get(
                f"/auth/callback?code=one-time-code&state={state}",
                follow_redirects=False,
            )
        assert callback.status_code == 303

    asyncio.run(scenario())
