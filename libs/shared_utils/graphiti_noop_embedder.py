"""No-op embedder for Graphiti when CRM sync does not use vector search."""

from __future__ import annotations

from collections.abc import Iterable

from graphiti_core.embedder.client import EmbedderClient, EmbedderConfig


class NullEmbedder(EmbedderClient):
    """Return zero vectors without calling an external embedding API."""

    def __init__(self, config: EmbedderConfig | None = None) -> None:
        self.config = config or EmbedderConfig()

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        """create a zero vector for the input data without calling an external embedding API."""
        del input_data
        return [0.0] * self.config.embedding_dim

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """create a zero vector for the input data list without calling an external embedding API"""
        zero = [0.0] * self.config.embedding_dim
        return [zero[:] for _ in input_data_list]
