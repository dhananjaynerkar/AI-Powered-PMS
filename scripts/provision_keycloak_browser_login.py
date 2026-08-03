"""Provision local browser-login secrets without printing them.

This command is intentionally limited to the local ``pms`` realm and its
``pms-api`` confidential client. It reads the already configured Keycloak
bootstrap administrator credentials from the ignored ``.env`` file, retrieves
the existing client secret, generates ``APP_SECRET_KEY`` when absent, and
updates only those two ignored settings.
"""

from __future__ import annotations

import argparse
import json
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
KEYCLOAK_BASE_URL = "http://127.0.0.1:8080"
REALM = "pms"
CLIENT_ID = "pms-api"


def _read_env() -> dict[str, str]:
    """Read simple dotenv assignments without displaying their values."""

    if not ENV_PATH.is_file():
        raise RuntimeError(".env is required for local Keycloak provisioning")
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip()
    return values


def _request_json(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    token: str | None = None,
) -> Any:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"Keycloak {method} request failed for {url}") from error


def _admin_token(values: dict[str, str]) -> str:
    username = values.get("KC_BOOTSTRAP_ADMIN_USERNAME", "")
    password = values.get("KC_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("Keycloak bootstrap administrator credentials are required")
    payload = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": username,
            "password": password,
        }
    ).encode("utf-8")
    result = _request_json(
        f"{KEYCLOAK_BASE_URL}/realms/master/protocol/openid-connect/token",
        method="POST",
        data=payload,
    )
    token = result.get("access_token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Keycloak administrator token was not returned")
    return token


def _client_secret(token: str) -> str:
    clients = _request_json(
        f"{KEYCLOAK_BASE_URL}/admin/realms/{REALM}/clients?clientId={CLIENT_ID}", token=token
    )
    if not isinstance(clients, list) or len(clients) != 1 or not isinstance(clients[0], dict):
        raise RuntimeError("expected exactly one pms-api Keycloak client")
    internal_id = clients[0].get("id")
    if not isinstance(internal_id, str) or not internal_id:
        raise RuntimeError("pms-api client has no Keycloak internal ID")
    result = _request_json(
        f"{KEYCLOAK_BASE_URL}/admin/realms/{REALM}/clients/{internal_id}/client-secret",
        token=token,
    )
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(value, str) or not value:
        raise RuntimeError("pms-api client secret was not returned")
    return value


def _replace_env_values(replacements: dict[str, str]) -> None:
    """Replace only named dotenv keys and preserve all unrelated lines."""

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    pending = set(replacements)
    updated: list[str] = []
    for line in lines:
        key = line.split("=", maxsplit=1)[0].strip() if "=" in line else ""
        if key in replacements:
            updated.append(f"{key}={replacements[key]}")
            pending.remove(key)
        else:
            updated.append(line)
    updated.extend(f"{key}={replacements[key]}" for key in sorted(pending))
    ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the two local .env settings")
    parser.add_argument(
        "--app-only",
        action="store_true",
        help="generate only APP_SECRET_KEY; do not contact Keycloak",
    )
    args = parser.parse_args()
    values = _read_env()
    if args.app_only:
        if not args.apply:
            print("READY APP_SECRET_KEY can be generated locally")
            print("DRY_RUN rerun with --apply --app-only to configure it")
            return 0
        app_secret = values.get("APP_SECRET_KEY") or secrets.token_urlsafe(48)
        _replace_env_values({"APP_SECRET_KEY": app_secret})
        print("PASS APP_SECRET_KEY configured in ignored .env")
        print("PASS no secret value was printed")
        return 0
    token = _admin_token(values)
    client_secret = _client_secret(token)
    app_secret = values.get("APP_SECRET_KEY") or secrets.token_urlsafe(48)
    if not args.apply:
        print("READY Keycloak administrator authentication succeeded")
        print("READY pms-api confidential client secret is available")
        print("DRY_RUN rerun with --apply to configure APP_SECRET_KEY and KEYCLOAK_CLIENT_SECRET")
        return 0
    _replace_env_values(
        {"APP_SECRET_KEY": app_secret, "KEYCLOAK_CLIENT_SECRET": client_secret}
    )
    print("PASS local browser-login secrets configured in ignored .env")
    print("PASS no secret value was printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
