"""Per-round schemas: agent responses, debate turns, round summaries."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from assembly.schemas.agent_state import BuyerState, Stance

RoundType = Literal[
    "baseline",
    "first_exposure",
    "objection_formation",
    "competitor_comparison",
    "proof_exposure",
    "social_influence",
    "final_stance",
]


class Objection(BaseModel):
    text: str
    severity: Literal["mild", "moderate", "strong", "blocking"] = "moderate"
    category: str | None = Field(
        default=None,
        description="Lightweight tag the aggregator uses for objection clustering.",
    )


class PersuasionDriver(BaseModel):
    text: str
    strength: Literal["weak", "moderate", "strong"] = "moderate"
    category: str | None = None


class StanceShift(BaseModel):
    """How an agent's stance moved between rounds."""

    from_stance: Stance
    to_stance: Stance
    reason: str
    triggered_by: str | None = Field(
        default=None,
        description="A debate-turn id, evidence id, or free-text trigger label.",
    )


class AgentRoundResponse(BaseModel):
    """Structured output the LLM returns for a single agent in a single round."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    stance: Stance
    reasoning: str
    objections: list[Objection] = Field(default_factory=list)
    persuasion_drivers: list[PersuasionDriver] = Field(default_factory=list)
    shift_from_previous: StanceShift | None = None
    state_after: BuyerState


class DebateTurnOut(BaseModel):
    speaker_agent_id: UUID
    target_agent_id: UUID | None = None
    responding_to_turn_id: UUID | None = None
    argument: str
    caused_shifts: list[StanceShift] = Field(default_factory=list)
