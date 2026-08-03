from __future__ import annotations

from pathlib import Path

import pytest
from pms_common.security import (
    AuthorizationContext,
    AuthorizationDenied,
    Classification,
    UserRole,
)
from pms_rule_engine.engine import RuleCalculationEngine
from pms_rule_engine.service import RuleCalculationService

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260730_0008_effective_dated_rule_engine.py"
MONTHLY_MIGRATION = ROOT / "db/migrations/versions/20260730_0009_monthly_rule_basis.py"


class _Repository:
    def load_approved_rules(self, request: object) -> tuple[object, ...]:
        raise AssertionError("unauthorized request reached rule persistence")


def test_tenant_cannot_invoke_rule_calculation() -> None:
    context = AuthorizationContext(
        subject="tenant",
        roles=frozenset({UserRole.TENANT}),
        tenant_id="tenant-1",
        department_id=None,
        classification=Classification.INTERNAL,
    )
    service = RuleCalculationService(  # type: ignore[arg-type]
        _Repository(),
        context,
        RuleCalculationEngine(),
    )

    with pytest.raises(AuthorizationDenied):
        service.calculate(object())  # type: ignore[arg-type]


def test_migration_is_application_owned_and_fail_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "Revises: 20260730_0007" in sql
    assert "EXCLUDE USING gist" in sql
    assert "independent Finance and Legal approvals" in sql
    assert "completed calculation evidence is immutable" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE public." not in sql
    assert "ALTER TABLE pms_extract_2010_2023." not in sql


def test_monthly_basis_extension_refuses_to_touch_protected_schemas() -> None:
    sql = MONTHLY_MIGRATION.read_text(encoding="utf-8")

    assert "Revises: 20260730_0008" in sql
    assert "'per_area_per_month'" in sql
    assert "'full_calendar_months'" in sql
    assert "ALTER TABLE public." not in sql
    assert "ALTER TABLE pms_extract_2010_2023." not in sql
