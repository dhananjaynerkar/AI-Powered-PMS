from datetime import date

import pytest
from pms_graph.models import (
    GraphEdgeInput,
    GraphEdgeType,
    GraphNodeInput,
    GraphNodeType,
    GraphQuery,
    GraphVerificationStatus,
)
from pydantic import ValidationError


def test_graph_inputs_default_to_unreviewed_candidates() -> None:
    node = GraphNodeInput(
        node_id="tenant-1",
        node_type=GraphNodeType.TENANT,
        canonical_entity_id="tenant-1",
        source_schema="pms_extract_2010_2023",
        source_table="dim_applicant_safe",
        source_record_id="1",
        created_by_subject="review-import",
    )
    edge = GraphEdgeInput(
        edge_id="edge-1",
        from_node_id="tenant-1",
        to_node_id="tenancy-1",
        edge_type=GraphEdgeType.TENANT_HAS_TENANCY,
        source_schema="pms_extract_2010_2023",
        source_table="bridge_applicant_tenancy",
        source_record_id="7",
        created_by_subject="review-import",
        confidence=0.9,
    )

    assert node.verification_status is GraphVerificationStatus.CANDIDATE
    assert edge.verification_status is GraphVerificationStatus.CANDIDATE
    assert edge.confidence == 0.9


def test_graph_query_is_bounded_and_temporal() -> None:
    query = GraphQuery(
        source_node_id="tenant-1",
        target_node_id="plot-1",
        as_of_date=date(2023, 1, 1),
        max_hops=8,
        limit=100,
    )

    assert query.max_hops == 8
    assert query.limit == 100
    with pytest.raises(ValidationError):
        GraphQuery(source_node_id="tenant-1", max_hops=9)


def test_graph_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GraphQuery(source_node_id="tenant-1", raw_sql="SELECT 1")
