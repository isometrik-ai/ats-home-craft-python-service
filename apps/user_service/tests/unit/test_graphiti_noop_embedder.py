"""Unit tests for the Graphiti null embedder."""

from __future__ import annotations

import pytest

from libs.shared_utils.graphiti_noop_embedder import NullEmbedder


@pytest.mark.asyncio
async def test_null_embedder_returns_zero_vectors() -> None:
    """Null embedder should not call OpenAI and return zero-filled vectors."""
    embedder = NullEmbedder()
    vector = await embedder.create("hello")
    assert len(vector) == embedder.config.embedding_dim
    assert all(value == 0.0 for value in vector)

    batch = await embedder.create_batch(["a", "b"])
    assert len(batch) == 2
    assert all(all(value == 0.0 for value in row) for row in batch)
