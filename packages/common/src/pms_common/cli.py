"""Command-line entry points for safe local project operations."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Final

from sqlalchemy.exc import SQLAlchemyError

from pms_common.database import (
    create_database_engine,
    database_is_configured,
)
from pms_common.db_inspection import (
    check_database_health,
    inspect_database,
    load_expected_tables,
    verify_inspection,
    write_inventory,
)
from pms_common.preflight import project_root
from pms_common.settings import Settings

DEFAULT_INVENTORY_PATH: Final = Path("artifacts/database/extraction_schema_inventory.json")


def _configuration_required() -> int:
    print(
        json.dumps(
            {
                "status": "CONFIGURATION_REQUIRED",
                "reason": "database credentials are not configured",
                "next_command": "Copy-Item .env.example .env",
            },
            sort_keys=True,
        )
    )
    return 2


def _inspect(settings: Settings, *, write_output: bool) -> int:
    if not database_is_configured(settings):
        return _configuration_required()
    root = project_root()
    expected_tables = load_expected_tables(root)
    engine = create_database_engine(settings, read_only=True)
    try:
        inspection = inspect_database(
            engine,
            extraction_schema=settings.extract_schema,
            expected_tables=expected_tables,
        )
    except SQLAlchemyError:
        print(
            json.dumps(
                {
                    "status": "CONNECTION_FAILED",
                    "reason": "PostgreSQL metadata inspection failed",
                },
                sort_keys=True,
            )
        )
        return 2
    finally:
        engine.dispose()

    payload = inspection.to_dict()
    payload.pop("columns")
    payload["column_count"] = len(inspection.columns)
    if write_output:
        output_path = root / DEFAULT_INVENTORY_PATH
        write_inventory(inspection, output_path)
        payload["inventory_path"] = str(output_path)
    payload["status"] = "INSPECTED"
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _verify(settings: Settings) -> int:
    if not database_is_configured(settings):
        return _configuration_required()
    root = project_root()
    expected_tables = load_expected_tables(root)
    engine = create_database_engine(settings, read_only=True)
    try:
        inspection = inspect_database(
            engine,
            extraction_schema=settings.extract_schema,
            expected_tables=expected_tables,
        )
    except SQLAlchemyError:
        print(json.dumps({"status": "CONNECTION_FAILED"}, sort_keys=True))
        return 2
    finally:
        engine.dispose()
    issues = verify_inspection(inspection)
    print(
        json.dumps(
            {
                "status": "PASS" if not issues else "FAIL",
                "database_name": inspection.database_name,
                "connected_user": inspection.connected_user,
                "postgres_version": inspection.postgres_version,
                "extraction_schema_exists": inspection.schema_exists,
                "base_table_count": inspection.base_table_count,
                "missing_critical_tables": inspection.missing_critical_tables,
                "issues": issues,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not issues else 1


def _health(settings: Settings) -> int:
    if not database_is_configured(settings):
        return _configuration_required()
    engine = create_database_engine(settings, read_only=True)
    try:
        health = check_database_health(engine)
    except SQLAlchemyError:
        print(json.dumps({"status": "CONNECTION_FAILED"}, sort_keys=True))
        return 2
    finally:
        engine.dispose()
    print(json.dumps({"status": "PASS", **asdict(health)}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the bounded CLI parser."""

    parser = argparse.ArgumentParser(prog="python -m pms_common.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    db_parser = commands.add_parser("db")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)
    db_commands.add_parser("inspect")
    db_commands.add_parser("verify")
    db_commands.add_parser("health")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch a supported command."""

    arguments = build_parser().parse_args(argv)
    settings = Settings()
    if arguments.db_command == "inspect":
        return _inspect(settings, write_output=True)
    if arguments.db_command == "verify":
        return _verify(settings)
    if arguments.db_command == "health":
        return _health(settings)
    raise AssertionError("argparse accepted an unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
