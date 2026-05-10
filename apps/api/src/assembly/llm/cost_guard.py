"""Cost-cap enforcement with Postgres row-lock concurrency protection (C5).

Two concurrent LLM calls inside the same simulation could both pass a naive
`SUM(cost_usd)` check and both succeed, silently exceeding the hard cap. The
critic mandated a row lock to serialize cost-cap checks per simulation.

`with_cost_guard()` is the single entry point every LLM caller must go
through. It:

  1. Opens a transaction
  2. Acquires `SELECT ... FOR UPDATE` on `simulations.id` (the row lock)
  3. Computes `SUM(cost_usd)` for prior calls in this simulation
  4. If `total + estimated_cost > hard_cap`, raises `CostCapExceeded`
  5. Otherwise, runs the actual LLM call (lock held — concurrent callers wait)
  6. Logs the result to `llm_call_log` (success or failure)
  7. Commits, releasing the lock

The lock blocks `SELECT FOR UPDATE` and `UPDATE` on the same simulation row,
but plain `SELECT` reads (status polls from the API) are unaffected by MVCC.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.cost_log import log_llm_call
from assembly.llm.errors import CostCapExceeded
from assembly.llm.pricing import estimate_cost_usd
from assembly.llm.provider import LLMResponse
from assembly.models.llm_log import LLMCallLog
from assembly.models.simulation import Simulation

logger = logging.getLogger(__name__)


async def with_cost_guard(
    sessionmaker: async_sessionmaker[Any],
    *,
    simulation_id: UUID,
    stage: str,
    provider: str,
    model: str,
    estimated_prompt_tokens: int,
    estimated_completion_tokens: int,
    hard_cap_usd: Decimal,
    actual_call: Callable[[], Awaitable[LLMResponse]],
) -> LLMResponse:
    """Run `actual_call` under the cost guard. The lock is held for the full
    duration of the LLM call; concurrent calls in the same simulation wait."""
    estimated = estimate_cost_usd(
        model=model,
        prompt_tokens=estimated_prompt_tokens,
        completion_tokens=estimated_completion_tokens,
    )

    async with sessionmaker() as session:
        async with session.begin():
            # 1. Lock the simulation row.
            locked_row = await session.execute(
                select(Simulation.id)
                .where(Simulation.id == simulation_id)
                .with_for_update()
            )
            if locked_row.scalar_one_or_none() is None:
                raise ValueError(f"simulation {simulation_id} does not exist")

            # 2. Sum prior call cost.
            total_so_far = (
                await session.execute(
                    select(func.coalesce(func.sum(LLMCallLog.cost_usd), Decimal(0)))
                    .where(LLMCallLog.simulation_id == simulation_id)
                )
            ).scalar_one() or Decimal(0)
            total_so_far = Decimal(total_so_far)

            # 3. Cap check.
            projected = total_so_far + estimated
            if projected > hard_cap_usd:
                logger.warning(
                    "cost_guard.refuse simulation=%s stage=%s spent=%.4f estimated=%.4f cap=%.4f",
                    simulation_id, stage, float(total_so_far),
                    float(estimated), float(hard_cap_usd),
                )
                raise CostCapExceeded(
                    simulation_id=str(simulation_id),
                    total_so_far=float(total_so_far),
                    estimated_next=float(estimated),
                    hard_cap=float(hard_cap_usd),
                )

            # 4. Make the call (lock held).
            t0 = perf_counter()
            try:
                response = await actual_call()
                latency_ms = int((perf_counter() - t0) * 1000)
                actual_cost = estimate_cost_usd(
                    model=response.model,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                )
                # 5a. Log success.
                await log_llm_call(
                    session,
                    simulation_id=simulation_id,
                    stage=stage,
                    provider=provider,
                    model=response.model,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    latency_ms=response.latency_ms or latency_ms,
                    cost_usd=actual_cost,
                    success=True,
                    prompt_snapshot=response.prompt_snapshot,
                )
                return response
            except Exception as exc:
                # 5b. Log failure (best-effort).
                latency_ms = int((perf_counter() - t0) * 1000)
                await log_llm_call(
                    session,
                    simulation_id=simulation_id,
                    stage=stage,
                    provider=provider,
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=latency_ms,
                    cost_usd=Decimal(0),
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
