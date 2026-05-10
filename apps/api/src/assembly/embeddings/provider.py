"""Embedding provider ABC + shared types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar


# 1536 = text-embedding-3-small default. The pgvector column is sized to
# match. Mock and None providers also produce / accept vectors of this
# dimension so the column type is interchangeable across modes.
EMBEDDING_DIM: int = 1536


class EmbeddingProvider(ABC):
    """Abstract base. Concrete providers implement `embed(texts)` and report
    a `name` (used as `embedding_model` on persisted rows) and `dim`."""

    name: ClassVar[str] = "abstract"
    dim: ClassVar[int] = EMBEDDING_DIM

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float] | None]:
        """Return one vector per input text.

        Implementations may return `None` for an individual text (e.g. empty
        input). The caller is responsible for skipping None vectors when
        persisting.

        Length of the return list MUST equal `len(texts)`. Order MUST match.
        """
