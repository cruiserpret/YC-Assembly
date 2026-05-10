"""Phase 6.75 — embedding provider abstraction.

Three implementations:
  - MockEmbeddingProvider: deterministic per-text vector. Tests + offline runs.
  - NoEmbeddingProvider: returns None for every call. Forces BM25 fallback.
  - OpenAIEmbeddingProvider: text-embedding-3-small (1536 dim). Used when
    OPENAI_API_KEY is set.

Every embedding call MUST flow through `cost_guarded_embed` (defined in
`assembly.llm.guarded_chat`). The AST drift scan extends to forbid direct
`.embed(...)` calls outside this package and the guarded helper.
"""
from __future__ import annotations

from assembly.embeddings.mock import MockEmbeddingProvider
from assembly.embeddings.none import NoEmbeddingProvider
from assembly.embeddings.openai_emb import OpenAIEmbeddingProvider
from assembly.embeddings.provider import EmbeddingProvider, EMBEDDING_DIM

__all__ = [
    "EmbeddingProvider",
    "EMBEDDING_DIM",
    "MockEmbeddingProvider",
    "NoEmbeddingProvider",
    "OpenAIEmbeddingProvider",
]
