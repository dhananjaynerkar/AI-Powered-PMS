"""Static infrastructure and migration safety checks for Phase 05."""

from __future__ import annotations

from pathlib import Path

from pms_common.migration_safety import validate_migration_source

ROOT = Path(__file__).parents[2]
MIGRATION = (
    ROOT
    / "db"
    / "migrations"
    / "versions"
    / "20260729_0004_document_registry.py"
)


def test_phase05_migration_is_linear_private_and_immutable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    validate_migration_source(source)
    assert 'down_revision: str | None = "20260728_0003"' in source
    assert "pms_extract_2010_2023" not in source
    assert 'schema=SCHEMA' in source
    assert source.count("FORCE ROW LEVEL SECURITY") >= 1
    assert "reject_immutable_object_change" in source
    assert "document_record" in source
    assert "stored_object" in source
    assert "document_version" in source
    assert "derived_artifact" in source


def test_minio_compose_is_private_bounded_and_persistent() -> None:
    compose = (ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infra" / "minio" / "Dockerfile").read_text(encoding="utf-8")

    assert "pms-minio-local:RELEASE.2025-10-15T17-29-55Z" in compose
    assert "ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z" in dockerfile
    assert '"127.0.0.1:9000:9000"' in compose
    assert '"127.0.0.1:9001:9001"' in compose
    assert "minio-data:/data" in compose
    assert 'restart: "on-failure:3"' in compose
    assert "public" not in compose.lower()
