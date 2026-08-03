from __future__ import annotations

import math

import pytest
from pms_retrieval.embedding import EmbeddingError, validate_embedding_vector


def test_embedding_vector_is_exactly_1024_and_normalized() -> None:
    vector = validate_embedding_vector(
        [1.0] * 1024,
        dimension=1024,
        normalize=True,
    )

    assert len(vector) == 1024
    assert math.isclose(sum(value * value for value in vector), 1)


def test_embedding_dimension_mismatch_is_rejected() -> None:
    with pytest.raises(EmbeddingError, match="expected 1024, got 1023"):
        validate_embedding_vector(
            [1.0] * 1023,
            dimension=1024,
            normalize=True,
        )


def test_zero_and_nonfinite_vectors_are_rejected() -> None:
    with pytest.raises(EmbeddingError, match="zero vector"):
        validate_embedding_vector(
            [0.0] * 1024,
            dimension=1024,
            normalize=True,
        )
    with pytest.raises(EmbeddingError, match="non-finite"):
        validate_embedding_vector(
            [float("nan"), *([1.0] * 1023)],
            dimension=1024,
            normalize=True,
        )
