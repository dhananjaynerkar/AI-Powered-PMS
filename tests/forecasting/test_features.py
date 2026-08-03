from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pms_forecasting.features import revenue_feature_rows


class _Rows:
    def mappings(self) -> _Rows:
        return self

    def __iter__(self) -> Any:
        yield {
            "month_start": date(2023, 12, 1),
            "division_key": "1",
            "transaction_count": 10,
            "amount_total": Decimal("125.50"),
        }


class _Connection:
    def execute(self, statement: object, parameters: object) -> _Rows:
        assert "GROUP BY month_start, division_key" in str(statement)
        assert parameters == {
            "source_table": "cash_revenue_data",
            "cutoff": datetime(2024, 1, 1, tzinfo=UTC),
        }
        return _Rows()


def test_revenue_feature_generation_preserves_cutoff_and_quality_flags() -> None:
    rows = revenue_feature_rows(
        _Connection(),  # type: ignore[arg-type]
        cutoff=datetime(2024, 1, 1, tzinfo=UTC),
        feature_version="1.0",
        source_table="cash_revenue_data",
    )

    assert len(rows) == 1
    assert rows[0].target_value == Decimal("125.50")
    assert rows[0].leakage_safe_status == "safe"
    assert "ESTATE_DIMENSION_UNMAPPED" in rows[0].quality_flags
    assert rows[0].observation_date < rows[0].data_cutoff.date()
