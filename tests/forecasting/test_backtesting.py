from __future__ import annotations

from datetime import date

from pms_forecasting.backtesting import (
    BacktestResult,
    MonthlyPoint,
    evaluate,
    rolling_origins,
    select_champion,
)
from pms_forecasting.contracts import ForecastMetrics
from pms_forecasting.training import forecast_predictions, same_predictions


def _monthly_points(count: int) -> tuple[MonthlyPoint, ...]:
    return tuple(
        MonthlyPoint(
            date(2010 + index // 12, index % 12 + 1, 1),
            float((index % 12 + 1) * 100),
        )
        for index in range(count)
    )


def test_rolling_origins_never_include_future_in_training() -> None:
    folds = rolling_origins(
        84,
        minimum_training=36,
        horizon=12,
        step=12,
        max_folds=3,
    )

    assert folds == ((48, 60), (60, 72), (72, 84))
    assert all(train_end < test_end for train_end, test_end in folds)


def test_seasonal_naive_is_exact_for_repeating_season() -> None:
    result = evaluate(
        {"division:1": _monthly_points(84)},
        model_name="seasonal_naive",
        minimum_training=36,
        horizon=12,
        step=12,
        max_folds=3,
        season=12,
    )

    assert result.metrics.mae == 0
    assert result.metrics.wape == 0
    assert result.metrics.folds == 3
    assert result.metrics.coverage_95 == 1


def _result(name: str, wape: float) -> BacktestResult:
    return BacktestResult(
        model_name=name,
        metrics=ForecastMetrics(
            mae=1,
            rmse=1,
            wape=wape,
            mase=1,
            bias=0,
            coverage_80=0.8,
            coverage_95=0.95,
            observations=12,
            folds=1,
        ),
        predictions=(1,),
        actuals=(1,),
        lower_80=(0,),
        upper_80=(2,),
        lower_95=(0,),
        upper_95=(2,),
    )


def test_challenger_must_strictly_beat_baseline() -> None:
    assert (
        select_champion((_result("seasonal_naive", 0.2), _result("ets", 0.2))).model_name
        == "seasonal_naive"
    )
    assert (
        select_champion((_result("seasonal_naive", 0.2), _result("ets", 0.1))).model_name
        == "ets"
    )


def test_on_demand_forecast_records_are_reproducible_for_one_snapshot() -> None:
    points = _monthly_points(48)

    first = forecast_predictions(
        model_name="seasonal_naive",
        model_version_id="cash-v1",
        feature_snapshot_id="snapshot-v1",
        entity_id="division:1",
        points=points,
        horizon=12,
        season=12,
    )
    second = forecast_predictions(
        model_name="seasonal_naive",
        model_version_id="cash-v1",
        feature_snapshot_id="snapshot-v1",
        entity_id="division:1",
        points=points,
        horizon=12,
        season=12,
    )

    assert len(first) == 12
    assert first[0].forecast_date == date(2014, 1, 1)
    assert first[0].model_version_id == "cash-v1"
    assert first[0].feature_snapshot_id == "snapshot-v1"
    assert same_predictions(first, second)
