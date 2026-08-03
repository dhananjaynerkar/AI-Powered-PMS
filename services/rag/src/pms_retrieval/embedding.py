"""Lazy, local BGE-M3 tokenizer and dense embedding adapter."""

from __future__ import annotations

import importlib
import importlib.metadata
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pms_common.settings import Settings

from pms_retrieval.chunking import Tokenizer


class EmbeddingError(RuntimeError):
    """Raised when local model validation or embedding fails safely."""


class EmbeddingModelUnavailable(EmbeddingError):
    """Raised when BGE-M3 is not already available in the local cache."""


@dataclass(frozen=True, slots=True)
class EmbeddingModelStatus:
    available: bool
    model: str
    revision: str | None
    local_path: str | None
    dimension: int
    provider_version: str | None
    detail: str


def check_bge_m3_model(settings: Settings) -> EmbeddingModelStatus:
    """Check local files only; this command never downloads a model."""

    try:
        hub = importlib.import_module("huggingface_hub")
    except ImportError:
        return EmbeddingModelStatus(
            available=False,
            model=settings.embedding_model,
            revision=None,
            local_path=None,
            dimension=settings.embedding_dimension,
            provider_version=None,
            detail="huggingface_hub is not installed",
        )
    try:
        path = Path(
            hub.snapshot_download(
                repo_id=settings.embedding_model,
                revision=settings.embedding_model_revision or None,
                cache_dir=settings.embedding_cache_dir,
                local_files_only=True,
            )
        )
    except (OSError, ValueError) as error:
        return EmbeddingModelStatus(
            available=False,
            model=settings.embedding_model,
            revision=settings.embedding_model_revision or None,
            local_path=None,
            dimension=settings.embedding_dimension,
            provider_version=_distribution_version("FlagEmbedding"),
            detail=f"model is not present in the configured local cache: {type(error).__name__}",
        )
    revision = settings.embedding_model_revision or path.name
    return EmbeddingModelStatus(
        available=True,
        model=settings.embedding_model,
        revision=revision,
        local_path=str(path),
        dimension=settings.embedding_dimension,
        provider_version=_distribution_version("FlagEmbedding"),
        detail="local model snapshot is available",
    )


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


class BgeM3Tokenizer(Tokenizer):
    """Use the exact local BGE-M3 tokenizer for chunk limits."""

    def __init__(self, settings: Settings) -> None:
        status = check_bge_m3_model(settings)
        if not status.available or status.local_path is None:
            raise EmbeddingModelUnavailable(status.detail)
        try:
            transformers = importlib.import_module("transformers")
        except ImportError as error:
            raise EmbeddingModelUnavailable("transformers is not installed") from error
        self._tokenizer: Any = transformers.AutoTokenizer.from_pretrained(
            status.local_path,
            local_files_only=True,
            trust_remote_code=False,
        )

    def count(self, text: str) -> int:
        encoded = self._tokenizer.encode(text, add_special_tokens=True)
        return len(encoded)

    def split(self, text: str, maximum: int, overlap: int) -> tuple[str, ...]:
        if maximum <= 0 or overlap < 0 or overlap >= maximum:
            raise ValueError("invalid tokenizer split bounds")
        token_ids: list[int] = list(
            self._tokenizer.encode(text, add_special_tokens=False)
        )
        if not token_ids:
            return ()
        step = maximum - overlap
        return tuple(
            str(
                self._tokenizer.decode(
                    token_ids[start : start + maximum],
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )
            ).strip()
            for start in range(0, len(token_ids), step)
            if token_ids[start : start + maximum]
        )


class BgeM3EmbeddingAdapter:
    """Generate normalized 1024-dimensional dense vectors with local BGE-M3."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        status = check_bge_m3_model(settings)
        if not status.available or status.local_path is None or status.revision is None:
            raise EmbeddingModelUnavailable(status.detail)
        if status.provider_version is None:
            raise EmbeddingModelUnavailable("FlagEmbedding is not installed")
        self.model = status.model
        self.revision = status.revision
        self.embedding_version = status.provider_version
        self.dimension = status.dimension
        self._local_path = status.local_path
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            module = importlib.import_module("FlagEmbedding")
        except ImportError as error:
            raise EmbeddingModelUnavailable("FlagEmbedding is not installed") from error
        model_type = getattr(module, "BGEM3FlagModel", None)
        if model_type is None:
            raise EmbeddingModelUnavailable("BGEM3FlagModel is unavailable")
        self._model = model_type(
            self._local_path,
            use_fp16=self._settings.embedding_device == "cuda",
        )
        return self._model

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        last_error: Exception | None = None
        for _attempt in range(self._settings.embedding_max_retries + 1):
            try:
                result = self._load().encode(
                    list(texts),
                    batch_size=self._settings.embedding_batch_size,
                    max_length=self._settings.embedding_max_sequence_length,
                )
                if not isinstance(result, dict) or "dense_vecs" not in result:
                    raise EmbeddingError("BGE-M3 returned no dense vectors")
                vectors = tuple(
                    self._validated_vector(vector)
                    for vector in result["dense_vecs"]
                )
                if len(vectors) != len(texts):
                    raise EmbeddingError("BGE-M3 vector count does not match input")
                return vectors
            except EmbeddingError:
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                last_error = error
                self._model = None
        raise EmbeddingError("BGE-M3 embedding failed after bounded retries") from last_error

    def _validated_vector(self, value: object) -> tuple[float, ...]:
        return validate_embedding_vector(
            value,
            dimension=self.dimension,
            normalize=self._settings.embedding_normalize,
        )


def validate_embedding_vector(
    value: object,
    *,
    dimension: int,
    normalize: bool,
) -> tuple[float, ...]:
    raw = value.tolist() if hasattr(value, "tolist") else value
    if not isinstance(raw, list | tuple):
        raise EmbeddingError("BGE-M3 returned a non-vector value")
    vector = tuple(float(item) for item in raw)
    if len(vector) != dimension:
        raise EmbeddingError(
            f"BGE-M3 dimension mismatch: expected {dimension}, got {len(vector)}"
        )
    if not all(math.isfinite(item) for item in vector):
        raise EmbeddingError("BGE-M3 returned a non-finite vector")
    if not normalize:
        return vector
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0:
        raise EmbeddingError("BGE-M3 returned a zero vector")
    return tuple(item / norm for item in vector)
