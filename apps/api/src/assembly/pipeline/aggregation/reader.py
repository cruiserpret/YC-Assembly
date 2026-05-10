"""Phase 7 — read-only loader for the report input bundle.

`load_report_inputs` produces a single `ReportInputBundle` consumed by the
mechanical computations and by the synthesis prompts. The loader is the
ONLY surface that touches `simulation_rounds`, `agent_responses`, and
`debate_turns` — every downstream module passes the bundle through.

All evidence access goes through `EvidenceGraphService` (Phase 6.75 graph
helpers). The reader does not query `evidence_items`, `evidence_edges`, or
`claims` tables directly.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.embeddings.provider import EmbeddingProvider
from assembly.models.agent import Agent as AgentORM
from assembly.models.agent import AgentEdge as AgentEdgeORM
from assembly.models.round import AgentResponse as AgentResponseORM
from assembly.models.round import DebateTurn as DebateTurnORM
from assembly.models.round import SimulationRound as SimulationRoundORM
from assembly.models.simulation import Simulation
from assembly.pipeline.evidence_graph import EvidenceGraphService
from assembly.pipeline.evidence_graph.service import (
    ClaimTraceability,
    EvidenceBundle,
    MissingEvidenceSummary,
)
from assembly.schemas.brief import SimulationBriefIn
from assembly.schemas.product_intelligence import ProductIntelligenceObject


@dataclass
class ReportInputBundle:
    simulation: Simulation
    brief: SimulationBriefIn
    pio: ProductIntelligenceObject
    society: list[AgentORM]
    edges: list[AgentEdgeORM]
    rounds: list[SimulationRoundORM]
    agent_responses: list[AgentResponseORM]
    debate_turns: list[DebateTurnORM]

    competitor_evidence: EvidenceBundle
    pricing_evidence: EvidenceBundle
    trust_barrier_evidence: EvidenceBundle
    positioning_evidence: EvidenceBundle
    market_acceptance_evidence: EvidenceBundle
    missing_evidence: MissingEvidenceSummary
    claim_traceability: list[ClaimTraceability]

    cutoff_date: date | None = None


async def load_report_inputs(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    embedding_provider: EmbeddingProvider | None = None,
) -> ReportInputBundle:
    """Load every read-only input the aggregator needs in one bundle."""
    # 1. Simulation row + brief + PIO.
    async with sessionmaker() as session:
        sim = await session.get(Simulation, simulation_id)
        if sim is None:
            raise ValueError(f"simulation {simulation_id} not found")
        # Eagerly read the input on the same session.
        await session.refresh(sim, attribute_names=("input",))
        if sim.input is None:
            raise ValueError(
                f"simulation {simulation_id} has no SimulationInput row "
                "(brief required for aggregation)"
            )
        brief = SimulationBriefIn.model_validate(sim.input.raw_brief)
        if sim.parsed_pio is None:
            raise ValueError(
                f"simulation {simulation_id} has no parsed_pio "
                "(parse_brief must complete before aggregation)"
            )
        pio = ProductIntelligenceObject.model_validate(sim.parsed_pio)
        cutoff_date = sim.evidence_cutoff_date

        # 2. Society.
        society = (
            await session.execute(
                select(AgentORM).where(AgentORM.simulation_id == simulation_id)
            )
        ).scalars().all()
        edges = (
            await session.execute(
                select(AgentEdgeORM).where(
                    AgentEdgeORM.simulation_id == simulation_id
                )
            )
        ).scalars().all()

        # 3. Rounds + responses + debate turns.
        rounds = (
            await session.execute(
                select(SimulationRoundORM)
                .where(SimulationRoundORM.simulation_id == simulation_id)
                .order_by(SimulationRoundORM.round_number.asc())
            )
        ).scalars().all()
        round_ids = [r.id for r in rounds]
        agent_responses: Sequence[AgentResponseORM] = []
        debate_turns: Sequence[DebateTurnORM] = []
        if round_ids:
            agent_responses = (
                await session.execute(
                    select(AgentResponseORM).where(
                        AgentResponseORM.round_id.in_(round_ids)
                    )
                )
            ).scalars().all()
            debate_turns = (
                await session.execute(
                    select(DebateTurnORM).where(
                        DebateTurnORM.round_id.in_(round_ids)
                    )
                )
            ).scalars().all()

    # 4. Evidence graph bundles via the Phase 6.75 helper service. The
    # service is the only path; aggregation never reads evidence_items
    # directly.
    service = EvidenceGraphService(
        sessionmaker=sessionmaker, embedding_provider=embedding_provider,
    )
    competitor_evidence = await service.get_competitor_evidence(
        simulation_id, cutoff_date=cutoff_date,
    )
    pricing_evidence = await service.get_pricing_evidence(
        simulation_id, cutoff_date=cutoff_date,
    )
    trust_barrier_evidence = await service.get_trust_barrier_evidence(
        simulation_id, cutoff_date=cutoff_date,
    )
    positioning_evidence = await service.get_positioning_evidence(
        simulation_id, cutoff_date=cutoff_date,
    )
    market_acceptance_evidence = await service.get_market_acceptance_evidence(
        simulation_id, cutoff_date=cutoff_date,
    )
    missing_evidence = await service.get_missing_evidence_summary(simulation_id)
    claim_traceability = await service.get_claim_traceability(simulation_id)

    return ReportInputBundle(
        simulation=sim,
        brief=brief,
        pio=pio,
        society=list(society),
        edges=list(edges),
        rounds=list(rounds),
        agent_responses=list(agent_responses),
        debate_turns=list(debate_turns),
        competitor_evidence=competitor_evidence,
        pricing_evidence=pricing_evidence,
        trust_barrier_evidence=trust_barrier_evidence,
        positioning_evidence=positioning_evidence,
        market_acceptance_evidence=market_acceptance_evidence,
        missing_evidence=missing_evidence,
        claim_traceability=claim_traceability,
        cutoff_date=cutoff_date,
    )
