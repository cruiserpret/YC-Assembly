"""Phase 8.2K — single LLM seam for the micro-simulation harness.

Every LLM call goes through `cost_guarded_chat` (Phase 6.6). Stage
labels start with `micro_` so cost-cap audits can attribute spend
to the harness. Drift test asserts no `provider.chat(...)` outside
this module.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider, LLMResponse


# Stage labels — every label MUST start with "micro_" so the drift
# test + the cost-attribution audit can match on the prefix.
STAGE_BASELINE = "micro_baseline"  # never used (baseline is deterministic)
STAGE_FIRST_EXPOSURE = "micro_first_exposure"
STAGE_OBJECTION = "micro_objection"
STAGE_FINAL_STANCE = "micro_final_stance"
STAGE_DEBATE = "micro_debate_turn"


async def micro_llm_call(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    stage: str,
    messages: Sequence[LLMMessage],
    provider: LLMProvider,
    model: str | None = None,
    hard_cap_usd: Decimal | None = None,
) -> LLMResponse:
    """The ONE LLM call site for the entire micro_simulation package.

    Anything else trying to call provider.chat / structured_output /
    embed in this package is a drift-test failure.
    """
    if not stage.startswith("micro_"):
        raise ValueError(
            f"micro_llm_call refuses non-micro stage label: {stage!r}"
        )
    return await cost_guarded_chat(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        stage=stage,
        messages=list(messages),
        provider=provider,
        model=model,
        hard_cap_usd=hard_cap_usd,
        max_tokens=900,
        temperature=0.3,
        capture_prompt_snapshot=True,
        estimated_prompt_tokens=2500,
        estimated_completion_tokens=600,
    )
