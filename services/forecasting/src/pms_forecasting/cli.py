"""Manual Phase 11 feature, training, evaluation, and promotion commands."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime

from pms_common.database import build_database_url
from pms_common.settings import Settings
from sqlalchemy import create_engine, text

from pms_forecasting.features import build_revenue_features
from pms_forecasting.training import (
    evaluation_rows,
    on_demand_predictions,
    persisted_predictions,
    promote,
    regenerate_stale_predictions,
    same_predictions,
    train,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pms_forecasting.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-features")
    build.add_argument("--target", required=True)
    build.add_argument("--cutoff")
    build.add_argument("--subject", default="phase11-cli")
    build.add_argument("--dry-run", action="store_true")
    training = commands.add_parser("train")
    training.add_argument("--target", required=True)
    training.add_argument("--models", default="seasonal_naive,ets")
    training.add_argument("--subject", default="phase11-cli")
    refresh = commands.add_parser("refresh-stale")
    refresh.add_argument("--target", required=True)
    refresh.add_argument("--models", default="seasonal_naive,ets")
    refresh.add_argument("--subject", default="phase14-cli")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--target", required=True)
    forecast = commands.add_parser("forecast")
    forecast.add_argument("--target", required=True)
    forecast.add_argument("--model-version-id")
    forecast.add_argument("--compare-precomputed", action="store_true")
    promotion = commands.add_parser("promote")
    promotion.add_argument("--run-id", required=True)
    promotion.add_argument("--approved-by", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    settings = Settings()
    write = arguments.command in {
        "build-features",
        "train",
        "refresh-stale",
        "promote",
    } and not getattr(
        arguments, "dry_run", False
    )
    trainer_settings = settings.model_copy(update={"database_url": None})
    engine = create_engine(
        build_database_url(trainer_settings),
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "sslmode": settings.db_ssl_mode,
            "options": (
                f"-c statement_timeout={settings.db_command_timeout_seconds * 1000}"
            ),
        },
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE pms_forecast_trainer"))
            if not write:
                connection.execute(text("SET TRANSACTION READ ONLY"))
            if arguments.command == "build-features":
                if arguments.target != "monthly_cash_collection":
                    raise ValueError(
                        f"target is data-blocked or unsupported: {arguments.target}"
                    )
                cutoff = datetime.fromisoformat(
                    arguments.cutoff or settings.forecast_default_cutoff
                )
                feature_result = build_revenue_features(
                    connection,
                    cutoff=cutoff,
                    feature_version=settings.forecast_feature_version,
                    source_table=settings.forecast_source_table,
                    created_by_subject=arguments.subject,
                    dry_run=arguments.dry_run,
                )
                print(json.dumps(asdict(feature_result), default=str, sort_keys=True))
            elif arguments.command == "train":
                training_result = train(
                    connection,
                    target_name=arguments.target,
                    model_names=tuple(
                        name.strip() for name in arguments.models.split(",") if name.strip()
                    ),
                    minimum_training=settings.forecast_min_train_periods,
                    horizon=settings.forecast_default_horizon_months,
                    step=settings.forecast_backtest_step_months,
                    max_folds=settings.forecast_backtest_folds,
                    season=settings.forecast_season_length,
                    created_by_subject=arguments.subject,
                )
                print(
                    json.dumps(
                        {
                            "training_run_id": training_result.training_run_id,
                            "selected_model": training_result.selected_model,
                            "selected_model_version_id": (
                                training_result.selected_model_version_id
                            ),
                            "promotion_required": training_result.promotion_required,
                            "metrics": {
                                item.model_name: item.metrics.model_dump()
                                for item in training_result.results
                            },
                        },
                        sort_keys=True,
                    )
                )
            elif arguments.command == "refresh-stale":
                refresh_result = regenerate_stale_predictions(
                    connection,
                    target_name=arguments.target,
                    model_names=tuple(
                        name.strip() for name in arguments.models.split(",") if name.strip()
                    ),
                    minimum_training=settings.forecast_min_train_periods,
                    horizon=settings.forecast_default_horizon_months,
                    step=settings.forecast_backtest_step_months,
                    max_folds=settings.forecast_backtest_folds,
                    season=settings.forecast_season_length,
                    created_by_subject=arguments.subject,
                )
                if refresh_result is None:
                    print(
                        json.dumps(
                            {"status": "FRESH", "target": arguments.target},
                            sort_keys=True,
                        )
                    )
                else:
                    print(
                        json.dumps(
                            {
                                "status": "REGENERATED",
                                "target": arguments.target,
                                "training_run_id": refresh_result.training_run_id,
                                "selected_model_version_id": (
                                    refresh_result.selected_model_version_id
                                ),
                            },
                            sort_keys=True,
                        )
                    )
            elif arguments.command == "evaluate":
                print(
                    json.dumps(
                        evaluation_rows(connection, arguments.target),
                        default=str,
                        sort_keys=True,
                    )
                )
            elif arguments.command == "forecast":
                predictions = on_demand_predictions(
                    connection,
                    target_name=arguments.target,
                    model_version_id=arguments.model_version_id,
                )
                if not predictions:
                    raise ValueError("on-demand forecasting returned no predictions")
                payload: dict[str, object] = {
                    "path": "on_demand_recompute",
                    "model_version_id": predictions[0].model_version_id,
                    "feature_snapshot_id": predictions[0].feature_snapshot_id,
                    "predictions": [item.model_dump(mode="json") for item in predictions],
                }
                if arguments.compare_precomputed:
                    stored = persisted_predictions(
                        connection,
                        target_name=arguments.target,
                        model_version_id=predictions[0].model_version_id,
                    )
                    matches = same_predictions(predictions, stored)
                    payload["matches_precomputed"] = matches
                    if not matches:
                        raise RuntimeError(
                            "on-demand and precomputed predictions do not match"
                        )
                print(json.dumps(payload, default=str, sort_keys=True))
            else:
                version = promote(
                    connection,
                    training_run_id=arguments.run_id,
                    approved_by=arguments.approved_by,
                )
                print(json.dumps({"status": "PROMOTED", "model_version_id": version}))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
