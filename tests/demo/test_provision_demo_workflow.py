from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _module() -> object:
    path = Path("scripts/provision_demo_workflow.py")
    spec = importlib.util.spec_from_file_location("provision_demo_workflow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_demo_values_link_the_workflow() -> None:
    module = _module()
    column = module.ColumnSpec("customer_code", "character varying", 100)

    assert module.demo_value("bridge_letout_tenancy_plot", column, 3) == "DCT003"
    assert module.demo_value("dim_lease_particulars_snapshot", column, 3) == "DCT003"
    assert (
        module.demo_value(
            "bridge_letout_tenancy_plot",
            module.ColumnSpec("tenancy_id", "character varying", 50),
            3,
        )
        == "DTEN-003"
    )


def test_sensitive_columns_are_refused() -> None:
    module = _module()

    with pytest.raises(ValueError, match="sensitive column"):
        module.demo_value("example", module.ColumnSpec("new_password", "character varying", 100), 1)


def test_customer_table_is_part_of_the_connected_demo_workflow() -> None:
    module = _module()

    assert "dim_customer_legacy" in module.WORKFLOW_TABLES
