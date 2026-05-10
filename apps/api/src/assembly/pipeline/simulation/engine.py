"""Phase 6 — Top-level simulation engine.

`run_simulation(...)` runs all 7 rounds for a single simulation, threading
per-agent `BuyerStateSnapshot`s between them. Each round commits its own
transaction so partial progress is visible to status polls; if a round
raises, the simulation is marked `failed` with the error captured.

This module does NOT call the LLM directly — every LLM call inside the
rounds package goes through `call_llm_for_simulation`, which is the
single blessed entry point per the O1 standing entry condition.

Phase 6 produces RAW simulation state. It does NOT write
`simulation_outputs` (that's Phase 7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assembly.llm.errors import CostCapExceeded, LLMRepairExhausted
from assembly.llm.provider import LLMProvider
from assembly.models.round import (
    AgentResponse as AgentResponseORM,
)
from assembly.models.round import (
    SimulationRound as SimulationRoundORM,
)
from assembly.models.simulation import Simulation
from assembly.pipeline.evidence_builder import EvidenceBuildResult
from assembly.pipeline.progress import update_status_and_progress
from assembly.pipeline.simulation.persistence import write_round_results
from assembly.pipeline.simulation.rounds import (
    baseline,
    competitor_comparison,
    final_stance,
    first_exposure,
    objection_formation,
    proof_exposure,
    social_influence,
)
from assembly.pipeline.simulation.state import (
    ROUND_NUMBERS_TO_TYPE,
    BuyerStateSnapshot,
    RoundContext,
    RoundResult,
)
from assembly.schemas.brief import SimulationBriefIn
from assembly.schemas.product_intelligence import ProductIntelligenceObject
from assembly.schemas.society import SocietyBuildResult

logger = logging.getLogger(__name__)


# Round-number → module mapping. Order is fixed.
_ROUND_MODULES = (
    (1, "baseline", baseline),
    (2, "first_exposure", first_exposure),
    (3, "objection_formation", objection_formation),
    (4, "competitor_comparison", competitor_comparison),
    (5, "proof_exposure", proof_exposure),
    (6, "social_influence", social_influence),
    (7, "final_stance", final_stance),
)


@dataclass
class SimulationEngineResult:
    """Top-level engine output. NOT persisted — `simulation_outputs` is Phase 7.
    The engine returns this so a synchronous caller (FastAPI route) can serialize
    a quick summary, and so tests can assert against the in-memory state."""

    simulation_id: UUID
    rounds: list[RoundResult]
    final_snapshots: dict[UUID, BuyerStateSnapshot]
    total_cost_usd: float
    total_latency_ms: int


async def run_simulation(
    *,
    simulation_id: UUID,
    brief: SimulationBriefIn,
    pio: ProductIntelligenceObject,
    evidence: EvidenceBuildResult,
    society: SocietyBuildResult,
    provider: LLMProvider,
    sessionmaker: async_sessionmaker,
    seed: int = 0xA55E_AB10,  # default: deterministic seed for peer sampling
) -> SimulationEngineResult:
    """Run all 7 rounds end-to-end.

    Phase 6.5: status transitions ('simulating' → 'simulation_completed') are
    owned by the orchestrator (`pipeline.orchestration.run_full_pipeline`).
    The engine only writes started_at, completed_at, total_cost_usd,
    total_latency_ms, and per-round progress.

    Idempotent / resumable: if `simulation_rounds` rows already exist for
    `simulation_id`, the engine skips them and resumes at round N+1, loading
    snapshots from the latest committed round's `agent_responses.state_after`.
    """
    started_at = datetime.now(UTC)

    # Resume detection — count existing simulation_rounds.
    resume_from_round, resumed_snapshots = await _detect_resume_point(
        sessionmaker, simulation_id, society.agents
    )
    snapshots: dict[UUID, BuyerStateSnapshot] = resumed_snapshots

    # Set started_at if this is the first run; preserve on resume.
    async with sessionmaker() as session:
        async with session.begin():
            sim = await session.get(Simulation, simulation_id)
            if sim is not None and sim.started_at is None:
                sim.started_at = started_at

    rounds_completed: list[RoundResult] = []

    try:
        for round_number, round_type, module in _ROUND_MODULES:
            if round_number < resume_from_round:
                logger.info(
                    "engine.round.skip sim=%s round=%d (resumed from %d)",
                    simulation_id, round_number, resume_from_round,
                )
                continue

            ctx = RoundContext(
                simulation_id=simulation_id,
                round_number=round_number,
                round_type=round_type,
                society=list(society.agents),
                edges=list(society.edges),
                pio=pio,
                evidence=list(evidence.items),
                brief=brief,
                snapshots=snapshots,
                seed=seed,
            )
            logger.info(
                "engine.round.start sim=%s round=%d type=%s agents=%d",
                simulation_id, round_number, round_type, len(society.agents),
            )

            # Update progress to reflect the round we're entering.
            await update_status_and_progress(
                sessionmaker,
                simulation_id=simulation_id,
                progress_changes={
                    "current_round": round_type,
                    "round_index": round_number,
                    "total_rounds": 7,
                    "agents_completed": 0,
                    "agents_total": len(society.agents),
                },
            )

            round_result: RoundResult = await module.run_round(
                ctx, provider=provider, sessionmaker=sessionmaker
            )

            # Persist this round in its own transaction.
            async with sessionmaker() as session:
                async with session.begin():
                    await write_round_results(session, round_result=round_result)

            # Update progress: agents_completed now matches agents_total.
            await update_status_and_progress(
                sessionmaker,
                simulation_id=simulation_id,
                progress_changes={
                    "agents_completed": len(round_result.agent_responses),
                },
            )

            rounds_completed.append(round_result)
            snapshots = round_result.new_snapshots
            logger.info(
                "engine.round.complete sim=%s round=%d responses=%d turns=%d",
                simulation_id, round_number,
                len(round_result.agent_responses), len(round_result.debate_turns),
            )

        # Compute total cost + latency from llm_call_log.
        total_cost, total_latency = await _read_simulation_cost_and_latency(
            sessionmaker, simulation_id
        )

        # Operational metadata only — orchestrator advances the status.
        async with sessionmaker() as session:
            async with session.begin():
                sim = await session.get(Simulation, simulation_id)
                if sim is not None:
                    sim.completed_at = datetime.now(UTC)
                    sim.total_cost_usd = total_cost  # type: ignore[assignment]
                    sim.total_latency_ms = total_latency

        return SimulationEngineResult(
            simulation_id=simulation_id,
            rounds=rounds_completed,
            final_snapshots=snapshots,
            total_cost_usd=total_cost,
            total_latency_ms=total_latency,
        )

    except (CostCapExceeded, LLMRepairExhausted):
        # Phase 6.5: orchestrator owns status='failed' + failed_stage. Just
        # re-raise so the orchestrator can attribute the failure correctly.
        raise
    except Exception:  # pragma: no cover — defensive
        raise


# ---------------------------------------------------------------------------
# Resume detection
# ---------------------------------------------------------------------------


async def _detect_resume_point(
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    society_agents: list,
) -> tuple[int, dict[UUID, BuyerStateSnapshot]]:
    """Look at how many simulation_rounds rows already exist for this
    simulation. If 0, run from round 1. If N (1..6), resume at round N+1
    by reconstructing snapshots from the latest committed round's
    agent_responses.

    Returns (resume_from_round, snapshots).
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(func.count(SimulationRoundORM.id))
            .where(SimulationRoundORM.simulation_id == simulation_id)
        )
        completed_rounds = int(result.scalar_one() or 0)

        if completed_rounds == 0:
            return 1, {}

        # Load the latest round's agent_responses to reconstruct snapshots.
        latest_round = (
            await session.execute(
                select(SimulationRoundORM)
                .where(SimulationRoundORM.simulation_id == simulation_id)
                .order_by(SimulationRoundORM.round_number.desc())
                .limit(1)
            )
        ).scalar_one()

        responses = (
            await session.execute(
                select(AgentResponseORM)
                .where(AgentResponseORM.round_id == latest_round.id)
            )
        ).scalars().all()

        agents_by_id = {a.agent_id: a for a in society_agents}
        snapshots: dict[UUID, BuyerStateSnapshot] = {}
        for r in responses:
            snapshots[r.agent_id] = BuyerStateSnapshot(
                agent_id=r.agent_id,
                current_stance=r.stance,
                state_after=r.state_after if isinstance(r.state_after, dict) else {},
                accumulated_objections=r.objections or [],
                accumulated_persuasion_drivers=r.persuasion_drivers or [],
                last_reasoning=r.reasoning,
                shift_history=[r.shift_from_previous] if r.shift_from_previous else [],
            )

        # Defensive: any agent without a response yet (shouldn't happen if the
        # round wrote atomically) starts fresh.
        for agent in society_agents:
            if agent.agent_id not in snapshots:
                snapshots[agent.agent_id] = BuyerStateSnapshot.initial(agent)

    if completed_rounds >= 7:
        return 8, snapshots  # all rounds done — caller skips the loop
    return completed_rounds + 1, snapshots


async def _read_simulation_cost_and_latency(
    sessionmaker: async_sessionmaker, simulation_id: UUID
) -> tuple[float, int]:
    from sqlalchemy import func

    from assembly.models.llm_log import LLMCallLog

    async with sessionmaker() as session:
        cost = (
            await session.execute(
                select(func.coalesce(func.sum(LLMCallLog.cost_usd), 0))
                .where(LLMCallLog.simulation_id == simulation_id)
            )
        ).scalar_one() or 0
        latency = (
            await session.execute(
                select(func.coalesce(func.sum(LLMCallLog.latency_ms), 0))
                .where(LLMCallLog.simulation_id == simulation_id)
            )
        ).scalar_one() or 0
    return float(cost), int(latency)


__all__ = ["SimulationEngineResult", "run_simulation"]
