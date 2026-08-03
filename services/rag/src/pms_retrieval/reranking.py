"""Bounded, local-only BGE reranking."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pms_common.settings import Settings

from pms_retrieval.models import RankedEvidence


class RerankingError(RuntimeError):
    """Raised when reranking cannot complete without weakening the gate."""


class RerankerUnavailable(RerankingError):
    """Raised when the approved reranker is not present locally."""


@dataclass(frozen=True, slots=True)
class RerankerStatus:
    available: bool
    model: str
    revision: str | None
    local_path: str | None
    provider_version: str | None
    detail: str


class EvidenceReranker(Protocol):
    model: str
    revision: str

    def rerank(
        self,
        query: str,
        candidates: tuple[RankedEvidence, ...],
        limit: int,
    ) -> tuple[RankedEvidence, ...]: ...


def check_reranker_model(settings: Settings) -> RerankerStatus:
    """Check the configured cache only. Never download during validation."""

    try:
        hub = importlib.import_module("huggingface_hub")
    except ImportError:
        return RerankerStatus(
            False,
            settings.reranker_model,
            settings.reranker_model_revision,
            None,
            None,
            "huggingface_hub is not installed",
        )
    try:
        path = Path(
            hub.snapshot_download(
                repo_id=settings.reranker_model,
                revision=settings.reranker_model_revision or None,
                cache_dir=settings.reranker_cache_dir,
                local_files_only=True,
            )
        )
    except (OSError, ValueError) as error:
        return RerankerStatus(
            False,
            settings.reranker_model,
            settings.reranker_model_revision,
            None,
            _distribution_version("FlagEmbedding"),
            f"model is not present in the configured local cache: {type(error).__name__}",
        )
    return RerankerStatus(
        True,
        settings.reranker_model,
        settings.reranker_model_revision or path.name,
        str(path),
        _distribution_version("FlagEmbedding"),
        "local model snapshot is available",
    )


class BgeReranker:
    """Use the approved multilingual reranker from a verified local snapshot."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        status = check_reranker_model(settings)
        if not status.available or status.local_path is None or status.revision is None:
            raise RerankerUnavailable(status.detail)
        if status.provider_version is None:
            raise RerankerUnavailable("FlagEmbedding is not installed")
        self.model = status.model
        self.revision = status.revision
        self._local_path = status.local_path
        self._instance: Any | None = None

    def _load(self) -> Any:
        if self._instance is not None:
            return self._instance
        try:
            module = importlib.import_module("FlagEmbedding")
        except ImportError as error:
            raise RerankerUnavailable("FlagEmbedding is not installed") from error
        reranker_type = getattr(module, "FlagReranker", None)
        if reranker_type is None:
            raise RerankerUnavailable("FlagReranker is unavailable")
        try:
            self._instance = reranker_type(
                self._local_path,
                use_fp16=self._settings.reranker_device == "cuda",
                trust_remote_code=False,
                devices=self._settings.reranker_device,
                batch_size=self._settings.reranker_batch_size,
                max_length=self._settings.reranker_max_sequence_length,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise RerankerUnavailable("approved reranker could not be loaded") from error
        return self._instance

    def rerank(
        self,
        query: str,
        candidates: tuple[RankedEvidence, ...],
        limit: int,
    ) -> tuple[RankedEvidence, ...]:
        bounded = candidates[: self._settings.rerank_input_top_k]
        if not bounded:
            return ()
        if limit < 1 or limit > self._settings.rerank_output_top_k:
            raise ValueError("rerank limit is outside configured bounds")
        pairs = [(query, item.hit.text) for item in bounded]
        try:
            raw = self._load().compute_score(
                pairs,
                batch_size=self._settings.reranker_batch_size,
                max_length=self._settings.reranker_max_sequence_length,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise RerankingError("bounded reranking failed") from error
        scores = [raw] if isinstance(raw, int | float) else list(raw)
        if len(scores) != len(bounded):
            raise RerankingError("reranker score count does not match candidates")
        reranked = tuple(
            item.model_copy(update={"rerank_score": float(score)})
            for item, score in zip(bounded, scores, strict=True)
        )
        return tuple(
            sorted(
                reranked,
                key=lambda item: (
                    -(item.rerank_score if item.rerank_score is not None else float("-inf")),
                    -item.rrf_score,
                    item.hit.chunk_id,
                ),
            )[:limit]
        )


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
