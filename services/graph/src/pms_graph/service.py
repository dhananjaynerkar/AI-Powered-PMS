"""Deterministic GraphRAG orchestration over verified PostgreSQL paths."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from pms_graph.models import GraphAnswer, GraphPath, GraphQuery


class GraphRepository(Protocol):
    def traverse(self, query: GraphQuery) -> tuple[GraphPath, ...]: ...


class GraphRagService:
    """Return only ACL-visible, verified paths; no LLM creates graph facts."""

    def __init__(self, repository: GraphRepository) -> None:
        self._repository = repository

    def ask(self, query: GraphQuery) -> GraphAnswer:
        paths = self._repository.traverse(query)
        if not paths:
            return GraphAnswer(
                answer="No verified relationship path is available for this request.",
                graph_paths=(),
                confidence="LOW",
                warnings=(
                    "Unverified, inactive, out-of-date or unauthorized graph data was excluded.",
                ),
                review_required=True,
                correlation_id=str(uuid4()),
                generated_at=datetime.now(UTC),
            )
        return GraphAnswer(
            answer=f"Found {len(paths)} verified relationship path(s).",
            graph_paths=paths,
            confidence="HIGH",
            warnings=(
                "Graph paths do not replace exact SQL or deterministic rule calculations.",
            ),
            review_required=False,
            correlation_id=str(uuid4()),
            generated_at=datetime.now(UTC),
        )
