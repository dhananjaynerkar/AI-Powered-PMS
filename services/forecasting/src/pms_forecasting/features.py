"""Bounded PostgreSQL feature generation for approved Phase 11 targets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Connection, text

from pms_forecasting.contracts import FeatureRow


@dataclass(frozen=True, slots=True)
class FeatureBuildResult:
    feature_snapshot_id: str
    target_name: str
    row_count: int
    feature_hash: str
    dry_run: bool
    quality_flags: tuple[str, ...]


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def revenue_feature_rows(
    connection: Connection,
    *,
    cutoff: datetime,
    feature_version: str,
    source_table: str,
) -> tuple[FeatureRow, ...]:
    """Load pre-aggregated revenue without joining row-producing source tables."""

    rows = connection.execute(
        text(
            """
            SELECT month_start, division_key,
                   sum(transaction_count)::bigint AS transaction_count,
                   sum(amount_total)::numeric AS amount_total
            FROM pms_extract_2010_2023.model_revenue_monthly_by_source
            WHERE source_table = :source_table
              AND month_start < CAST(:cutoff AS date)
            GROUP BY month_start, division_key
            ORDER BY month_start, division_key
            """
        ),
        {"source_table": source_table, "cutoff": cutoff},
    ).mappings()
    quality_flags = (
        "ESTATE_DIMENSION_UNMAPPED",
        "CHARGE_CATEGORY_UNMAPPED",
        "AGGREGATED_SOURCE_RECORD_IDS",
    )
    return tuple(
        FeatureRow(
            observation_date=row["month_start"],
            target_name="monthly_cash_collection",
            entity_id=f"division:{row['division_key']}",
            entity_ids={
                "division_id": str(row["division_key"]),
                "estate_id": "UNMAPPED",
                "charge_category": "UNMAPPED",
            },
            source_record_ids={
                "source_table": source_table,
                "month_start": str(row["month_start"]),
                "division_key": str(row["division_key"]),
            },
            target_value=Decimal(row["amount_total"]),
            features={"transaction_count": int(row["transaction_count"])},
            feature_generation_version=feature_version,
            data_cutoff=cutoff,
            leakage_safe_status="safe",
            quality_flags=quality_flags,
        )
        for row in rows
    )


def build_revenue_features(
    connection: Connection,
    *,
    cutoff: datetime,
    feature_version: str,
    source_table: str,
    created_by_subject: str,
    dry_run: bool,
) -> FeatureBuildResult:
    rows = revenue_feature_rows(
        connection,
        cutoff=cutoff,
        feature_version=feature_version,
        source_table=source_table,
    )
    serialized = [row.model_dump(mode="json") for row in rows]
    feature_hash = _canonical_hash(serialized)
    snapshot_id = f"monthly-cash-{feature_hash[:24]}"
    quality_flags = tuple(sorted({flag for row in rows for flag in row.quality_flags}))
    result = FeatureBuildResult(
        feature_snapshot_id=snapshot_id,
        target_name="monthly_cash_collection",
        row_count=len(rows),
        feature_hash=feature_hash,
        dry_run=dry_run,
        quality_flags=quality_flags,
    )
    if dry_run:
        return result
    connection.execute(
        text(
            """
            INSERT INTO pms_forecast.feature_snapshot (
              feature_snapshot_id, target_name, feature_generation_version,
              data_cutoff, source_schema, source_tables, row_count,
              feature_hash, leakage_status, quality_flags, created_by_subject
            ) VALUES (
              :snapshot_id, :target_name, :version, :cutoff,
              'pms_extract_2010_2023', CAST(:source_tables AS jsonb), :row_count,
              :feature_hash, 'safe', CAST(:quality_flags AS jsonb), :subject
            )
            ON CONFLICT (feature_snapshot_id) DO NOTHING
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "target_name": result.target_name,
            "version": feature_version,
            "cutoff": cutoff,
            "source_tables": json.dumps(
                ["model_revenue_monthly_by_source", source_table]
            ),
            "row_count": len(rows),
            "feature_hash": feature_hash,
            "quality_flags": json.dumps(quality_flags),
            "subject": created_by_subject,
        },
    )
    statement = text(
        """
        INSERT INTO pms_forecast.fs_revenue_monthly (
          feature_row_id, feature_snapshot_id, observation_date, target_name,
          entity_id, entity_ids, source_record_ids, target_value, features,
          feature_generation_version, data_cutoff, leakage_safe_status,
          quality_flags, feature_hash
        ) VALUES (
          :row_id, :snapshot_id, :observation_date, :target_name, :entity_id,
          CAST(:entity_ids AS jsonb), CAST(:source_record_ids AS jsonb),
          :target_value, CAST(:features AS jsonb), :version, :cutoff,
          :leakage_status, CAST(:quality_flags AS jsonb), :feature_hash
        )
        ON CONFLICT (feature_row_id) DO NOTHING
        """
    )
    for row, payload in zip(rows, serialized, strict=True):
        row_hash = _canonical_hash(payload)
        connection.execute(
            statement,
            {
                "row_id": f"revenue-{row_hash[:32]}",
                "snapshot_id": snapshot_id,
                "observation_date": row.observation_date,
                "target_name": row.target_name,
                "entity_id": row.entity_id,
                "entity_ids": json.dumps(row.entity_ids),
                "source_record_ids": json.dumps(row.source_record_ids),
                "target_value": row.target_value,
                "features": json.dumps(row.features),
                "version": row.feature_generation_version,
                "cutoff": row.data_cutoff,
                "leakage_status": row.leakage_safe_status,
                "quality_flags": json.dumps(row.quality_flags),
                "feature_hash": row_hash,
            },
        )
    return result
