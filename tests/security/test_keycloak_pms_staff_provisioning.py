from __future__ import annotations

import builtins
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

import pytest


def _module() -> Any:
    path = Path("scripts/provision_keycloak_pms_staff.py")
    spec = importlib.util.spec_from_file_location("provision_keycloak_pms_staff", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _HttpMock:
    def __init__(self, module: Any) -> None:
        self.module = module
        self.writes: list[tuple[str, str, Any]] = []
        self.requests: list[tuple[str, str, str | None, Any]] = []
        self.auth_calls = 0

    def __call__(
        self, method: str, path: str, *, body: Any = None, token: str | None = None
    ) -> Any:
        response = self.module.HttpResponse
        self.requests.append((method, path, token, body))
        if path.endswith("/master/protocol/openid-connect/token"):
            assert method == "POST"
            self.auth_calls += 1
            return response(
                200,
                {"access_token": f"opaque-admin-token-{self.auth_calls}", "expires_in": 3600},
                {},
            )
        if method in {"POST", "PUT", "DELETE"}:
            self.writes.append((method, path, body))
            return response(204, None, {"Location": "http://keycloak/users/new-id"})
        if path.endswith("/users/profile"):
            return response(200, {"attributes": [{"name": "email", "multivalued": False}]}, {})
        if "/clients?clientId=" in path:
            return response(200, [{"id": "client-internal-id", "clientId": "pms-api"}], {})
        if path.endswith("/protocol-mappers/models"):
            return response(200, [], {})
        if "/roles/" in path:
            role_name = path.rsplit("/", 1)[-1]
            return response(200, {"id": f"role-{role_name}", "name": role_name}, {})
        if "/users?username=" in path:
            return response(200, [], {})
        raise AssertionError(f"unexpected mocked request: {method} {path}")


def test_profile_merge_preserves_existing_and_adds_missing() -> None:
    module = _module()
    merged, changed = module.merge_profile(
        {"attributes": [{"name": "email", "displayName": "Email"}, {"name": "department"}]}
    )

    assert changed is True
    assert merged["attributes"][0] == {"name": "email", "displayName": "Email"}
    names = {item["name"] for item in merged["attributes"]}
    assert names == {"email", "department", "unit_id", "classification"}
    for item in merged["attributes"]:
        if item["name"] in {"department", "unit_id", "classification"}:
            assert item["multivalued"] is False
            assert item["required"] is False
            assert item["permissions"] == {"view": ["admin", "user"], "edit": ["admin"]}


def test_repeated_profile_and_mapper_planning_is_idempotent() -> None:
    module = _module()
    first, _ = module.merge_profile({"attributes": []})
    second, changed = module.merge_profile(first)
    assert changed is False
    assert second == first

    expectations = module._mapper_expectations()
    mappers = [{"id": name, "name": name, **expected} for name, expected in expectations.items()]
    assert all(plan.action == "keep" for plan in module.build_mapper_plans(mappers))


def test_dry_run_uses_mocked_http_and_performs_no_writes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    transport = _HttpMock(module)
    api = module.KeycloakAdmin(request=transport)
    api.authenticate("permanent-admin", "never-print-this")
    assert module.run("dry-run", api=api, provision_staff=False, reset_password=False) == 0
    assert transport.writes == []
    output = capsys.readouterr().out
    assert "never-print-this" not in output
    assert "opaque-admin-token" not in output
    assert "DRY_RUN no Keycloak writes performed" in output


def test_put_has_bearer_and_json_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    captured: list[Any] = []

    class Response:
        status = 204
        headers: dict[str, str] = {}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b""

    def urlopen(request: Any, timeout: int) -> Response:
        captured.append(request)
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    api = module.KeycloakAdmin()
    api._request_http(
        "PUT",
        "/admin/realms/pms/users/profile",
        body={"attributes": []},
        token="current-token",
    )
    request = captured[0]
    assert request.get_header("Authorization") == "Bearer current-token"
    assert request.get_header("Content-type") == "application/json"


def test_password_collection_is_followed_by_fresh_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    transport = _HttpMock(module)
    api = module.KeycloakAdmin(request=transport)
    api.authenticate("admin", "admin-secret")
    plan = module.StaffPlan("new-user", "HOD", None, "role-hod")
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: "staff-secret")
    monkeypatch.setattr(builtins, "input", lambda _prompt: "YES")
    module._prompt_staff_passwords((plan,), reset=False)
    auth_before_refresh = transport.auth_calls
    api.prepare_for_writes()
    assert transport.auth_calls == auth_before_refresh + 1


def test_expired_token_refreshes_before_request() -> None:
    module = _module()
    transport = _HttpMock(module)
    api = module.KeycloakAdmin(request=transport)
    api.authenticate("admin", "admin-secret")
    api._token_expires_at = time.monotonic() - 1
    api.profile()
    assert transport.auth_calls == 2
    assert transport.requests[-1][2] == "opaque-admin-token-2"


class _UnauthorizedOnce:
    def __init__(self, module: Any, failures: int) -> None:
        self.module = module
        self.failures = failures
        self.auth_calls = 0
        self.calls: list[tuple[str, str, str | None, Any]] = []

    def __call__(
        self, method: str, path: str, *, body: Any = None, token: str | None = None
    ) -> Any:
        response = self.module.HttpResponse
        self.calls.append((method, path, token, body))
        if path.endswith("/master/protocol/openid-connect/token"):
            self.auth_calls += 1
            return response(
                200, {"access_token": f"token-{self.auth_calls}", "expires_in": 3600}, {}
            )
        if self.failures:
            self.failures -= 1
            return response(401, {"error": "unauthorized", "error_description": "expired"}, {})
        return response(200, {"attributes": []}, {})


def test_one_401_refreshes_once_and_retries_once() -> None:
    module = _module()
    transport = _UnauthorizedOnce(module, failures=1)
    api = module.KeycloakAdmin(request=transport)
    api.authenticate("admin", "admin-secret")
    api.profile()
    assert transport.auth_calls == 2
    assert len([item for item in transport.calls if item[1].endswith("/users/profile")]) == 2


def test_second_401_fails_without_unbounded_retry() -> None:
    module = _module()
    transport = _UnauthorizedOnce(module, failures=2)
    api = module.KeycloakAdmin(request=transport)
    api.authenticate("admin", "admin-secret")
    with pytest.raises(module.ProvisioningError, match="HTTP 401"):
        api.profile()
    assert transport.auth_calls == 2
    assert len([item for item in transport.calls if item[1].endswith("/users/profile")]) == 2


def test_retry_replays_one_idempotent_write_without_duplicate_success() -> None:
    module = _module()
    transport = _UnauthorizedOnce(module, failures=1)
    api = module.KeycloakAdmin(request=transport)
    api.authenticate("admin", "admin-secret")
    body = {"attributes": [{"name": "department"}]}
    response = api.write("PUT", "/admin/realms/pms/users/profile", body)
    attempts = [item for item in transport.calls if item[1].endswith("/users/profile")]
    assert response.status == 200
    assert len(attempts) == 2
    assert attempts[0][3] == attempts[1][3] == body
    assert sum(item[1].endswith("/users/profile") and item[0] == "PUT" for item in attempts) == 2


class _RoleFake:
    def __init__(self, module: Any) -> None:
        self.module = module
        self.calls: list[tuple[str, str, Any]] = []

    def clients(self) -> list[dict[str, Any]]:
        return [{"id": "client-id"}]

    def prepare_for_writes(self) -> None:
        return None

    def write(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        return self.module.HttpResponse(204, None, {"Location": "http://keycloak/users/new"})


def test_apply_removes_other_business_roles_and_assigns_one() -> None:
    module = _module()
    fake = _RoleFake(module)
    role_map = {name: {"id": f"id-{name}"} for name in module.BUSINESS_ROLES}
    user = {"id": "user-id", "username": "dhananjay.do", "realmRoles": ["HOD", "Tenant"]}
    plan = module.StaffPlan("dhananjay.do", "Data Entry Operator", user, "id-Data Entry Operator")
    module._apply(fake, {"attributes": []}, False, (), (plan,), role_map)

    deletes = [call for call in fake.calls if call[0] == "DELETE"]
    assigns = [call for call in fake.calls if call[0] == "POST" and "role-mappings" in call[1]]
    assert {item["name"] for item in deletes[0][2]} == {"HOD", "Tenant"}
    assert assigns[0][2] == [{"id": "id-Data Entry Operator", "name": "Data Entry Operator"}]


def test_write_failure_stops_without_following_writes() -> None:
    module = _module()

    class Failing(module.KeycloakAdmin):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def write(self, method: str, path: str, body: Any = None) -> Any:
            self.calls.append(path)
            raise module.ProvisioningError("mocked write failure")

        def clients(self) -> list[dict[str, Any]]:
            return [{"id": "client-id"}]

        def prepare_for_writes(self) -> None:
            return None

    api = Failing()
    with pytest.raises(module.ProvisioningError, match="mocked write failure"):
        module._apply(api, {"attributes": []}, True, (), (), {})
    assert len(api.calls) == 1
