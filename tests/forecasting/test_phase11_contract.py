from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260730_0010_forecast_feature_store.py"
TRAINER_MIGRATION = (
    ROOT / "db/migrations/versions/20260730_0011_forecast_trainer_policy.py"
)


def test_phase11_migration_is_application_owned_and_governed() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "Revises: 20260730_0009" in source
    for table in (
        "fs_revenue_monthly",
        "fs_payment_bill_level",
        "fs_land_value",
        "fs_lease_lifecycle",
        "fs_inspection_risk",
        "model_definition",
        "model_version",
        "training_run",
        "evaluation_result",
        "prediction",
        "prediction_feature_snapshot",
    ):
        assert table in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE public." not in source
    assert "ALTER TABLE pms_extract_2010_2023." not in source


def test_phase11_requires_explicit_model_promotion() -> None:
    source = (
        ROOT / "services/forecasting/src/pms_forecasting/training.py"
    ).read_text(encoding="utf-8")

    assert "promotion_required: bool = True" in source
    assert "def promote(" in source
    assert "approval_status = 'champion'" in source


def test_offline_trainer_policy_does_not_expand_runtime_or_source_schema() -> None:
    source = TRAINER_MIGRATION.read_text(encoding="utf-8")

    assert "Revises: 20260730_0010" in source
    assert "current_user = 'pms_forecast_trainer'" in source
    assert "pms_app_runtime" not in source
    assert "ALTER TABLE public." not in source
    assert "ALTER TABLE pms_extract_2010_2023." not in source
