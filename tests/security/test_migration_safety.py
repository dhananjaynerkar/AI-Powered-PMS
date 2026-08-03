"""Protected-schema migration policy tests."""

from pathlib import Path

import pytest
from pms_common.migration_safety import (
    UnsafeMigrationError,
    validate_migration_source,
    validate_revision_directory,
)


@pytest.mark.parametrize(
    "source",
    [
        'op.execute(\'DROP TABLE "public"."customers"\')',
        'op.execute("ALTER SCHEMA pms_extract_2010_2023 RENAME TO old")',
        'op.execute("DROP VIEW public.customer_summary")',
        'op.drop_table("customers", schema="public")',
        'op.alter_column("leases", "amount", schema="pms_extract_2010_2023")',
        'op.drop_schema("public")',
    ],
)
def test_protected_schema_mutations_are_rejected(source: str) -> None:
    with pytest.raises(UnsafeMigrationError):
        validate_migration_source(source)


def test_application_schema_migration_is_allowed() -> None:
    validate_migration_source(
        'op.execute(sa.schema.CreateSchema("pms_app", if_not_exists=True))'
    )


def test_checked_in_revisions_pass_policy() -> None:
    versions = Path(__file__).parents[2] / "db" / "migrations" / "versions"
    validate_revision_directory(versions)
