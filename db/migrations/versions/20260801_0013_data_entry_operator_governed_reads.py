"""Authorize Data Entry Operators to read governed PMS application data.

This revision changes only existing application-schema SELECT RLS policies.
It does not grant access to ``public``, extracted source schemas, credentials,
or audit-only records, and it preserves each policy's classification and ACL
predicates.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0013"
down_revision: str | None = "20260731_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICIES = (
    ("pms_catalog", "entity_identity_map", "identity_map_select"),
    ("pms_doc", "document_acl", "document_acl_select"),
    ("pms_forecast", "evaluation_result", "evaluation_result_select"),
    ("pms_forecast", "feature_snapshot", "feature_snapshot_select"),
    ("pms_forecast", "fs_inspection_risk", "fs_inspection_risk_select"),
    ("pms_forecast", "fs_land_value", "fs_land_value_select"),
    ("pms_forecast", "fs_lease_lifecycle", "fs_lease_lifecycle_select"),
    ("pms_forecast", "fs_payment_bill_level", "fs_payment_bill_level_select"),
    ("pms_forecast", "fs_revenue_monthly", "fs_revenue_monthly_select"),
    ("pms_forecast", "model_definition", "model_definition_select"),
    ("pms_forecast", "model_version", "model_version_select"),
    ("pms_forecast", "prediction", "prediction_select"),
    ("pms_forecast", "prediction_feature_snapshot", "prediction_feature_snapshot_select"),
    ("pms_forecast", "target_definition", "target_definition_select"),
    ("pms_forecast", "training_run", "training_run_select"),
    ("pms_graph", "graph_edge", "graph_edge_select"),
    ("pms_graph", "graph_node", "graph_node_select"),
    ("pms_rules", "calculation_input", "calculation_input_select"),
    ("pms_rules", "calculation_result", "calculation_result_select"),
    ("pms_rules", "gold_case", "gold_case_select"),
    ("pms_rules", "rule_approval", "rule_approval_select"),
    ("pms_rules", "rule_candidate", "rule_candidate_select"),
    ("pms_rules", "rule_definition", "rule_definition_select"),
    ("pms_vector", "chunk_acl", "chunk_acl_select"),
)

NO_ROLE = "pms_app.has_role('Nodal/Regional Officer'::text)"
HOD_ROLE = "pms_app.has_role('HOD'::text)"
DO_ROLE = "pms_app.has_role('Data Entry Operator'::text)"


def _change_role_predicate(*, add: bool) -> None:
    """Amend a fixed policy set while preserving server-deparsed predicates."""

    bind = op.get_bind()
    for schema, table, policy in POLICIES:
        current = bind.execute(
            sa.text(
                """
                SELECT qual
                FROM pg_policies
                WHERE schemaname = :schema AND tablename = :table AND policyname = :policy
                """
            ),
            {"schema": schema, "table": table, "policy": policy},
        ).scalar_one_or_none()
        if not isinstance(current, str):
            raise RuntimeError(f"expected SELECT policy is missing: {schema}.{table}.{policy}")
        if add:
            if DO_ROLE in current:
                raise RuntimeError(
                    f"Data Entry Operator is already present: {schema}.{table}.{policy}"
                )
            anchor = NO_ROLE if NO_ROLE in current else HOD_ROLE
            if anchor not in current:
                raise RuntimeError(f"staff predicate is missing: {schema}.{table}.{policy}")
            updated = current.replace(anchor, f"({DO_ROLE} OR {anchor})")
        else:
            anchors = (NO_ROLE, HOD_ROLE)
            expected = next(
                (
                    f"({DO_ROLE} OR {anchor})"
                    for anchor in anchors
                    if f"({DO_ROLE} OR {anchor})" in current
                ),
                None,
            )
            if expected is None:
                raise RuntimeError(
                    "expected Data Entry Operator predicate is missing: "
                    f"{schema}.{table}.{policy}"
                )
            original = expected.removeprefix(f"({DO_ROLE} OR ").removesuffix(")")
            updated = current.replace(expected, original)
        op.execute(
            sa.text(
                f'ALTER POLICY "{policy}" ON "{schema}"."{table}" USING ({updated})'
            )
        )


def upgrade() -> None:
    """Grant governed port-wide reads to the approved Data Entry Operator role."""

    _change_role_predicate(add=True)


def downgrade() -> None:
    """Restore the prior Data Entry Operator RLS scope."""

    _change_role_predicate(add=False)
