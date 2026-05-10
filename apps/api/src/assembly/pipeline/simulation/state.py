"""Phase 6 — Simulation state types.

These are NOT persisted directly; they're inter-round in-memory carriers.
The persisted shape is `agent_responses` rows (existing
[models/round.py](../../models/round.py) schema). `state_after` on those
rows uses `schemas.agent_state.BuyerState`, the mutable per-agent state
that evolves across rounds.

`BuyerStateSnapshot` here is a richer, Phase-6-internal carrier that
bundles BuyerState + accumulated objections + accumulated persuasion
drivers + last reasoning, for re-serialization into the next round's
prompt. It is dumped into the prompt; it is NOT a DB column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from assembly.schemas.agent_state import BuyerState, Stance
from assembly.schemas.round import (
    AgentRoundResponse,
    DebateTurnOut,
    Objection,
    PersuasionDriver,
    StanceShift,
)
from assembly.schemas.society import GeneratedAgent

# Round identifiers. Match the strings used in stage labels and
# `models.round.ROUND_TYPES`.
ROUND_NUMBERS_TO_TYPE: dict[int, str] = {
    1: "baseline",
    2: "first_exposure",
    3: "objection_formation",
    4: "competitor_comparison",
    5: "proof_exposure",
    6: "social_influence",
    7: "final_stance",
}


# ---------------------------------------------------------------------------
# BuyerStateSnapshot — inter-round carrier
# ---------------------------------------------------------------------------


class BuyerStateSnapshot(BaseModel):
    """Per-agent mutable state carried between rounds. Re-serialized into
    every prompt so the LLM never has to "remember" — coherence comes from
    explicit state replay, not memory.

    The agent's IMMUTABLE traits (OCEAN, economic, trust/proof/risk, etc.)
    live on `GeneratedAgent` in the database and are passed alongside this
    snapshot. The snapshot only carries fields that genuinely change from
    one round to the next.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    current_stance: Stance
    state_after: BuyerState  # the mutated BuyerState going into the next round
    accumulated_objections: list[Objection] = Field(default_factory=list)
    accumulated_persuasion_drivers: list[PersuasionDriver] = Field(default_factory=list)
    last_reasoning: str | None = None
    shift_history: list[StanceShift] = Field(default_factory=list)

    @classmethod
    def initial(cls, agent: GeneratedAgent) -> "BuyerStateSnapshot":
        """Build the round-1 starting snapshot from a GeneratedAgent. The
        baseline round uses `current_stance=curious_hesitant` (the neutral
        anchor) so subsequent shifts are well-defined."""
        return cls(
            agent_id=agent.agent_id,
            current_stance="curious_hesitant",  # type: ignore[arg-type]
            state_after=_initial_buyer_state(agent),
            accumulated_objections=[],
            accumulated_persuasion_drivers=[],
            last_reasoning=None,
            shift_history=[],
        )

    def updated_for_response(
        self, response: AgentRoundResponse
    ) -> "BuyerStateSnapshot":
        """Return a fresh snapshot reflecting the agent's response to the
        round just completed. Pure: does not mutate self."""
        new_objections = list(self.accumulated_objections) + list(response.objections)
        new_drivers = list(self.accumulated_persuasion_drivers) + list(
            response.persuasion_drivers
        )
        history = list(self.shift_history)
        if response.shift_from_previous is not None:
            history.append(response.shift_from_previous)

        return BuyerStateSnapshot(
            agent_id=self.agent_id,
            current_stance=response.stance,
            state_after=response.state_after,
            accumulated_objections=new_objections,
            accumulated_persuasion_drivers=new_drivers,
            last_reasoning=response.reasoning,
            shift_history=history,
        )


def _initial_buyer_state(agent: GeneratedAgent) -> BuyerState:
    """Bootstrap a BuyerState from a GeneratedAgent's evidence-anchored
    AgentField values. We extract the `value` strings; the full agent
    (with provenance) is still passed alongside this snapshot to every
    prompt so the LLM sees the basis of every claim."""
    return BuyerState(
        current_alternatives=[
            s.strip()
            for s in agent.current_alternatives.value.split(",")
            if s.strip()
        ] or [agent.current_alternatives.value],
        budget=agent.budget_level.value,
        trust_threshold=agent.trust_threshold.value,
        switching_trigger=agent.switching_trigger.value,
        fear=agent.fear.value,
        desire=agent.desire.value,
        influence_score=agent.influence_score,
        price_sensitivity=agent.price_sensitivity.value,
        current_behavior=agent.summary,
        objection_pattern=agent.objection_pattern.value,
        emotional_state=agent.emotional_state.value,
    )


# ---------------------------------------------------------------------------
# RoundResult — what each round.run_round(...) returns
# ---------------------------------------------------------------------------


@dataclass
class RoundResult:
    """Output of a single round. Persisted by `persistence.write_round_results`."""

    simulation_id: UUID
    round_number: int
    round_type: str
    started_at: datetime
    completed_at: datetime
    agent_responses: list[AgentRoundResponse]
    debate_turns: list[DebateTurnOut] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    new_snapshots: dict[UUID, "BuyerStateSnapshot"] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RoundContext — what each round.run_round(...) takes as input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundContext:
    """Inputs every round needs. Built once by `engine.run_simulation`
    and threaded to each round."""

    simulation_id: UUID
    round_number: int
    round_type: str
    society: list[GeneratedAgent]
    edges: list[Any]  # InfluenceEdge from schemas.society
    pio: Any  # ProductIntelligenceObject
    evidence: list[Any]  # PendingEvidenceItem from pipeline.evidence_builder
    brief: Any  # SimulationBriefIn
    snapshots: dict[UUID, BuyerStateSnapshot]  # per-agent state from end of prior round
    seed: int  # deterministic peer-sampling seed
