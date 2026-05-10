"""Deterministic mock embedding provider for tests and offline runs.

Same input text → identical vector across calls AND across processes. The
projection is hash-based (sha256 → 1536 floats in [-1, 1]). Two distinct
texts produce distinct vectors with overwhelmingly high probability; the
goal isn't semantic similarity but determinism + dimensional correctness.
"""
from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import ClassVar

from assembly.embeddings.provider import EMBEDDING_DIM, EmbeddingProvider


class MockEmbeddingProvider(EmbeddingProvider):
    name: ClassVar[str] = "mock-1536"
    dim: ClassVar[int] = EMBEDDING_DIM

    async def embed(self, texts: Sequence[str]) -> list[list[float] | None]:
        out: list[list[float] | None] = []
        for text in texts:
            if not text:
                out.append(None)
                continue
            out.append(_hash_to_vector(text, self.dim))
        return out


def _hash_to_vector(text: str, dim: int) -> list[float]:
    """sha256 over `text`, interpreted as little-endian uint32 chunks
    normalized to [-1, 1]. We loop the digest as needed to reach `dim`
    floats, varying the salt so the chunks aren't repeated."""
    out: list[float] = []
    salt = 0
    while len(out) < dim:
        h = hashlib.sha256(f"{salt}|{text}".encode("utf-8")).digest()
        # 32 bytes → 8 uint32s → 8 floats per loop
        for i in range(0, len(h), 4):
            (val,) = struct.unpack_from("<I", h, i)
            out.append((val / 0xFFFFFFFF) * 2.0 - 1.0)
            if len(out) >= dim:
                break
        salt += 1
    return out
