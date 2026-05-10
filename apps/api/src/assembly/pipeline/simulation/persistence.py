"""Phase 6 — Persistence layer.

Single write surface for round results. The Phase 6 worker calls
`write_round_results(session, round_result)` after each round; the
function inserts `simulation_rounds`, `agent_responses`, and (round 6
only) `debate_turns` rows. Caller owns the transaction.

Phase 6 explicitly does NOT write `simulation_outputs` — that's Phase 7.
"""
from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from assembly.models.round import (
    AgentResponse as AgentResponseORM,
)
from assembly.models.round import (
    DebateTurn as DebateTurnORM,
)
from assembly.models.round import (
    SimulationRound as SimulationRoundORM,
)
from assembly.pipeline.simulation.state import RoundResult


async def write_round_results(
    session: AsyncSession,
    *,
    round_result: RoundResult,
    agent_id_lookup: dict[UUID, UUID] | None = None,
) -> SimulationRoundORM:
    """Insert one simulation_round row + N agent_responses + (if round 6) M
    debate_turns. Returns the persisted SimulationRound. Caller owns the
    transaction; this only does add() + flush().

    `agent_id_lookup` is reserved for future cases where the in-memory
    agent_id from the engine differs from the persisted DB id. In V0 they
    match 1:1 so the lookup is None.
    """
    round_row = SimulationRoundORM(
        simulation_id=round_result.simulation_id,
        round_number=round_result.round_number,
        round_type=round_result.round_type,
        started_at=round_result.started_at,
        completed_at=round_result.completed_at,
        summary=round_result.summary,
    )
    session.add(round_row)
    await session.flush()  # populate round_row.id

    # agent_responses
    for r in round_result.agent_responses:
        # JSON-roundtrip so SQLAlchemy stores plain dicts in JSONB columns.
        raw = json.loads(r.model_dump_json())
        session.add(
            AgentResponseORM(
                round_id=round_row.id,
                agent_id=r.agent_id,
                stance=r.stance,
                reasoning=r.reasoning,
                objections=raw.get("objections", []),
                persuasion_drivers=raw.get("persuasion_drivers", []),
                shift_from_previous=raw.get("shift_from_previous"),
                state_after=raw.get("state_after", {}),
                raw_output=raw,
            )
        )

    # debate_turns (only round 6 will have any)
    for t in round_result.debate_turns:
        raw = json.loads(t.model_dump_json())
        session.add(
            DebateTurnORM(
                round_id=round_row.id,
                speaker_agent_id=t.speaker_agent_id,
                target_agent_id=t.target_agent_id,
                responding_to_turn_id=t.responding_to_turn_id,
                argument=t.argument,
                caused_shifts=raw.get("caused_shifts", []),
            )
        )

    await session.flush()
    return round_row


__all__ = ["write_round_results"]
