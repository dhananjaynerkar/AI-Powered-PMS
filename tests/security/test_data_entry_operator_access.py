"""Contract checks for the approved DO governed-read scope."""

from pathlib import Path

from pms_common.security import PORT_WIDE_ROLES, UserRole

ROOT = Path(__file__).parents[2]
REVISION = ROOT / "db/migrations/versions/20260801_0013_data_entry_operator_governed_reads.py"


def test_data_entry_operator_is_port_wide_for_governed_application_permissions() -> None:
    assert UserRole.DATA_ENTRY_OPERATOR in PORT_WIDE_ROLES


def test_data_entry_migration_targets_only_governed_select_policies() -> None:
    source = REVISION.read_text(encoding="utf-8")

    assert source.count("_select\")") == 24
    assert '"public"' not in source
    assert "pms_extract_2010_2023" not in source
    assert "pms_audit" not in source
    assert "Data Entry Operator" in source
