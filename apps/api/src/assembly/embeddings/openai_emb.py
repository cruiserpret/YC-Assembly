"""OpenAI embedding provider — text-embedding-3-small (1536 dim).

Used at runtime when `OPENAI_API_KEY` is set. The Phase 6.75 graph builder
chooses between this, `MockEmbeddingProvider`, and `NoEmbeddingProvider`
based on environment configuration.

The openai SDK is imported lazily so the rest of the codebase remains
runnable without it.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import ClassVar

from assembly.config import get_settings
from assembly.embeddings.provider import EMBEDDING_DIM, EmbeddingProvider
from assembly.llm.errors import LLMProviderError

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name: ClassVar[str] = "text-embedding-3-small"
    dim: ClassVar[int] = EMBEDDING_DIM

    def __init__(self, *, api_key: str | None = None) -> None:
        try:
            import openai  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise LLMProviderError(
                "openai SDK not installed. Run `uv sync` with the openai "
                "dependency, or use MockEmbeddingProvider in tests."
            ) from e

        key = api_key or get_settings().openai_api_key
        if not key:
            raise LLMProviderError("OPENAI_API_KEY not configured")

        from openai import AsyncOpenAI  # type: ignore[import-not-found]
        self._client = AsyncOpenAI(api_key=key)

    async def embed(self, texts: Sequence[str]) -> list[list[float] | None]:
        if not texts:
            return []
        # OpenAI API rejects empty strings; replace with a single space and
        # let the caller treat any result as opaque. Real users should pre-
        # filter empties, but we don't crash on the edge case.
        cleaned = [t if t.strip() else " " for t in texts]
        response = await self._client.embeddings.create(
            model=self.name,
            input=cleaned,
        )
        # Order is preserved by the API.
        return [list(item.embedding) for item in response.data]
