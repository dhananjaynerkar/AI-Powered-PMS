"""Leakage-safe rolling-origin evaluation for monthly forecasts."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pms_forecasting.contracts import ForecastMetrics


@dataclass(frozen=True, slots=True)
class MonthlyPoint:
    observation_date: date
    value: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    model_name: str
    metrics: ForecastMetrics
    predictions: tuple[float, ...]
    actuals: tuple[float, ...]
    lower_80: tuple[float, ...]
    upper_80: tuple[float, ...]
    lower_95: tuple[float, ...]
    upper_95: tuple[float, ...]


def rolling_origins(
    length: int,
    *,
    minimum_training: int,
    horizon: int,
    step: int,
    max_folds: int,
) -> tuple[tuple[int, int], ...]:
    """Return train-end/test-end indices, keeping every test strictly in the future."""

    if minimum_training < 1 or horizon < 1 or step < 1 or max_folds < 1:
        raise ValueError("rolling-origin parameters must be positive")
    candidates: list[tuple[int, int]] = []
    train_end = minimum_training
    while train_end + horizon <= length:
        candidates.append((train_end, train_end + horizon))
        train_end += step
    return tuple(candidates[-max_folds:])


def _seasonal_naive(train: list[float], horizon: int, season: int) -> list[float]:
    if len(train) < season:
        raise ValueError("seasonal naive requires at least one season")
    return [train[len(train) - season + (index % season)] for index in range(horizon)]


def _ets_additive(train: list[float], horizon: int, season: int) -> list[float]:
    """Small deterministic Holt-Winters additive implementation."""

    if len(train) < season * 2:
        raise ValueError("ETS requires at least two seasons")
    alpha, beta, gamma = 0.35, 0.10, 0.20
    level = sum(train[:season]) / season
    second = sum(train[season : season * 2]) / season
    trend = (second - level) / season
    seasonal = [train[index] - level for index in range(season)]
    for index, value in enumerate(train):
        previous = level
        season_value = seasonal[index % season]
        level = alpha * (value - season_value) + (1 - alpha) * (level + trend)
        trend = beta * (level - previous) + (1 - beta) * trend
        seasonal[index % season] = gamma * (value - level) + (1 - gamma) * season_value
    return [
        level + (index + 1) * trend + seasonal[(len(train) + index) % season]
        for index in range(horizon)
    ]


def forecast_values(
    model_name: str,
    train: list[float],
    horizon: int,
    season: int,
) -> list[float]:
    if model_name == "seasonal_naive":
        return _seasonal_naive(train, horizon, season)
    if model_name == "ets":
        return _ets_additive(train, horizon, season)
    raise ValueError(f"unsupported model: {model_name}")


def training_sigma(train: list[float], season: int) -> float:
    residuals = [
        train[index] - train[index - season] for index in range(season, len(train))
    ]
    return statistics.pstdev(residuals) if len(residuals) > 1 else 0.0


def evaluate(
    series: dict[str, tuple[MonthlyPoint, ...]],
    *,
    model_name: str,
    minimum_training: int,
    horizon: int,
    step: int,
    max_folds: int,
    season: int,
) -> BacktestResult:
    actuals: list[float] = []
    predictions: list[float] = []
    lower_80: list[float] = []
    upper_80: list[float] = []
    lower_95: list[float] = []
    upper_95: list[float] = []
    fold_count = 0
    scale_errors: list[float] = []
    for points in series.values():
        values = [point.value for point in points]
        origins = rolling_origins(
            len(values),
            minimum_training=minimum_training,
            horizon=horizon,
            step=step,
            max_folds=max_folds,
        )
        for train_end, test_end in origins:
            train = values[:train_end]
            actual = values[train_end:test_end]
            predicted = forecast_values(model_name, train, len(actual), season)
            sigma = training_sigma(train, season)
            actuals.extend(actual)
            predictions.extend(predicted)
            lower_80.extend(value - 1.2816 * sigma for value in predicted)
            upper_80.extend(value + 1.2816 * sigma for value in predicted)
            lower_95.extend(value - 1.96 * sigma for value in predicted)
            upper_95.extend(value + 1.96 * sigma for value in predicted)
            scale_errors.extend(
                abs(train[index] - train[index - season])
                for index in range(season, len(train))
            )
            fold_count += 1
    if not actuals or fold_count == 0:
        raise ValueError("insufficient complete history for rolling-origin evaluation")
    errors = [
        prediction - actual
        for prediction, actual in zip(predictions, actuals, strict=True)
    ]
    absolute = [abs(value) for value in errors]
    denominator = sum(abs(value) for value in actuals)
    scale = sum(scale_errors) / len(scale_errors) if scale_errors else 0.0
    coverage_80 = sum(
        low <= actual <= high
        for low, actual, high in zip(lower_80, actuals, upper_80, strict=True)
    ) / len(actuals)
    coverage_95 = sum(
        low <= actual <= high
        for low, actual, high in zip(lower_95, actuals, upper_95, strict=True)
    ) / len(actuals)
    metrics = ForecastMetrics(
        mae=sum(absolute) / len(absolute),
        rmse=math.sqrt(sum(value * value for value in errors) / len(errors)),
        wape=sum(absolute) / denominator if denominator else 0.0,
        mase=(sum(absolute) / len(absolute)) / scale if scale else 0.0,
        bias=sum(errors) / len(errors),
        coverage_80=coverage_80,
        coverage_95=coverage_95,
        observations=len(actuals),
        folds=fold_count,
    )
    return BacktestResult(
        model_name=model_name,
        metrics=metrics,
        predictions=tuple(predictions),
        actuals=tuple(actuals),
        lower_80=tuple(lower_80),
        upper_80=tuple(upper_80),
        lower_95=tuple(lower_95),
        upper_95=tuple(upper_95),
    )


def select_champion(results: tuple[BacktestResult, ...]) -> BacktestResult:
    """Require a challenger to strictly beat the seasonal-naive baseline on WAPE."""

    baseline = next(
        (result for result in results if result.model_name == "seasonal_naive"),
        None,
    )
    if baseline is None:
        raise ValueError("seasonal_naive baseline is mandatory")
    best = min(results, key=lambda result: result.metrics.wape)
    if best.model_name != "seasonal_naive" and best.metrics.wape >= baseline.metrics.wape:
        return baseline
    return best


def decimal_prediction(value: float) -> Decimal:
    return Decimal(str(max(0.0, value))).quantize(Decimal("0.01"))
