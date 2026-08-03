from datetime import date

from pms_graph.models import (
    GraphEdgeEvidence,
    GraphEdgeType,
    GraphPath,
    GraphQuery,
)
from pms_graph.service import GraphRagService


class FakeRepository:
    def __init__(self, paths: tuple[GraphPath, ...]) -> None:
        self.paths = paths
        self.received: GraphQuery | None = None

    def traverse(self, query: GraphQuery) -> tuple[GraphPath, ...]:
        self.received = query
        return self.paths


def _path() -> GraphPath:
    return GraphPath(
        node_ids=("tenant-1", "tenancy-1"),
        edge_ids=("edge-1",),
        depth=1,
        evidence=(
            GraphEdgeEvidence(
                edge_id="edge-1",
                edge_type=GraphEdgeType.TENANT_HAS_TENANCY,
                from_node_id="tenant-1",
                to_node_id="tenancy-1",
                source_schema="pms_extract_2010_2023",
                source_table="bridge_applicant_tenancy",
                source_record_id="7",
                valid_from=date(2010, 1, 1),
            ),
        ),
    )


def test_graph_service_returns_only_repository_paths() -> None:
    repository = FakeRepository((_path(),))
    service = GraphRagService(repository)
    answer = service.ask(GraphQuery(source_node_id="tenant-1"))

    assert answer.review_required is False
    assert answer.graph_paths == (_path(),)
    assert answer.confidence == "HIGH"
    assert repository.received == GraphQuery(source_node_id="tenant-1")


def test_graph_service_fails_closed_without_a_verified_path() -> None:
    answer = GraphRagService(FakeRepository(())).ask(
        GraphQuery(source_node_id="tenant-1")
    )

    assert answer.review_required is True
    assert answer.graph_paths == ()
    assert "Unverified" in answer.warnings[0]
