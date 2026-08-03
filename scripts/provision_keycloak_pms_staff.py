"""Idempotently provision the PMS Keycloak profile, mappers and staff users.

The command is intentionally scoped to the ``pms`` realm.  It prompts for the
permanent Keycloak administrator credentials, uses them only to obtain a
short-lived master-realm token, and never prints or persists either secret.
Use exactly one of ``--dry-run``, ``--validate`` or ``--apply``.  Live writes
are deliberately not performed by this module's import or by its tests.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

BASE_URL = "http://127.0.0.1:8080"
REALM = "pms"
CLIENT_ID = "pms-api"
MASTER_REALM = "master"
TOKEN_REFRESH_MARGIN_SECONDS = 30.0

BUSINESS_ROLES = (
    "Administrator",
    "Auditor",
    "Data Entry Operator",
    "Estate Officer",
    "Finance Officer",
    "HOD",
    "Legal Officer",
    "Nodal/Regional Officer",
    "Tenant",
)

STAFF_USERS = (
    ("dhananjay.do", "Data Entry Operator"),
    ("dhananjay.no", "Nodal/Regional Officer"),
    ("dhananjay.hod", "HOD"),
)


class ProvisioningError(RuntimeError):
    """A safe, non-secret provisioning failure."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: Any
    headers: dict[str, str]


class KeycloakAdmin:
    """Small Admin REST client restricted to the local PMS realm."""

    def __init__(
        self, base_url: str = BASE_URL, request: Callable[..., HttpResponse] | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None
        self._admin_username: str | None = None
        self._admin_password: str | None = None
        self._token_expires_at = 0.0
        self._request_impl = request or self._request_http

    def _request_http(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        token: str | None = None,
    ) -> HttpResponse:
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if body is not None:
            if method == "POST" and path.endswith("/token"):
                data = urllib.parse.urlencode(body).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                parsed: Any = None
                if raw:
                    try:
                        parsed = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        parsed = raw.decode("utf-8", errors="replace")
                return HttpResponse(response.status, parsed, dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            raw = error.read()
            parsed_error: Any = None
            if raw:
                try:
                    parsed_error = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    parsed_error = raw.decode("utf-8", errors="replace")
            return HttpResponse(error.code, parsed_error, dict(error.headers.items()))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ProvisioningError(f"Keycloak {method} {path} could not be reached") from error

    @staticmethod
    def _error_detail(body: Any) -> str:
        if isinstance(body, dict):
            values = [body.get("error"), body.get("error_description")]
            text = "; ".join(str(value) for value in values if isinstance(value, str))
        elif isinstance(body, str):
            text = body
        else:
            return ""
        text = re.sub(
            r"(?i)(password|passwd|secret|token|refresh_token|client_secret)\s*[:=]\s*[^,;\s]+",
            r"\1=<redacted>",
            text,
        )
        return " ".join(text.split())[:240]

    def _failure(self, method: str, path: str, response: HttpResponse) -> ProvisioningError:
        detail = self._error_detail(response.body)
        suffix = f": {detail}" if detail else ""
        return ProvisioningError(
            f"Keycloak {method} {path} returned HTTP {response.status}{suffix}"
        )

    def _reauthenticate(self) -> None:
        if self._admin_username is None or self._admin_password is None:
            raise ProvisioningError("administrator credentials are unavailable for token refresh")
        self.authenticate(self._admin_username, self._admin_password)

    def _refresh_if_needed(self, *, force: bool = False) -> None:
        if force or self.token is None or (
            self._token_expires_at - time.monotonic() < TOKEN_REFRESH_MARGIN_SECONDS
        ):
            self._reauthenticate()

    def prepare_for_writes(self) -> None:
        """Acquire a fresh token immediately before the first write phase."""

        self._refresh_if_needed(force=True)

    def request(self, method: str, path: str, *, body: Any = None) -> HttpResponse:
        self._refresh_if_needed()
        response = self._request_impl(method, path, body=body, token=self.token)
        if response.status == 401:
            self._reauthenticate()
            response = self._request_impl(method, path, body=body, token=self.token)
            if response.status == 401:
                raise self._failure(method, path, response)
        if response.status >= 400:
            raise self._failure(method, path, response)
        return response

    def authenticate(self, username: str, password: str) -> None:
        response = self._request_impl(
            "POST",
            f"/realms/{MASTER_REALM}/protocol/openid-connect/token",
            body={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": username,
                "password": password,
            },
        )
        result = response.body
        token = result.get("access_token") if isinstance(result, dict) else None
        if response.status >= 400 or not isinstance(token, str) or not token:
            raise self._failure(
                "POST", f"/realms/{MASTER_REALM}/protocol/openid-connect/token", response
            )
        expires_in = result.get("expires_in") if isinstance(result, dict) else None
        if isinstance(expires_in, (int, float, str)):
            try:
                lifetime = float(expires_in)
            except ValueError:
                lifetime = 300.0
        else:
            lifetime = 300.0
        if lifetime <= 0:
            raise ProvisioningError("Keycloak administrator token has invalid expires_in")
        self._admin_username = username
        self._admin_password = password
        self.token = token
        self._token_expires_at = time.monotonic() + lifetime

    def profile(self) -> dict[str, Any]:
        body = self.request("GET", f"/admin/realms/{REALM}/users/profile").body
        if not isinstance(body, dict):
            raise ProvisioningError("Keycloak user-profile response was invalid")
        return body

    def clients(self) -> list[dict[str, Any]]:
        body = self.request(
            "GET", f"/admin/realms/{REALM}/clients?clientId={urllib.parse.quote(CLIENT_ID)}"
        ).body
        if not isinstance(body, list) or len(body) != 1 or not isinstance(body[0], dict):
            raise ProvisioningError("expected exactly one pms-api client in the pms realm")
        return body

    def mappers(self, client_id: str) -> list[dict[str, Any]]:
        body = self.request(
            "GET", f"/admin/realms/{REALM}/clients/{client_id}/protocol-mappers/models"
        ).body
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise ProvisioningError("pms-api mapper response was invalid")
        return body

    def roles(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name in BUSINESS_ROLES:
            body = self.request(
                "GET", f"/admin/realms/{REALM}/roles/{urllib.parse.quote(name, safe='')}"
            ).body
            if not isinstance(body, dict) or not isinstance(body.get("id"), str):
                raise ProvisioningError(f"required realm role is missing: {name}")
            result[name] = body
        return result

    def users(self, username: str) -> list[dict[str, Any]]:
        body = self.request(
            "GET",
            f"/admin/realms/{REALM}/users?username={urllib.parse.quote(username)}&exact=true",
        ).body
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise ProvisioningError("Keycloak user response was invalid")
        return body

    def user_roles(self, user_id: str) -> list[dict[str, Any]]:
        body = self.request(
            "GET", f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm"
        ).body
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise ProvisioningError("Keycloak role-mapping response was invalid")
        return body

    def write(self, method: str, path: str, body: Any = None) -> HttpResponse:
        if method not in {"POST", "PUT", "DELETE"}:
            raise ProvisioningError("unsupported write method")
        return self.request(method, path, body=body)


def _profile_attribute(name: str) -> dict[str, Any]:
    """Return only supported User Profile fields for a managed attribute.

    Keycloak enables a managed attribute by including it in the profile.  The
    Admin REST schema has no per-attribute ``enabled`` property; ``enabled
    always`` therefore means the attribute is present and not conditionally
    required.  Permissions make it visible to both contexts and editable only
    by administrators.
    """

    return {
        "name": name,
        "multivalued": False,
        "required": False,
        "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
    }


def merge_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Preserve the profile and merge the three required managed attributes."""

    merged = dict(profile)
    existing = profile.get("attributes", [])
    if not isinstance(existing, list):
        raise ProvisioningError("Keycloak user-profile attributes were invalid")
    if any(not isinstance(item, dict) for item in existing):
        raise ProvisioningError("Keycloak user-profile attributes were invalid")
    attributes = [dict(item) for item in existing if isinstance(item, dict)]
    positions = {item.get("name"): index for index, item in enumerate(attributes)}
    managed_names = {"department", "unit_id", "classification"}
    if len(positions) != len(attributes) or any(
        sum(item.get("name") == name for item in attributes) > 1 for name in managed_names
    ):
        raise ProvisioningError("duplicate user-profile attributes require manual review")
    changed = len(attributes) != len(existing)
    for name in ("department", "unit_id", "classification"):
        desired = _profile_attribute(name)
        index = positions.get(name)
        if isinstance(index, int):
            current = attributes[index]
            updated = dict(current)
            updated.update(desired)
            if updated != current:
                changed = True
                attributes[index] = updated
        else:
            positions[name] = len(attributes)
            attributes.append(desired)
            changed = True
    merged["attributes"] = attributes
    return merged, changed


def _mapper_expectations() -> dict[str, dict[str, Any]]:
    def attribute(name: str) -> dict[str, Any]:
        return {
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": name,
                "claim.name": name,
                "jsonType.label": "String",
                "id.token.claim": "false",
                "access.token.claim": "true",
                "userinfo.token.claim": "false",
            },
        }

    return {
        "pms-api-audience": {
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {
                "included.client.audience": CLIENT_ID,
                "id.token.claim": "false",
                "access.token.claim": "true",
            },
        },
        "department": attribute("department"),
        "unit-id": attribute("unit_id"),
        "classification": attribute("classification"),
        "tenant-id": attribute("tenant_id"),
    }


def _mapper_matches(current: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(current.get(key) == value for key, value in expected.items())


@dataclass(frozen=True, slots=True)
class MapperPlan:
    name: str
    current: dict[str, Any] | None
    expected: dict[str, Any]

    @property
    def action(self) -> str:
        if self.current is None:
            return "create"
        return "keep" if _mapper_matches(self.current, self.expected) else "update"


@dataclass(frozen=True, slots=True)
class StaffPlan:
    username: str
    role: str
    user: dict[str, Any] | None
    role_id: str
    password: str | None = None


def build_mapper_plans(mappers: Iterable[dict[str, Any]]) -> tuple[MapperPlan, ...]:
    by_name: dict[str, dict[str, Any]] = {}
    for item in mappers:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        if name in by_name:
            raise ProvisioningError(f"duplicate pms-api mapper requires manual review: {name}")
        by_name[name] = item
    return tuple(
        MapperPlan(name, by_name.get(name), expected)
        for name, expected in _mapper_expectations().items()
    )


def _attribute_values(user: dict[str, Any]) -> dict[str, list[str]]:
    attrs = user.get("attributes", {})
    if not isinstance(attrs, dict):
        raise ProvisioningError("existing staff user has invalid attributes")
    return {str(key): list(value) for key, value in attrs.items() if isinstance(value, list)}


def plan_staff(api: KeycloakAdmin, role_map: dict[str, dict[str, Any]]) -> tuple[StaffPlan, ...]:
    plans: list[StaffPlan] = []
    for username, role in STAFF_USERS:
        matches = api.users(username)
        if len(matches) > 1:
            raise ProvisioningError(f"multiple Keycloak users match {username}")
        user = matches[0] if matches else None
        if user is not None and "tenant_id" in _attribute_values(user):
            raise ProvisioningError(
                f"staff user {username} already has tenant_id; manual review required"
            )
        if user is not None:
            user = dict(user)
            user_id = user.get("id")
            if not isinstance(user_id, str):
                raise ProvisioningError(f"existing user id is invalid for {username}")
            user["realmRoles"] = [
                item["name"]
                for item in api.user_roles(user_id)
                if isinstance(item.get("name"), str)
            ]
        role_id = role_map[role].get("id")
        if not isinstance(role_id, str):
            raise ProvisioningError(f"role id is invalid for {role}")
        plans.append(StaffPlan(username, role, user, role_id))
    return tuple(plans)


def _staff_user_payload(plan: StaffPlan) -> dict[str, Any]:
    existing = plan.user or {}
    attrs = _attribute_values(existing) if plan.user else {}
    attrs.update({"department": ["estate"], "unit_id": ["land"], "classification": ["internal"]})
    payload = {
        "username": plan.username,
        "enabled": True,
        "attributes": attrs,
    }
    for key in ("firstName", "lastName", "email", "emailVerified"):
        if key in existing:
            payload[key] = existing[key]
    return payload


def _role_names(user: dict[str, Any]) -> set[str]:
    values = user.get("realmRoles", [])
    return (
        {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()
    )


def _print_plan(
    profile_changed: bool, mapper_plans: Iterable[MapperPlan], staff: Iterable[StaffPlan]
) -> None:
    print(f"profile_attributes={'update' if profile_changed else 'keep'}")
    for mapper_plan in mapper_plans:
        print(f"mapper {mapper_plan.name}: {mapper_plan.action}")
    for staff_plan in staff:
        print(
            f"staff {staff_plan.username}: "
            f"{'create' if staff_plan.user is None else 'update'} role={staff_plan.role}"
        )


def _prompt_staff_passwords(plans: tuple[StaffPlan, ...], reset: bool) -> tuple[StaffPlan, ...]:
    prompted: list[StaffPlan] = []
    for plan in plans:
        if plan.user is not None and not reset:
            prompted.append(plan)
            continue
        password = getpass.getpass(f"Password for new/reset user {plan.username}: ")
        if not password:
            raise ProvisioningError(f"empty password refused for {plan.username}")
        confirmation = input(f"Type YES to set {plan.username}'s password as non-temporary: ")
        if confirmation != "YES":
            raise ProvisioningError(
                f"non-temporary password confirmation refused for {plan.username}"
            )
        prompted.append(StaffPlan(plan.username, plan.role, plan.user, plan.role_id, password))
    return tuple(prompted)


def _apply(
    api: KeycloakAdmin,
    profile: dict[str, Any],
    profile_changed: bool,
    plans: tuple[MapperPlan, ...],
    staff: tuple[StaffPlan, ...],
    role_map: dict[str, dict[str, Any]],
) -> None:
    api.prepare_for_writes()
    if profile_changed:
        merged, _ = merge_profile(profile)
        api.write("PUT", f"/admin/realms/{REALM}/users/profile", merged)
    clients = api.clients()
    client_internal_id = clients[0].get("id")
    if not isinstance(client_internal_id, str):
        raise ProvisioningError("pms-api client id is invalid")
    for mapper_plan in plans:
        path = f"/admin/realms/{REALM}/clients/{client_internal_id}/protocol-mappers/models"
        if mapper_plan.action == "create":
            api.write("POST", path, {"name": mapper_plan.name, **mapper_plan.expected})
        elif mapper_plan.action == "update" and mapper_plan.current is not None:
            mapper_id = mapper_plan.current.get("id")
            if not isinstance(mapper_id, str):
                raise ProvisioningError(f"mapper id is invalid for {mapper_plan.name}")
            api.write(
                "PUT",
                f"{path}/{mapper_id}",
                {"name": mapper_plan.name, **mapper_plan.expected},
            )
    for staff_plan in staff:
        payload = _staff_user_payload(staff_plan)
        if staff_plan.user is None:
            response = api.write("POST", f"/admin/realms/{REALM}/users", payload)
            location = response.headers.get("Location") or response.headers.get("location")
            if not location:
                raise ProvisioningError(
                    f"Keycloak did not return an id for {staff_plan.username}"
                )
            user_id = location.rstrip("/").rsplit("/", maxsplit=1)[-1]
            current_roles: set[str] = set()
        else:
            user_id_value = staff_plan.user.get("id")
            if not isinstance(user_id_value, str):
                raise ProvisioningError(f"existing user id is invalid for {staff_plan.username}")
            user_id = user_id_value
            api.write("PUT", f"/admin/realms/{REALM}/users/{user_id}", payload)
            current_roles = _role_names(staff_plan.user)
        remove = [
            {"id": role_map[name]["id"], "name": name}
            for name in BUSINESS_ROLES
            if name != staff_plan.role and name in current_roles
        ]
        if remove:
            api.write(
                "DELETE", f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm", remove
            )
        api.write(
            "POST",
            f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
            [{"id": staff_plan.role_id, "name": staff_plan.role}],
        )
        if staff_plan.password is not None:
            api.write(
                "PUT",
                f"/admin/realms/{REALM}/users/{user_id}/reset-password",
                {"type": "password", "value": staff_plan.password, "temporary": False},
            )


def run(mode: str, *, api: KeycloakAdmin, provision_staff: bool, reset_password: bool) -> int:
    profile = api.profile()
    merged, profile_changed = merge_profile(profile)
    clients = api.clients()
    client_id = clients[0].get("id")
    if not isinstance(client_id, str):
        raise ProvisioningError("pms-api client id is invalid")
    mapper_plans = build_mapper_plans(api.mappers(client_id))
    role_map = api.roles()
    staff = plan_staff(api, role_map) if provision_staff else ()
    if mode == "validate":
        if profile_changed or any(item.action != "keep" for item in mapper_plans):
            raise ProvisioningError("profile or pms-api mapper drift detected")
        for plan in staff:
            if plan.user is None or len(_role_names(plan.user) & set(BUSINESS_ROLES)) != 1:
                raise ProvisioningError(f"staff role drift detected for {plan.username}")
        print("PASS Keycloak PMS profile, mappers and requested staff state validated")
        return 0
    if mode == "dry-run":
        _print_plan(profile_changed, mapper_plans, staff)
        print("DRY_RUN no Keycloak writes performed")
        return 0
    staff_with_passwords = (
        _prompt_staff_passwords(staff, reset_password) if provision_staff else staff
    )
    _apply(
        api, profile, profile_changed, mapper_plans, staff_with_passwords, role_map
    )
    print("PASS Keycloak PMS profile, mappers and requested staff state applied")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--validate", action="store_true")
    parser.add_argument("--provision-staff", action="store_true")
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args(argv)
    username = input("Permanent Keycloak administrator username: ").strip()
    password = getpass.getpass("Permanent Keycloak administrator password: ")
    if not username or not password:
        print("ERROR administrator credentials are required", file=sys.stderr)
        return 2
    api = KeycloakAdmin()
    try:
        api.authenticate(username, password)
        mode = "apply" if args.apply else "validate" if args.validate else "dry-run"
        return run(
            mode,
            api=api,
            provision_staff=args.provision_staff,
            reset_password=args.reset_password,
        )
    except ProvisioningError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
