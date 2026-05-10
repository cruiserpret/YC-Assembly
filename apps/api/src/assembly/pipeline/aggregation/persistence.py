"""Phase 7 — write the simulation_outputs row + claims rows.

One row per simulation. If a row already exists, raise — never overwrite.
Operators who want to re-aggregate must explicitly clear the row first
(see `clear_simulation_output`).

Read-only over `agent_responses`, `simulation_rounds`, `debate_turns`,
`evidence_items`, `evidence_edges`. Phase 7 must not mutate raw simulation
data.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.claim import Claim
from assembly.models.output import SimulationOutput

logger = logging.getLogger(__name__)


class SimulationOutputAlreadyExists(Exception):
    """Raised when persistence would create a duplicate simulation_outputs
    row. Phase 7 V0 refuses overwrite — operators must explicitly clear."""


async def write_simulation_output(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    sections: dict[str, Any],
    claims: list[dict[str, Any]],
    validator_passed: bool,
    validator_notes: dict[str, Any],
) -> SimulationOutput:
    """Insert one simulation_outputs row + N claims rows in a single
    transaction. Refuses if a simulation_outputs row already exists.

    `sections` keys MUST match the SimulationOutput JSONB columns:
      public_opinion_sentiment, persuasion_analysis,
      market_acceptance_requirement, product_trajectory,
      competitor_analysis, recommendations, debate_shift_markers,
      confidence, evidence_ledger.
    """
    async with sessionmaker() as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(SimulationOutput).where(
                        SimulationOutput.simulation_id == simulation_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise SimulationOutputAlreadyExists(
                    f"simulation {simulation_id} already has a simulation_outputs "
                    "row. Phase 7 refuses overwrite. Use clear_simulation_output() "
                    "explicitly to re-aggregate."
                )

            row = SimulationOutput(
                simulation_id=simulation_id,
                public_opinion_sentiment=sections["public_opinion_sentiment"],
                persuasion_analysis=sections["persuasion_analysis"],
                market_acceptance_requirement=sections["market_acceptance_requirement"],
                product_trajectory=sections["product_trajectory"],
                competitor_analysis=sections["competitor_analysis"],
                recommendations=sections["recommendations"],
                debate_shift_markers=sections["debate_shift_markers"],
                confidence=sections["confidence"],
                evidence_ledger=sections["evidence_ledger"],
                validator_passed=validator_passed,
                validator_notes=validator_notes,
            )
            session.add(row)
            await session.flush()

            for c in claims:
                claim_row = Claim(
                    simulation_id=simulation_id,
                    text=c["text"],
                    source_evidence_id=c["source_evidence_id"],
                    source_url=c.get("source_url"),
                    source_excerpt=c["source_excerpt"],
                    claim_type=c["claim_type"],
                    basis=c["basis"],
                    confidence=c["confidence"],
                )
                session.add(claim_row)

        # Re-fetch outside the begin() block so the caller has a usable copy.
        result = (
            await session.execute(
                select(SimulationOutput).where(
                    SimulationOutput.simulation_id == simulation_id
                )
            )
        ).scalar_one()
        return result


async def clear_simulation_output(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
) -> None:
    """Operator-only: delete the simulation_outputs row + claims for a
    simulation so aggregation can be rerun. NOT exposed via the API."""
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                Claim.__table__.delete().where(
                    Claim.simulation_id == simulation_id
                )
            )
            await session.execute(
                SimulationOutput.__table__.delete().where(
                    SimulationOutput.simulation_id == simulation_id
                )
            )
