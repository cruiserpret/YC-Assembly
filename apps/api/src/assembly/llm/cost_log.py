"""Persist one row per LLM call into `llm_call_log`.

This is the audit trail. Every call — successful or failed — should land here
so:
  - the cost dashboard can report total spend per simulation
  - the Phase 11 backtest harness can prove no post-cutoff content leaked
    into prompts (via `prompt_snapshot`)
  - operations can debug rate limits / parse failures by stage
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from assembly.models.llm_log import LLMCallLog


async def log_llm_call(
    session: AsyncSession,
    *,
    simulation_id: UUID | None,
    stage: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    cost_usd: Decimal,
    success: bool,
    error: str | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
) -> LLMCallLog:
    """Insert one llm_call_log row. Caller controls the transaction; this
    function only does session.add() + flush, not commit."""
    row = LLMCallLog(
        simulation_id=simulation_id,
        stage=stage,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        success=success,
        error=error,
        prompt_snapshot=prompt_snapshot,
    )
    session.add(row)
    await session.flush()
    return row
