"""Buyer-state schema for synthetic agents.

Important: agents are NOT personas. They are *states of the market*. Each agent
is grounded in real evidence via `evidence_anchors` (foreign keys into the
`evidence_items` table)."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Stance = Literal[
    "strongly_interested",
    "mildly_interested",
    "curious_hesitant",
    "confused",
    "skeptical",
    "resistant",
]


class BuyerState(BaseModel):
    """Persistent per-agent state. Re-serialized into every round's prompt so
    agents do not drift across the simulation."""

    model_config = ConfigDict(extra="forbid")

    current_alternatives: list[str] = Field(
        ..., description="What the agent currently uses or buys instead."
    )
    budget: str = Field(..., description="Approximate purchasing power, free-text.")
    trust_threshold: str = Field(
        ..., description="What level of proof this agent requires before believing."
    )
    switching_trigger: str = Field(
        ..., description="What event or proof would make this agent switch."
    )
    fear: str = Field(..., description="Single dominant anxiety blocking adoption.")
    desire: str = Field(..., description="Single dominant desire pulling toward adoption.")
    influence_score: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    price_sensitivity: str
    current_behavior: str
    objection_pattern: str
    emotional_state: str = Field(
        ..., description="Pre-exposure emotional state toward the category."
    )


class Agent(BaseModel):
    """A single buyer-state agent in the synthetic society."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    segment_label: str
    weight: Annotated[float, Field(gt=0.0, le=1.0)] = 0.05
    buyer_state: BuyerState
    traits: dict = Field(default_factory=dict)
    evidence_anchors: list[UUID] = Field(
        default_factory=list,
        description="evidence_items.id foreign keys that grounded this agent.",
    )
