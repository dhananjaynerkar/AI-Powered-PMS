from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pms_forecasting.contracts import (
    FeatureRow,
    ForecastPrediction,
    TargetDefinition,
)
from pydantic import ValidationError


def test_vague_target_is_rejected() -> None:
    with pytest.raises(ValidationError, match="too vague"):
        TargetDefinition(
            target_name="revenue",
            description="A deliberately vague revenue target",
            unit="INR",
            frequency="monthly",
            entity_level="port",
            business_owner="Finance",
            definition_status="approved",
        )


def test_blocked_target_requires_reason() -> None:
    with pytest.raises(ValidationError, match="blocked targets"):
        TargetDefinition(
            target_name="payment_delay_days",
            description="Days from contractual due date to receipt",
            unit="days",
            frequency="event",
            entity_level="bill",
            business_owner="Finance",
            definition_status="blocked",
        )


def test_feature_row_rejects_future_observation() -> None:
    with pytest.raises(ValidationError, match="before data_cutoff"):
        FeatureRow(
            observation_date=date(2024, 1, 1),
            target_name="monthly_cash_collection",
            entity_id="division:1",
            entity_ids={"division_id": "1"},
            source_record_ids={"source_table": "cash_revenue_data"},
            target_value=Decimal("1"),
            features={"transaction_count": 1},
            feature_generation_version="1.0",
            data_cutoff=datetime(2024, 1, 1, tzinfo=UTC),
            leakage_safe_status="safe",
        )


def test_prediction_requires_nested_intervals() -> None:
    with pytest.raises(ValidationError, match="nested"):
        ForecastPrediction(
            entity_id="division:1",
            forecast_date=date(2024, 1, 1),
            forecast_horizon=1,
            point_estimate=Decimal("100"),
            lower_80=Decimal("80"),
            upper_80=Decimal("120"),
            lower_95=Decimal("90"),
            upper_95=Decimal("130"),
            model_version_id="model-v1",
            feature_snapshot_id="snapshot-1",
        )
