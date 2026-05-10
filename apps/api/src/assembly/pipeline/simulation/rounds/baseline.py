"""Round 1 — Baseline (pre-exposure)."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.provider import LLMProvider
from assembly.pipeline.simulation.call_llm import call_llm_for_simulation
from assembly.pipeline.simulation.rounds._base import (
    build_messages,
    make_round_result,
)
from assembly.pipeline.simulation.state import (
    BuyerStateSnapshot,
    RoundContext,
    RoundResult,
)
from assembly.schemas.round import AgentRoundResponse, DebateTurnOut


async def run_round(
    ctx: RoundContext,
    *,
    provider: LLMProvider,
    sessionmaker: async_sessionmaker,
) -> RoundResult:
    started_at = datetime.now(UTC)
    responses: list[AgentRoundResponse] = []
    new_snapshots: dict[UUID, BuyerStateSnapshot] = {}

    for agent in ctx.society:
        snapshot = ctx.snapshots.get(agent.agent_id)  # None for round 1
        messages = build_messages(
            round_type=ctx.round_type,
            agent=agent,
            snapshot=snapshot,
            ctx=ctx,
        )
        parsed, _response = await call_llm_for_simulation(
            sessionmaker=sessionmaker,
            simulation_id=ctx.simulation_id,
            stage=f"round_{ctx.round_type}",
            schema=AgentRoundResponse,
            messages=messages,
            provider=provider,
        )
        # Force the round-1 invariants the prompt asks for, defensively:
        # baseline stance is fixed and there are no shifts/objections/drivers.
        # We don't override the LLM if it complied; we only normalize.
        if str(parsed.stance) != "curious_hesitant":
            parsed.stance = "curious_hesitant"  # type: ignore[assignment]
        parsed.shift_from_previous = None
        if parsed.objections:
            parsed.objections = []
        if parsed.persuasion_drivers:
            parsed.persuasion_drivers = []

        # Round 1 starts the snapshot chain.
        starting = BuyerStateSnapshot.initial(agent)
        new_snapshots[agent.agent_id] = starting.updated_for_response(parsed)
        responses.append(parsed)

    debate_turns: list[DebateTurnOut] = []
    return make_round_result(
        ctx=ctx,
        responses=responses,
        debate_turns=debate_turns,
        new_snapshots=new_snapshots,
        started_at=started_at,
    )
