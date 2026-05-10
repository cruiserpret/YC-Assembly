"""No-op embedding provider — returns None for every call.

Used when no embedding API key is configured. Retrieval falls back to BM25
over evidence content. The graph still builds; only similarity-anchored
edges (`similar_to` for embedding-cosine ≥ threshold) are skipped.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from assembly.embeddings.provider import EmbeddingProvider


class NoEmbeddingProvider(EmbeddingProvider):
    name: ClassVar[str] = "none"

    async def embed(self, texts: Sequence[str]) -> list[list[float] | None]:
        return [None for _ in texts]
