from __future__ import annotations

import json
from pathlib import Path

import pytest
from pms_common.settings import Settings
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_development_defaults_are_valid() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.app_port == 8000
    assert settings.source_schema == "public"
    assert settings.extract_schema == "pms_extract_2010_2023"
    assert settings.pii_log_redaction_enabled is True


def test_env_example_loads_as_development_placeholders() -> None:
    settings = Settings(_env_file=PROJECT_ROOT / ".env.example")

    assert settings.app_env == "development"
    assert settings.extract_schema == "pms_extract_2010_2023"
    assert settings.chat_schema == "pms_chat"
    assert settings.case_thread_mode == "shared_case_thread"
    assert settings.semantic_catalog_schema == "pms_catalog"
    assert settings.text_to_sql_mode == "templates_first"
    assert settings.text_to_sql_select_only is True
    assert settings.nl_to_sql_enabled is False
    assert settings.approved_sql_only is True
    assert settings.text_to_sql_max_plan_cost == 100_000


def test_environment_values_are_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "8123")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "15")

    settings = Settings(_env_file=None)

    assert settings.app_port == 8123
    assert settings.debug is True
    assert settings.db_connect_timeout_seconds == 15


def test_default_page_size_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="DEFAULT_PAGE_SIZE"):
        Settings(_env_file=None, default_page_size=501, max_page_size=500)


def test_case_summary_cannot_exceed_context_budget() -> None:
    with pytest.raises(ValidationError, match="CASE_SUMMARY_MAX_TOKENS"):
        Settings(
            _env_file=None,
            case_summary_max_tokens=1000,
            case_context_max_tokens=900,
        )


def test_unsafe_revision_2_guards_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, case_allow_silent_message_edit=True)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, text_to_sql_select_only=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, text_to_sql_ast_validation=False)
    with pytest.raises(ValidationError, match="NL_TO_SQL_ENABLED"):
        Settings(_env_file=None, nl_to_sql_enabled=True)


def test_text_to_sql_requires_an_allowlist_when_enabled() -> None:
    with pytest.raises(ValidationError, match="TEXT_TO_SQL_ALLOWLIST_SCHEMAS"):
        Settings(_env_file=None, text_to_sql_allowlist_schemas="")


def test_production_rejects_missing_secrets() -> None:
    with pytest.raises(ValidationError, match="production secrets are required"):
        Settings(_env_file=None, app_env="production")


def test_production_accepts_configured_secrets() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="app-value",
        field_encryption_key="field-value",
        password_pepper="pepper-value",
        postgres_password="postgres-value",
        keycloak_client_secret="keycloak-value",
        keycloak_issuer="https://keycloak.example/realms/pms",
        keycloak_jwks_url="https://keycloak.example/realms/pms/protocol/openid-connect/certs",
        keycloak_verify_ssl=True,
        minio_access_key="minio-access",
        minio_secret_key="minio-secret",
        database_url="postgresql+psycopg://pms:password@localhost/pms",
    )

    assert settings.app_env == "production"


def test_production_rejects_insecure_keycloak_transport() -> None:
    with pytest.raises(ValidationError, match="verified HTTPS"):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="app-value",
            field_encryption_key="field-value",
            password_pepper="pepper-value",
            postgres_password="postgres-value",
            keycloak_client_secret="keycloak-value",
            minio_access_key="minio-access",
            minio_secret_key="minio-secret",
            database_url="postgresql+psycopg://pms:password@localhost/pms",
        )


def test_safe_diagnostics_exclude_secret_values() -> None:
    secret_values = {
        "app_secret_key": "app-value",
        "field_encryption_key": "field-value",
        "password_pepper": "pepper-value",
        "postgres_password": "postgres-value",
        "keycloak_client_secret": "keycloak-value",
        "minio_access_key": "minio-access",
        "minio_secret_key": "minio-secret",
        "database_url": "postgresql+psycopg://pms:password@localhost/pms",
    }
    settings = Settings(_env_file=None, **secret_values)

    serialized = json.dumps(settings.safe_diagnostics(), sort_keys=True)

    for secret in secret_values.values():
        assert secret not in serialized
    assert settings.safe_diagnostics()["chat_schema"] == "pms_chat"
    assert settings.safe_diagnostics()["semantic_catalog_schema"] == "pms_catalog"
    assert settings.safe_diagnostics()["text_to_sql_select_only"] is True
