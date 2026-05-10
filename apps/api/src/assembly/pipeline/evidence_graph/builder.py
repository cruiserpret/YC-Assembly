"""Phase 6.75 — top-level evidence graph builder.

Idempotent: if `simulations.evidence_graph_built_at` is non-null, skip.
Explicit rebuild requires nulling that flag AND clearing evidence_edges
for the simulation (see `clear_graph_for_rebuild`).

Run order:
  1. classify (deterministic + optional LLM fallback)
  2. dedup (content_hash → dedup_group_id, similar_to edges)
  3. embed (skip if no embedding provider; cutoff-aware)
  4. derive deterministic edges (competes_with / priced_against / maps_to_competitor)
  5. derive inferred edges (LLM, optional, basis='inferred')
  6. mark `simulations.evidence_graph_built_at = now`
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.embeddings.provider import EmbeddingProvider
from assembly.llm.provider import LLMProvider
from assembly.models.evidence_edge import EvidenceEdge
from assembly.models.simulation import Simulation
from assembly.pipeline.evidence_graph.classifier import classify_all
from assembly.pipeline.evidence_graph.dedup import run_dedup
from assembly.pipeline.evidence_graph.edge_builder import (
    derive_deterministic_edges,
    derive_inferred_edges,
)
from assembly.pipeline.evidence_graph.embedder import embed_eligible_items

logger = logging.getLogger(__name__)


@dataclass
class EvidenceGraphResult:
    classified_count: int
    dedup_groups: int
    embedded_count: int
    deterministic_edges: int
    inferred_edges: int
    skipped: bool = False  # True if idempotent skip


async def clear_graph_for_rebuild(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
) -> None:
    """Explicit rebuild path. Caller must invoke this BEFORE re-running
    `build_evidence_graph` if they want to start fresh. Clears:
      - evidence_edges for this simulation
      - simulations.evidence_graph_built_at = NULL
    Does NOT clear node_class / dedup_group_id on evidence_items, since
    those are stable on re-classification anyway."""
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                EvidenceEdge.__table__.delete().where(
                    EvidenceEdge.simulation_id == simulation_id
                )
            )
            await session.execute(
                Simulation.__table__.update()
                .where(Simulation.id == simulation_id)
                .values(evidence_graph_built_at=None)
            )


async def build_evidence_graph(
    *,
    simulation_id: UUID,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider | None,
    model: str | None,
    embedding_provider: EmbeddingProvider | None = None,
    use_llm_classifier: bool = True,
    use_llm_edges: bool = True,
) -> EvidenceGraphResult:
    """Build the graph for one simulation. Idempotent."""
    async with sessionmaker() as session:
        sim = await session.get(Simulation, simulation_id)
        if sim is None:
            raise ValueError(f"simulation {simulation_id} not found")
        if sim.evidence_graph_built_at is not None:
            logger.info(
                "evidence_graph.skip_already_built sim=%s built_at=%s",
                simulation_id, sim.evidence_graph_built_at,
            )
            return EvidenceGraphResult(0, 0, 0, 0, 0, skipped=True)
        cutoff_date = sim.evidence_cutoff_date

    classified = await classify_all(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        provider=provider if use_llm_classifier else None,
        model=model if use_llm_classifier else None,
        use_llm_fallback=use_llm_classifier and provider is not None and model is not None,
    )

    dedup_groups = await run_dedup(
        sessionmaker=sessionmaker, simulation_id=simulation_id
    )

    embedded = 0
    if embedding_provider is not None:
        embedded = await embed_eligible_items(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            embedding_provider=embedding_provider,
            cutoff_date=cutoff_date,
        )

    det_edges = await derive_deterministic_edges(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        cutoff_date=cutoff_date,
    )

    inf_edges = 0
    if use_llm_edges and provider is not None and model is not None:
        inf_edges = await derive_inferred_edges(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            provider=provider,
            model=model,
            cutoff_date=cutoff_date,
        )

    # Mark the sim as graph-built.
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                Simulation.__table__.update()
                .where(Simulation.id == simulation_id)
                .values(evidence_graph_built_at=datetime.now(UTC))
            )

    return EvidenceGraphResult(
        classified_count=classified,
        dedup_groups=dedup_groups,
        embedded_count=embedded,
        deterministic_edges=det_edges,
        inferred_edges=inf_edges,
    )
