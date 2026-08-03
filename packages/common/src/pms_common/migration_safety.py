"""Static safety policy for Alembic migrations."""

from __future__ import annotations

import re
from pathlib import Path

PROTECTED_SCHEMAS = ("public", "pms_extract_2010_2023")
_SCHEMA_PATTERN = "|".join(re.escape(schema) for schema in PROTECTED_SCHEMAS)
_RAW_PROTECTED_DDL = re.compile(
    rf"\b(?:DROP|ALTER|TRUNCATE)\s+"
    rf"(?:TABLE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|TYPE|FUNCTION|PROCEDURE)\s+"
    rf"(?:IF\s+EXISTS\s+)?[\"']?(?:{_SCHEMA_PATTERN})[\"']?(?:\.|\s|;)",
    re.IGNORECASE | re.MULTILINE,
)
_ALEMBIC_PROTECTED_OPERATION = re.compile(
    rf"op\.(?:drop|alter|rename)_[a-z_]+\s*\(.*?"
    rf"schema\s*=\s*[\"'](?:{_SCHEMA_PATTERN})[\"']",
    re.IGNORECASE | re.DOTALL,
)
_QUALIFIED_PROTECTED_OPERATION = re.compile(
    rf"op\.(?:drop|alter|rename)_[a-z_]+\s*\(\s*"
    rf"[\"'](?:{_SCHEMA_PATTERN})\.",
    re.IGNORECASE,
)
_DIRECT_PROTECTED_SCHEMA_OPERATION = re.compile(
    rf"op\.(?:drop|rename)_schema\s*\(\s*[\"'](?:{_SCHEMA_PATTERN})[\"']",
    re.IGNORECASE,
)


class UnsafeMigrationError(ValueError):
    """Raised when a migration targets a protected source schema."""


def validate_migration_source(source: str, *, name: str = "<migration>") -> None:
    """Reject destructive operations against protected schemas."""

    if (
        _RAW_PROTECTED_DDL.search(source)
        or _ALEMBIC_PROTECTED_OPERATION.search(source)
        or _QUALIFIED_PROTECTED_OPERATION.search(source)
        or _DIRECT_PROTECTED_SCHEMA_OPERATION.search(source)
    ):
        raise UnsafeMigrationError(
            f"{name} contains DROP/ALTER/TRUNCATE against a protected schema"
        )


def validate_revision_directory(versions_path: Path) -> None:
    """Validate every Python revision before Alembic opens a transaction."""

    for path in sorted(versions_path.glob("*.py")):
        validate_migration_source(path.read_text(encoding="utf-8"), name=path.name)
