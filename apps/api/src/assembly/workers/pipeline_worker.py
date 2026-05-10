"""arq job: run_pipeline(simulation_id).

Thin glue. Picks an LLMProvider per env config and forwards to
`pipeline.orchestration.run_full_pipeline`. The orchestrator handles all
status transitions, idempotency, failure attribution, and the cost guard
discipline.

NO direct `provider.chat(...)` / `provider.structured_output(...)` calls
in this file or anywhere in `workers/`. The static drift tripwire
enforces this.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from assembly.config import get_settings
from assembly.db import get_sessionmaker
from assembly.llm.errors import LLMProviderError
from assembly.llm.provider import LLMProvider
from assembly.pipeline.orchestration import PipelineResult, run_full_pipeline

logger = logging.getLogger(__name__)


def build_provider() -> LLMProvider:
    """Pick an LLMProvider per env config. Anthropic primary, OpenAI fallback.

    Returns a real provider — NOT MockProvider. Tests should patch this
    function or pass providers explicitly to `run_full_pipeline` directly.
    """
    settings = get_settings()

    if settings.llm_primary_provider == "anthropic" and settings.anthropic_api_key:
        from assembly.llm.anthropic import AnthropicProvider
        return AnthropicProvider()
    if settings.openai_api_key:
        from assembly.llm.openai import OpenAIProvider
        return OpenAIProvider()
    if settings.anthropic_api_key:
        from assembly.llm.anthropic import AnthropicProvider
        return AnthropicProvider()

    raise LLMProviderError(
        "no LLM provider configured — set ANTHROPIC_API_KEY or OPENAI_API_KEY"
    )


async def run_pipeline(ctx: dict, simulation_id: str) -> dict[str, Any]:
    """arq job entry point. `ctx` is arq's job context dict; we don't need it."""
    sim_uuid = UUID(simulation_id)
    sessionmaker = get_sessionmaker()
    provider = build_provider()

    logger.info(
        "worker.run_pipeline.start sim=%s provider=%s",
        sim_uuid, provider.name,
    )
    result: PipelineResult = await run_full_pipeline(
        simulation_id=sim_uuid,
        sessionmaker=sessionmaker,
        provider=provider,
    )
    logger.info(
        "worker.run_pipeline.complete sim=%s status=%s rounds=%d cost_usd=%.4f",
        sim_uuid, result.final_status, result.rounds_completed,
        result.total_cost_usd,
    )

    return {
        "simulation_id": str(result.simulation_id),
        "final_status": result.final_status,
        "rounds_completed": result.rounds_completed,
        "total_cost_usd": result.total_cost_usd,
    }


__all__ = ["build_provider", "run_pipeline"]
