"""Aggregation package — Phase 7.

Phase 7 entry point: `run_aggregation`. When `ASSEMBLY_ENABLE_AGGREGATION=false`
this is a no-op (matches the Phase 6.5 stub semantics so callers don't
crash). When the flag is on, it dispatches to `service.run_aggregation_v7`.

The orchestrator calls `run_aggregation` AFTER `run_simulation` succeeds and
status is `simulation_completed`. It must not be called before raw state
exists.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from assembly.config import get_settings

logger = logging.getLogger(__name__)


async def run_aggregation(
    *,
    simulation_id: UUID,
    sessionmaker: Any,
    provider: Any,
) -> None:
    settings = get_settings()
    if not settings.enable_aggregation:
        logger.info(
            "aggregation.skipped simulation=%s reason=disabled_by_flag",
            simulation_id,
        )
        return None

    # Lazy import to avoid heavy dependency loading when the flag is off.
    from assembly.embeddings import (
        MockEmbeddingProvider,
        OpenAIEmbeddingProvider,
    )
    from assembly.llm.errors import LLMProviderError
    from assembly.pipeline.aggregation.service import run_aggregation_v7

    embedding_provider = None
    try:
        embedding_provider = OpenAIEmbeddingProvider()
    except LLMProviderError:
        embedding_provider = MockEmbeddingProvider()

    await run_aggregation_v7(
        simulation_id=simulation_id,
        sessionmaker=sessionmaker,
        provider=provider,
        embedding_provider=embedding_provider,
    )


__all__ = ["run_aggregation"]
