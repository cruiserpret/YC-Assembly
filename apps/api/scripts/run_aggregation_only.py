"""Re-run aggregation against an already-completed simulation.

Usage:
  uv run python scripts/run_aggregation_only.py <simulation_id>

Reads the sim's existing simulation_rounds / agent_responses / debate_turns
/ evidence graph, runs `run_aggregation_v7`, and reports the result. After
success, status flips to `reported` and a `simulation_outputs` row exists.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from assembly.db import get_sessionmaker
from assembly.embeddings import MockEmbeddingProvider, OpenAIEmbeddingProvider
from assembly.llm.errors import LLMProviderError
from assembly.models.simulation import Simulation
from assembly.pipeline.aggregation.service import run_aggregation_v7
from assembly.pipeline.progress import update_status_and_progress
from assembly.workers import build_provider


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_aggregation_only.py <simulation_id>")
        return 1
    sim_id = UUID(sys.argv[1])
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        sim = await session.get(Simulation, sim_id)
        if sim is None:
            print(f"simulation {sim_id} not found")
            return 1
        print(f"sim status before: {sim.status}, cost_so_far: {float(sim.total_cost_usd or 0):.4f}")

    await update_status_and_progress(
        sessionmaker,
        simulation_id=sim_id,
        new_status="aggregating",
        progress_changes={"stage": "aggregating"},
    )

    provider = build_provider()
    try:
        embedding = OpenAIEmbeddingProvider()
    except LLMProviderError:
        embedding = MockEmbeddingProvider()

    started = datetime.now(UTC)
    try:
        row = await run_aggregation_v7(
            simulation_id=sim_id,
            sessionmaker=sessionmaker,
            provider=provider,
            embedding_provider=embedding,
        )
    except Exception as e:
        # On failure, mark failed and re-raise.
        print(f"\n=== AGGREGATION FAILED: {type(e).__name__}: {e}")
        async with sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    Simulation.__table__.update()
                    .where(Simulation.id == sim_id)
                    .values(
                        status="failed",
                        failed_stage="aggregating",
                        error={"kind": type(e).__name__, "message": str(e)},
                    )
                )
        return 1

    await update_status_and_progress(
        sessionmaker,
        simulation_id=sim_id,
        new_status="reported",
        progress_changes={"stage": "reported"},
    )

    elapsed = (datetime.now(UTC) - started).total_seconds()
    print(f"\n=== AGGREGATION COMPLETE ===")
    print(f"  simulation_id:   {sim_id}")
    print(f"  output id:       {row.id}")
    print(f"  validator passed: {row.validator_passed}")
    print(f"  elapsed:         {elapsed:.1f}s")
    print(f"  schema version:  {row.schema_version}")
    print(f"  9 sections present: {bool(row.public_opinion_sentiment) and bool(row.recommendations)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
