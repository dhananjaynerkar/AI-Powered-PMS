"""Live PostgreSQL Phase 03 gate.

The module skips unless intentional database credentials are present.
"""

import pytest
from pms_common.database import create_database_engine, database_is_configured
from pms_common.db_inspection import (
    EXPECTED_TABLE_COUNT,
    check_database_health,
    inspect_database,
    load_expected_tables,
    verify_inspection,
)
from pms_common.preflight import project_root
from pms_common.settings import Settings

settings = Settings()
pytestmark = pytest.mark.skipif(
    not database_is_configured(settings),
    reason=".env database credentials are required for the live integration gate",
)


def test_postgresql_connection_and_extraction_schema() -> None:
    expected_tables = load_expected_tables(project_root())
    engine = create_database_engine(settings, read_only=True)
    try:
        inspection = inspect_database(
            engine,
            extraction_schema=settings.extract_schema,
            expected_tables=expected_tables,
        )
        health = check_database_health(engine)
    finally:
        engine.dispose()

    assert health.database_name == inspection.database_name
    assert inspection.base_table_count == EXPECTED_TABLE_COUNT
    assert inspection.missing_critical_tables == ()
    assert verify_inspection(inspection) == ()
