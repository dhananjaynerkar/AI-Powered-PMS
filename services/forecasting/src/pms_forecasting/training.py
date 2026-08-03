"""Persist reproducible rolling backtests and candidate forecasts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pms_forecasting.backtesting import (
    BacktestResult,
    MonthlyPoint,
    evaluate,
    forecast_values,
    select_champion,
    training_sigma,
)
from pms_forecasting.contracts import ForecastPrediction


@dataclass(frozen=True, slots=True)
class TrainingResult:
    training_run_id: str
    selected_model_version_id: str
    selected_model: str
    results: tuple[BacktestResult, ...]
    promotion_required: bool = True


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def _money(value: float) -> Decimal:
    return Decimal(str(max(0.0, value))).quantize(Decimal("0.01"))


def forecast_predictions(
    *,
    model_name: str,
    model_version_id: str,
    feature_snapshot_id: str,
    entity_id: str,
    points: tuple[MonthlyPoint, ...],
    horizon: int,
    season: int,
) -> tuple[ForecastPrediction, ...]:
    """Recompute one entity's forecast from an immutable feature snapshot."""

    if not points:
        raise ValueError("forecast requires at least one feature point")
    values = [point.value for point in points]
    estimates = forecast_values(model_name, values, horizon, season)
    sigma = training_sigma(values, season)
    origin = points[-1].observation_date
    return tuple(
        ForecastPrediction(
            entity_id=entity_id,
            forecast_date=_add_months(origin, index),
            forecast_horizon=index,
            point_estimate=_money(value),
            lower_80=_money(value - 1.2816 * sigma),
            upper_80=_money(value + 1.2816 * sigma),
            lower_95=_money(value - 1.96 * sigma),
            upper_95=_money(value + 1.96 * sigma),
            model_version_id=model_version_id,
            feature_snapshot_id=feature_snapshot_id,
        )
        for index, value in enumerate(estimates, start=1)
    )


def same_predictions(
    left: tuple[ForecastPrediction, ...],
    right: tuple[ForecastPrediction, ...],
) -> bool:
    """Return whether the on-demand and persisted paths are identical."""

    return left == right


def on_demand_predictions(
    connection: Connection,
    *,
    target_name: str,
    model_version_id: str | None = None,
) -> tuple[ForecastPrediction, ...]:
    """Recompute a candidate forecast without persisting or promoting it."""

    if target_name != "monthly_cash_collection":
        raise ValueError(f"target is data-blocked or unsupported: {target_name}")
    version = connection.execute(
        text(
            """
            SELECT definition.algorithm, version.model_version_id,
                   version.feature_snapshot_id,
                   (version.parameters ->> 'season_length')::integer AS season_length
            FROM pms_forecast.model_version AS version
            JOIN pms_forecast.model_definition AS definition
              ON definition.model_name = version.model_name
            WHERE version.target_name = :target_name
              AND version.model_version_id = COALESCE(
                CAST(:model_version_id AS text),
                (
                  SELECT selected_model_version_id
                  FROM pms_forecast.training_run
                  WHERE target_name = :target_name
                    AND status = 'completed'
                    AND selected_model_version_id IS NOT NULL
                  ORDER BY completed_at DESC
                  LIMIT 1
                )
              )
            LIMIT 1
            """
        ),
        {"target_name": target_name, "model_version_id": model_version_id},
    ).mappings().one_or_none()
    if version is None:
        raise ValueError("a persisted model version is required for on-demand forecasting")
    rows = connection.execute(
        text(
            """
            SELECT entity_id, observation_date, target_value
            FROM pms_forecast.fs_revenue_monthly
            WHERE feature_snapshot_id = :feature_snapshot_id
              AND leakage_safe_status = 'safe'
            ORDER BY entity_id, observation_date
            """
        ),
        {"feature_snapshot_id": version["feature_snapshot_id"]},
    ).mappings()
    series: dict[str, list[MonthlyPoint]] = {}
    for row in rows:
        series.setdefault(str(row["entity_id"]), []).append(
            MonthlyPoint(row["observation_date"], float(row["target_value"]))
        )
    if not series:
        raise ValueError("the model version has no leakage-safe feature rows")
    freshest_date = max(
        point.observation_date for points in series.values() for point in points
    )
    return tuple(
        prediction
        for entity_id, points in series.items()
        if points[-1].observation_date == freshest_date
        for prediction in forecast_predictions(
            model_name=str(version["algorithm"]),
            model_version_id=str(version["model_version_id"]),
            feature_snapshot_id=str(version["feature_snapshot_id"]),
            entity_id=entity_id,
            points=tuple(points),
            horizon=12,
            season=int(version["season_length"]),
        )
    )


def persisted_predictions(
    connection: Connection,
    *,
    target_name: str,
    model_version_id: str,
) -> tuple[ForecastPrediction, ...]:
    """Load precomputed predictions for one immutable model version."""

    rows = connection.execute(
        text(
            """
            SELECT prediction.entity_id, prediction.forecast_date,
                   prediction.forecast_horizon, prediction.point_estimate,
                   prediction.lower_80, prediction.upper_80,
                   prediction.lower_95, prediction.upper_95,
                   feature.feature_snapshot_id
            FROM pms_forecast.prediction AS prediction
            JOIN pms_forecast.prediction_feature_snapshot AS feature
              ON feature.prediction_feature_snapshot_id =
                 prediction.prediction_feature_snapshot_id
            WHERE prediction.target_name = :target_name
              AND prediction.model_version_id = :model_version_id
            ORDER BY prediction.entity_id, prediction.forecast_date
            """
        ),
        {"target_name": target_name, "model_version_id": model_version_id},
    ).mappings()
    return tuple(
        ForecastPrediction(
            entity_id=str(row["entity_id"]),
            forecast_date=row["forecast_date"],
            forecast_horizon=int(row["forecast_horizon"]),
            point_estimate=Decimal(row["point_estimate"]),
            lower_80=Decimal(row["lower_80"]),
            upper_80=Decimal(row["upper_80"]),
            lower_95=Decimal(row["lower_95"]),
            upper_95=Decimal(row["upper_95"]),
            model_version_id=model_version_id,
            feature_snapshot_id=str(row["feature_snapshot_id"]),
        )
        for row in rows
    )


def train(
    connection: Connection,
    *,
    target_name: str,
    model_names: tuple[str, ...],
    minimum_training: int,
    horizon: int,
    step: int,
    max_folds: int,
    season: int,
    created_by_subject: str,
) -> TrainingResult:
    if target_name != "monthly_cash_collection":
        raise ValueError(f"target is not trainable in the configured data: {target_name}")
    if "seasonal_naive" not in model_names:
        raise ValueError("seasonal_naive baseline is mandatory")
    snapshot = connection.execute(
        text(
            """
            SELECT feature_snapshot_id, data_cutoff, feature_hash, quality_flags
            FROM pms_forecast.feature_snapshot
            WHERE target_name = :target_name AND leakage_status = 'safe'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"target_name": target_name},
    ).mappings().one_or_none()
    if snapshot is None:
        raise ValueError("build a leakage-safe feature snapshot before training")
    rows = connection.execute(
        text(
            """
            SELECT entity_id, observation_date, target_value
            FROM pms_forecast.fs_revenue_monthly
            WHERE feature_snapshot_id = :snapshot_id
              AND leakage_safe_status = 'safe'
            ORDER BY entity_id, observation_date
            """
        ),
        {"snapshot_id": snapshot["feature_snapshot_id"]},
    ).mappings()
    series: dict[str, list[MonthlyPoint]] = {}
    for row in rows:
        series.setdefault(str(row["entity_id"]), []).append(
            MonthlyPoint(row["observation_date"], float(row["target_value"]))
        )
    immutable_series = {key: tuple(values) for key, values in series.items()}
    results = tuple(
        evaluate(
            immutable_series,
            model_name=model,
            minimum_training=minimum_training,
            horizon=horizon,
            step=step,
            max_folds=max_folds,
            season=season,
        )
        for model in model_names
    )
    selected = select_champion(results)
    run_payload = {
        "target": target_name,
        "snapshot": snapshot["feature_snapshot_id"],
        "models": model_names,
        "metrics": {
            result.model_name: result.metrics.model_dump() for result in results
        },
    }
    run_id = f"run-{_hash(run_payload)[:24]}"
    connection.execute(
        text(
            """
            INSERT INTO pms_forecast.training_run (
              training_run_id, target_name, feature_snapshot_id,
              requested_models, status, selection_rule, created_by_subject
            ) VALUES (
              :run_id, :target, :snapshot, CAST(:models AS jsonb),
              'running', :rule, :subject
            )
            ON CONFLICT (training_run_id) DO NOTHING
            """
        ),
        {
            "run_id": run_id,
            "target": target_name,
            "snapshot": snapshot["feature_snapshot_id"],
            "models": json.dumps(model_names),
            "rule": "lowest WAPE; a challenger must strictly beat seasonal_naive",
            "subject": created_by_subject,
        },
    )
    version_ids: dict[str, str] = {}
    dates = [point.observation_date for values in immutable_series.values() for point in values]
    for result in results:
        model_name = f"{target_name}_{result.model_name}"
        version_payload = {
            "model": model_name,
            "snapshot": snapshot["feature_snapshot_id"],
            "season": season,
            "implementation": "1.0",
        }
        artifact_hash = _hash(version_payload)
        version_id = f"{model_name}-v-{artifact_hash[:16]}"
        version_ids[result.model_name] = version_id
        connection.execute(
            text(
                """
                INSERT INTO pms_forecast.model_definition (
                  model_name, algorithm, target_name, is_baseline,
                  implementation_version
                ) VALUES (:model, :algorithm, :target, :baseline, '1.0')
                ON CONFLICT (model_name) DO NOTHING
                """
            ),
            {
                "model": model_name,
                "algorithm": result.model_name,
                "target": target_name,
                "baseline": result.model_name == "seasonal_naive",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO pms_forecast.model_version (
                  model_version_id, model_name, target_name, feature_snapshot_id,
                  training_period_from, training_period_to, training_data_cutoff,
                  feature_hash, parameters, artifact_hash, limitations
                ) VALUES (
                  :version, :model, :target, :snapshot, :period_from, :period_to,
                  :cutoff, :feature_hash, CAST(:parameters AS jsonb),
                  :artifact_hash, CAST(:limitations AS jsonb)
                )
                ON CONFLICT (model_version_id) DO NOTHING
                """
            ),
            {
                "version": version_id,
                "model": model_name,
                "target": target_name,
                "snapshot": snapshot["feature_snapshot_id"],
                "period_from": min(dates),
                "period_to": max(dates),
                "cutoff": snapshot["data_cutoff"],
                "feature_hash": snapshot["feature_hash"],
                "parameters": json.dumps({"season_length": season}),
                "artifact_hash": artifact_hash,
                "limitations": json.dumps(snapshot["quality_flags"]),
            },
        )
        comparison = {
            "baseline_wape": next(
                item.metrics.wape
                for item in results
                if item.model_name == "seasonal_naive"
            ),
            "candidate_wape": result.metrics.wape,
            "strictly_beats_baseline": result.metrics.wape
            < next(
                item.metrics.wape
                for item in results
                if item.model_name == "seasonal_naive"
            ),
        }
        connection.execute(
            text(
                """
                INSERT INTO pms_forecast.evaluation_result (
                  evaluation_result_id, training_run_id, model_version_id,
                  split_strategy, fold_count, horizon_months, metrics,
                  interval_coverage, baseline_comparison, leakage_check_passed
                ) VALUES (
                  :evaluation, :run, :version, 'rolling_origin', :folds,
                  :horizon, CAST(:metrics AS jsonb), CAST(:coverage AS jsonb),
                  CAST(:comparison AS jsonb), true
                )
                ON CONFLICT (training_run_id, model_version_id) DO NOTHING
                """
            ),
            {
                "evaluation": f"eval-{_hash((run_id, version_id))[:24]}",
                "run": run_id,
                "version": version_id,
                "folds": result.metrics.folds,
                "horizon": horizon,
                "metrics": result.metrics.model_dump_json(),
                "coverage": json.dumps(
                    {
                        "lower_80_upper_80": result.metrics.coverage_80,
                        "lower_95_upper_95": result.metrics.coverage_95,
                    }
                ),
                "comparison": json.dumps(comparison),
            },
        )
    selected_version = version_ids[selected.model_name]
    connection.execute(
        text(
            """
            UPDATE pms_forecast.training_run
            SET status = 'completed', completed_at = now(),
                selected_model_version_id = :selected
            WHERE training_run_id = :run
            """
        ),
        {"selected": selected_version, "run": run_id},
    )
    freshest_date = max(dates)
    for entity_id, points in immutable_series.items():
        if points[-1].observation_date != freshest_date:
            continue
        values = [point.value for point in points]
        forecast_records = forecast_predictions(
            model_name=selected.model_name,
            model_version_id=selected_version,
            feature_snapshot_id=str(snapshot["feature_snapshot_id"]),
            entity_id=entity_id,
            points=points,
            horizon=horizon,
            season=season,
        )
        feature_payload = {
            "entity_id": entity_id,
            "forecast_origin": freshest_date,
            "recent_season": values[-season:],
            "model_version_id": selected_version,
            "feature_snapshot_id": snapshot["feature_snapshot_id"],
        }
        prediction_feature_hash = _hash(feature_payload)
        prediction_feature_id = f"prediction-fs-{prediction_feature_hash[:24]}"
        connection.execute(
            text(
                """
                INSERT INTO pms_forecast.prediction_feature_snapshot (
                  prediction_feature_snapshot_id, feature_snapshot_id, entity_id,
                  forecast_origin, features, feature_hash
                ) VALUES (
                  :prediction_snapshot, :feature_snapshot, :entity,
                  :origin, CAST(:features AS jsonb), :feature_hash
                )
                ON CONFLICT (prediction_feature_snapshot_id) DO NOTHING
                """
            ),
            {
                "prediction_snapshot": prediction_feature_id,
                "feature_snapshot": snapshot["feature_snapshot_id"],
                "entity": entity_id,
                "origin": freshest_date,
                "features": json.dumps(feature_payload, default=str),
                "feature_hash": prediction_feature_hash,
            },
        )
        for forecast in forecast_records:
            prediction_id = f"prediction-{_hash(
                (target_name, entity_id, forecast.forecast_date, selected_version,
                 prediction_feature_id)
            )[:24]}"
            connection.execute(
                text(
                    """
                    INSERT INTO pms_forecast.prediction (
                      prediction_id, target_name, entity_id, forecast_date,
                      forecast_horizon, point_estimate, lower_80, upper_80,
                      lower_95, upper_95, model_version_id,
                      prediction_feature_snapshot_id, assumptions, review_status
                    ) VALUES (
                      :prediction, :target, :entity, :forecast_date, :horizon,
                      :point, :lower_80, :upper_80, :lower_95, :upper_95,
                      :version, :prediction_snapshot,
                      CAST(:assumptions AS jsonb), 'candidate'
                    )
                    ON CONFLICT (
                      target_name, entity_id, forecast_date, model_version_id,
                      prediction_feature_snapshot_id
                    ) DO NOTHING
                    """
                ),
                {
                    "prediction": prediction_id,
                    "target": target_name,
                    "entity": entity_id,
                    "forecast_date": forecast.forecast_date,
                    "horizon": forecast.forecast_horizon,
                    "point": forecast.point_estimate,
                    "lower_80": forecast.lower_80,
                    "upper_80": forecast.upper_80,
                    "lower_95": forecast.lower_95,
                    "upper_95": forecast.upper_95,
                    "version": selected_version,
                    "prediction_snapshot": prediction_feature_id,
                    "assumptions": json.dumps(
                        [
                            "candidate model; explicit promotion required",
                            "estate and charge category are unmapped",
                            "negative interval bounds are truncated to zero",
                        ]
                    ),
                },
            )
    return TrainingResult(
        training_run_id=run_id,
        selected_model_version_id=selected_version,
        selected_model=selected.model_name,
        results=results,
    )


def prediction_refresh_required(
    connection: Connection,
    *,
    target_name: str,
) -> bool:
    """Return whether the latest safe feature snapshot supersedes predictions.

    Predictions are immutable artifacts. A changed safe feature snapshot must
    produce a new candidate training run; it must never silently reuse the
    previous snapshot.
    """

    latest = connection.execute(
        text(
            """
            SELECT feature_snapshot_id
            FROM pms_forecast.feature_snapshot
            WHERE target_name = :target_name AND leakage_status = 'safe'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"target_name": target_name},
    ).scalar_one_or_none()
    selected = connection.execute(
        text(
            """
            SELECT version.feature_snapshot_id
            FROM pms_forecast.training_run AS run
            JOIN pms_forecast.model_version AS version
              ON version.model_version_id = run.selected_model_version_id
            WHERE run.target_name = :target_name
              AND run.status = 'completed'
              AND run.selected_model_version_id IS NOT NULL
            ORDER BY run.completed_at DESC
            LIMIT 1
            """
        ),
        {"target_name": target_name},
    ).scalar_one_or_none()
    return latest is not None and latest != selected


def regenerate_stale_predictions(
    connection: Connection,
    *,
    target_name: str,
    model_names: tuple[str, ...],
    minimum_training: int,
    horizon: int,
    step: int,
    max_folds: int,
    season: int,
    created_by_subject: str,
) -> TrainingResult | None:
    """Train a new candidate only when a newer safe snapshot exists."""

    if target_name != "monthly_cash_collection":
        raise ValueError(f"target is not refreshable in the configured data: {target_name}")
    if not prediction_refresh_required(connection, target_name=target_name):
        return None
    return train(
        connection,
        target_name=target_name,
        model_names=model_names,
        minimum_training=minimum_training,
        horizon=horizon,
        step=step,
        max_folds=max_folds,
        season=season,
        created_by_subject=created_by_subject,
    )


def promote(
    connection: Connection,
    *,
    training_run_id: str,
    approved_by: str,
) -> str:
    """Explicitly promote the selected candidate; never called by training."""

    row = connection.execute(
        text(
            """
            SELECT target_name, selected_model_version_id
            FROM pms_forecast.training_run
            WHERE training_run_id = :run AND status = 'completed'
            """
        ),
        {"run": training_run_id},
    ).one_or_none()
    if row is None or row[1] is None:
        raise ValueError("completed training run with a selected model is required")
    connection.execute(
        text(
            """
            UPDATE pms_forecast.model_version
            SET approval_status = 'retired'
            WHERE target_name = :target AND approval_status = 'champion'
            """
        ),
        {"target": row[0]},
    )
    connection.execute(
        text(
            """
            UPDATE pms_forecast.model_version
            SET approval_status = 'champion', approved_by = :approved_by,
                approved_at = now()
            WHERE model_version_id = :version
            """
        ),
        {"approved_by": approved_by, "version": row[1]},
    )
    return str(row[1])


def evaluation_rows(connection: Connection, target_name: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT run.training_run_id, version.model_name,
                       version.model_version_id, version.approval_status,
                       evaluation.metrics, evaluation.interval_coverage,
                       evaluation.baseline_comparison,
                       evaluation.leakage_check_passed
                FROM pms_forecast.training_run AS run
                JOIN pms_forecast.evaluation_result AS evaluation
                  ON evaluation.training_run_id = run.training_run_id
                JOIN pms_forecast.model_version AS version
                  ON version.model_version_id = evaluation.model_version_id
                WHERE run.target_name = :target
                ORDER BY run.completed_at DESC, version.model_name
                """
            ),
            {"target": target_name},
        ).mappings()
    ]
