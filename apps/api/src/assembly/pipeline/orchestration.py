"""Phase 6.5 — Pipeline orchestrator.

`run_full_pipeline(simulation_id)` is the single entry point that walks a
simulation through every backend stage:

    pending → parsing → evidence_building → society_building
            → simulating → simulation_completed
            → (aggregating → reported)   ← Phase 7, gated by flag

Idempotent: each stage checks the DB for its own outputs and skips if
already done. Re-enqueueing a `failed` simulation resumes from the last
incomplete stage.

The orchestrator is the SINGLE source of truth for status transitions.
The engine, parser, and other stages no longer set `simulations.status`
themselves — they only do their work and update operational metadata.

Cost-guard discipline preserved: the orchestrator does NOT call
`provider.chat(...)` directly. Every LLM call still goes through
`call_llm_for_simulation` inside the per-stage modules. The drift
tripwire is extended to scan this file too.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.config import get_settings
from assembly.llm.provider import LLMProvider
from assembly.models.agent import Agent as AgentORM
from assembly.models.evidence import EvidenceItem as EvidenceItemORM
from assembly.models.round import SimulationRound as SimulationRoundORM
from assembly.models.simulation import Simulation, SimulationInput
from assembly.pipeline.aggregation import run_aggregation
from assembly.pipeline.evidence_builder import (
    EvidenceBuildResult,
    PendingEvidenceItem,
    build_evidence,
)
from assembly.pipeline.intake_parser import parse_brief
from assembly.pipeline.progress import update_status_and_progress
from assembly.pipeline.simulation.engine import (
    SimulationEngineResult,
    run_simulation,
)
from assembly.pipeline.society_builder import build_society
from assembly.schemas.brief import (
    CompetitorRef,
    PriceStructure,
    SimulationBriefIn,
    TargetSociety,
)
from assembly.schemas.product_intelligence import ProductIntelligenceObject
from assembly.schemas.society import (
    AgentField,
    AgentTraits,
    BasisKind,
    GeneratedAgent,
    InfluenceEdge,
    SocietyBuildResult,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    simulation_id: UUID
    final_status: str
    rounds_completed: int
    total_cost_usd: float


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


async def run_full_pipeline(
    *,
    simulation_id: UUID,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
) -> PipelineResult:
    """Run every backend stage for a single simulation. Idempotent — safe to
    call repeatedly on the same simulation_id.

    Stages:
      1. parsing        → simulations.parsed_pio JSONB
      2. evidence       → evidence_items rows
      3. society        → agents + agent_edges rows
      4. simulating     → simulation_rounds + agent_responses + debate_turns
      5. (aggregating)  → only if ASSEMBLY_ENABLE_AGGREGATION=true (Phase 7)
    """
    settings = get_settings()
    logger.info("orchestration.start sim=%s", simulation_id)

    # Stage 1 — parsing
    pio = await _stage_parsing(
        simulation_id=simulation_id,
        sessionmaker=sessionmaker,
        provider=provider,
    )

    # Stage 2 — evidence
    evidence = await _stage_evidence(
        simulation_id=simulation_id,
        pio=pio,
        sessionmaker=sessionmaker,
        provider=provider,
    )

    # Stage 2.5 (Phase 6.75) — evidence graph
    await _stage_evidence_graph(
        simulation_id=simulation_id,
        sessionmaker=sessionmaker,
        provider=provider,
    )

    # Stage 3 — society
    society = await _stage_society(
        simulation_id=simulation_id,
        pio=pio,
        evidence=evidence,
        sessionmaker=sessionmaker,
        provider=provider,
    )

    # Stage 4 — simulating
    engine_result = await _stage_simulating(
        simulation_id=simulation_id,
        pio=pio,
        evidence=evidence,
        society=society,
        sessionmaker=sessionmaker,
        provider=provider,
    )

    # Mark simulation_completed BEFORE optionally calling aggregation.
    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        new_status="simulation_completed",
        progress_changes={"stage": "simulation_completed"},
    )

    # Stage 5 — aggregation (Phase 7). When the flag is off, run_aggregation
    # is a no-op — status stays at simulation_completed. When on, the
    # orchestrator transitions through aggregating → reported and any
    # AggregationFailed bubbles up as a `failed` simulation row.
    final_status = "simulation_completed"
    if settings.enable_aggregation:
        await update_status_and_progress(
            sessionmaker,
            simulation_id=simulation_id,
            new_status="aggregating",
            progress_changes={"stage": "aggregating"},
        )
        try:
            await run_aggregation(
                simulation_id=simulation_id,
                sessionmaker=sessionmaker,
                provider=provider,
            )
        except Exception as e:
            await _mark_failed(
                sessionmaker, simulation_id, stage="aggregating", error=e
            )
            raise
        await update_status_and_progress(
            sessionmaker,
            simulation_id=simulation_id,
            new_status="reported",
            progress_changes={"stage": "reported"},
        )
        final_status = "reported"

    return PipelineResult(
        simulation_id=simulation_id,
        final_status=final_status,
        rounds_completed=len(engine_result.rounds),
        total_cost_usd=engine_result.total_cost_usd,
    )


# ---------------------------------------------------------------------------
# Stage 1 — parsing (idempotent: check parsed_pio)
# ---------------------------------------------------------------------------


async def _stage_parsing(
    *,
    simulation_id: UUID,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
) -> ProductIntelligenceObject:
    settings = get_settings()

    # Idempotency check: if parsed_pio is already cached, deserialize it.
    async with sessionmaker() as session:
        sim = await session.get(Simulation, simulation_id)
        if sim is None:
            raise ValueError(f"simulation {simulation_id} not found")
        if sim.parsed_pio:
            logger.info("orchestration.parsing.skip_cached sim=%s", simulation_id)
            return ProductIntelligenceObject.model_validate(sim.parsed_pio)

    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        new_status="parsing",
        progress_changes={"stage": "parsing"},
    )

    try:
        brief = await _load_brief(sessionmaker, simulation_id)
        result = await parse_brief(
            brief,
            provider=provider,
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            model=settings.llm_synthesis_model,
        )
    except Exception as e:
        await _mark_failed(
            sessionmaker, simulation_id, stage="parsing", error=e
        )
        raise

    # Cache the PIO into simulations.parsed_pio for idempotency on retry.
    async with sessionmaker() as session:
        async with session.begin():
            sim = await session.get(Simulation, simulation_id)
            if sim is not None:
                sim.parsed_pio = json.loads(
                    result.product_intelligence.model_dump_json()
                )

    return result.product_intelligence


# ---------------------------------------------------------------------------
# Stage 2 — evidence (idempotent: check evidence_items)
# ---------------------------------------------------------------------------


async def _stage_evidence_graph(
    *,
    simulation_id: UUID,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
) -> None:
    """Phase 6.75 — build the typed evidence graph between evidence_building
    and society_building. Idempotent via `simulations.evidence_graph_built_at`.

    Uses MockEmbeddingProvider when no `OPENAI_API_KEY` is configured (the
    embedding pass writes deterministic vectors so retrieval still ranks).
    The LLM-typed-edge pass is gated on `provider` being available; the
    deterministic edges always run.
    """
    from assembly.embeddings import (
        MockEmbeddingProvider,
        NoEmbeddingProvider,
        OpenAIEmbeddingProvider,
    )
    from assembly.llm.errors import LLMProviderError
    from assembly.pipeline.evidence_graph import build_evidence_graph

    settings = get_settings()
    embedding_provider = None
    try:
        embedding_provider = OpenAIEmbeddingProvider()
    except LLMProviderError:
        # No OpenAI key — fall back to mock for deterministic vectors.
        # Tests/mock paths land here. NoEmbeddingProvider would skip embed
        # entirely; mock keeps retrieval well-ranked even offline.
        embedding_provider = MockEmbeddingProvider()
    if embedding_provider is None:  # pragma: no cover  defensive
        embedding_provider = NoEmbeddingProvider()

    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        new_status="evidence_graph_building",
        progress_changes={"stage": "evidence_graph_building"},
    )

    try:
        await build_evidence_graph(
            simulation_id=simulation_id,
            sessionmaker=sessionmaker,
            provider=provider,
            model=settings.llm_synthesis_model,
            embedding_provider=embedding_provider,
            # Keep LLM-typed edges OFF in default runs to control cost; the
            # graph still has deterministic edges + dedup + classification +
            # embeddings. Operators can flip this on for higher-quality
            # graphs at higher cost.
            use_llm_classifier=False,
            use_llm_edges=False,
        )
    except Exception as e:
        await _mark_failed(
            sessionmaker, simulation_id, stage="evidence_graph_building", error=e
        )
        raise


async def _stage_evidence(
    *,
    simulation_id: UUID,
    pio: ProductIntelligenceObject,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
) -> EvidenceBuildResult:
    # Idempotency check: any evidence_items rows mean we've run before.
    async with sessionmaker() as session:
        existing_count = (
            await session.execute(
                select(func.count(EvidenceItemORM.id))
                .where(EvidenceItemORM.simulation_id == simulation_id)
            )
        ).scalar_one() or 0

    if existing_count > 0:
        logger.info(
            "orchestration.evidence.skip_existing sim=%s rows=%d",
            simulation_id, existing_count,
        )
        return await _hydrate_evidence_from_db(
            sessionmaker=sessionmaker, simulation_id=simulation_id
        )

    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        new_status="evidence_building",
        progress_changes={"stage": "evidence_building"},
    )

    try:
        brief = await _load_brief(sessionmaker, simulation_id)
        result = await build_evidence(
            brief,
            simulation_id=simulation_id,
            sessionmaker=sessionmaker,
            provider=provider,
            extractor_model=get_settings().llm_synthesis_model,
        )
    except Exception as e:
        await _mark_failed(
            sessionmaker, simulation_id, stage="evidence_building", error=e
        )
        raise

    # Persist evidence_items.
    async with sessionmaker() as session:
        async with session.begin():
            for item in result.items:
                session.add(
                    EvidenceItemORM(
                        id=item.id,
                        simulation_id=item.simulation_id,
                        kind=item.kind,
                        source_type=item.source_type,
                        source_url=item.source_url,
                        content=item.content,
                        captured_at=item.captured_at,
                        metadata_=item.metadata,
                    )
                )

    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        progress_changes={
            "evidence_items_collected": len(result.items),
            "fetched_pages": len(result.fetched_pages),
        },
    )

    return result


# ---------------------------------------------------------------------------
# Stage 3 — society (idempotent: check agents)
# ---------------------------------------------------------------------------


async def _stage_society(
    *,
    simulation_id: UUID,
    pio: ProductIntelligenceObject,
    evidence: EvidenceBuildResult,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
) -> SocietyBuildResult:
    settings = get_settings()

    async with sessionmaker() as session:
        existing_count = (
            await session.execute(
                select(func.count(AgentORM.id))
                .where(AgentORM.simulation_id == simulation_id)
            )
        ).scalar_one() or 0

    if existing_count > 0:
        logger.info(
            "orchestration.society.skip_existing sim=%s agents=%d",
            simulation_id, existing_count,
        )
        return await _hydrate_society_from_db(
            sessionmaker=sessionmaker, simulation_id=simulation_id
        )

    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        new_status="society_building",
        progress_changes={"stage": "society_building"},
    )

    try:
        brief = await _load_brief(sessionmaker, simulation_id)
        # Phase 6.75: re-hydrate evidence from DB so society_builder sees
        # node_class + dedup_group_id annotations populated by the graph
        # stage. The in-memory `evidence` object from build_evidence still
        # carries the raw items but lacks the post-graph metadata.
        evidence = await _hydrate_evidence_from_db(
            sessionmaker=sessionmaker, simulation_id=simulation_id
        )
        result = await build_society(
            simulation_id=simulation_id,
            brief=brief,
            pio=pio,
            evidence=evidence,
            provider=provider,
            sessionmaker=sessionmaker,
            model=settings.llm_synthesis_model,
            desired_size=settings.simulation_default_society_size,
            # Six-layer trait society dumps are large (~20KB JSON for 6 agents).
            # Allow an extra repair round to recover from occasional malformed
            # JSON that the tolerant parser couldn't fix.
            max_repair_attempts=3,
        )
    except Exception as e:
        await _mark_failed(
            sessionmaker, simulation_id, stage="society_building", error=e
        )
        raise

    # Persist agents + edges.
    from assembly.pipeline.society_builder import persist_society
    async with sessionmaker() as session:
        async with session.begin():
            await persist_society(
                session, simulation_id=simulation_id, society=result
            )

    return result


# ---------------------------------------------------------------------------
# Stage 4 — simulating (engine handles per-round resume internally)
# ---------------------------------------------------------------------------


async def _stage_simulating(
    *,
    simulation_id: UUID,
    pio: ProductIntelligenceObject,
    evidence: EvidenceBuildResult,
    society: SocietyBuildResult,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
) -> SimulationEngineResult:
    await update_status_and_progress(
        sessionmaker,
        simulation_id=simulation_id,
        new_status="simulating",
        progress_changes={"stage": "simulating"},
    )

    try:
        brief = await _load_brief(sessionmaker, simulation_id)
        return await run_simulation(
            simulation_id=simulation_id,
            brief=brief,
            pio=pio,
            evidence=evidence,
            society=society,
            provider=provider,
            sessionmaker=sessionmaker,
        )
    except Exception as e:
        await _mark_failed(
            sessionmaker, simulation_id, stage="simulating", error=e
        )
        raise


# ---------------------------------------------------------------------------
# Helpers — load brief, hydrate from DB, mark failed
# ---------------------------------------------------------------------------


async def _load_brief(
    sessionmaker: async_sessionmaker, simulation_id: UUID
) -> SimulationBriefIn:
    """Reconstruct SimulationBriefIn from the persisted SimulationInput row."""
    async with sessionmaker() as session:
        row = (
            await session.execute(
                select(SimulationInput)
                .where(SimulationInput.simulation_id == simulation_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(
                f"simulation_inputs row missing for simulation {simulation_id}"
            )

    return SimulationBriefIn(
        product_type=row.product_type,
        product_name=row.product_name,
        description=row.description,
        price_structure=PriceStructure(**row.price_structure),
        target_society=TargetSociety(**row.target_society),
        competitors=[CompetitorRef(**c) for c in row.competitors],
        product_url=row.product_url,
        additional_context=row.additional_context,
    )


async def _hydrate_evidence_from_db(
    *, sessionmaker: async_sessionmaker, simulation_id: UUID
) -> EvidenceBuildResult:
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(EvidenceItemORM)
                .where(EvidenceItemORM.simulation_id == simulation_id)
            )
        ).scalars().all()

    items = []
    for r in rows:
        # Phase 6.75: surface graph classification into the metadata view so
        # society_builder's prompt can reason about node_class. The DB value
        # wins over any cached value in metadata.
        meta = dict(r.metadata_ or {})
        if r.node_class:
            meta["node_class"] = r.node_class
            meta["node_class_confidence"] = float(r.node_class_confidence or 0)
        if r.dedup_group_id is not None:
            meta["dedup_group_id"] = str(r.dedup_group_id)
        items.append(
            PendingEvidenceItem(
                id=r.id,
                simulation_id=r.simulation_id,
                kind=r.kind,
                source_type=r.source_type,
                source_url=r.source_url,
                content=r.content,
                captured_at=r.captured_at,
                metadata=meta,
            )
        )
    return EvidenceBuildResult(
        items=items,
        fetched_pages=[],
        fetch_errors=[],
        extracted_phrases=[],
    )


async def _hydrate_society_from_db(
    *, sessionmaker: async_sessionmaker, simulation_id: UUID
) -> SocietyBuildResult:
    """Reconstruct SocietyBuildResult by reading agents+edges from DB.
    The buyer_state JSONB on each AgentORM is a full GeneratedAgent dump
    from Phase 5; we reconstruct via Pydantic model_validate."""
    from assembly.models.agent import AgentEdge as AgentEdgeORM

    async with sessionmaker() as session:
        agents_rows = (
            await session.execute(
                select(AgentORM)
                .where(AgentORM.simulation_id == simulation_id)
                .order_by(AgentORM.created_at)
            )
        ).scalars().all()
        edges_rows = (
            await session.execute(
                select(AgentEdgeORM)
                .where(AgentEdgeORM.simulation_id == simulation_id)
            )
        ).scalars().all()

    agents = [
        GeneratedAgent.model_validate(a.buyer_state) for a in agents_rows
    ]
    edges = [
        InfluenceEdge(
            source_agent_id=e.source_agent_id,
            target_agent_id=e.target_agent_id,
            influence_strength=e.influence_strength,
            cluster_label=e.cluster_label,
        )
        for e in edges_rows
    ]
    return SocietyBuildResult(
        agents=agents,
        edges=edges,
        segments=sorted({a.segment for a in agents}),
    )


async def _mark_failed(
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    *,
    stage: str,
    error: Exception,
) -> None:
    """Single failure-attribution path. Sets status='failed', failed_stage,
    error JSONB, all in one transaction so status polls see consistent state."""
    error_payload = {
        "kind": type(error).__name__,
        "message": str(error)[:1000],
    }
    try:
        await update_status_and_progress(
            sessionmaker,
            simulation_id=simulation_id,
            new_status="failed",
            failed_stage=stage,
            error=error_payload,
            progress_changes={"stage": "failed", "notes": f"failed at {stage}"},
        )
    except Exception:
        # If even the failure-write fails, log but don't mask the original error.
        logger.exception(
            "orchestration._mark_failed.write_error sim=%s stage=%s",
            simulation_id, stage,
        )


__all__ = ["PipelineResult", "run_full_pipeline"]
