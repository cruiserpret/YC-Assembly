"""Round 3 — Objection formation."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.provider import LLMProvider
from assembly.pipeline.simulation.rounds._base import run_per_agent_round
from assembly.pipeline.simulation.state import RoundContext, RoundResult


async def run_round(
    ctx: RoundContext,
    *,
    provider: LLMProvider,
    sessionmaker: async_sessionmaker,
) -> RoundResult:
    return await run_per_agent_round(
        ctx, provider=provider, sessionmaker=sessionmaker
    )
