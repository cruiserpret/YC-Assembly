"""Phase 9A.4 — schemas for discussion turns, private ballots, and
memory atoms. `extra="forbid"`. Closed-set Literals.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DiscussionStance = Literal[
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
]
TurnType = Literal[
    "public_opening",
    "challenge",
    "peer_response",
    "proof_discussion",
]
BallotStage = Literal["pre", "reflection", "final"]
Confidence = Literal["high", "medium", "low"]
PublicPrivateDelta = Literal[
    "private_acceptance",
    "public_compliance_only",
    "resistance",
    "no_change",
    "polarization",
    "uncertainty_increase",
]


class PsychologyControlSnapshot(BaseModel):
    """Snapshot of the 11 psychology trait values + labels at the time
    of the turn. Recorded on every turn so the evaluator can verify the
    psychology actually drove behavior."""

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    openness: float = Field(ge=0.0, le=1.0)
    conscientiousness: float = Field(ge=0.0, le=1.0)
    extraversion: float = Field(ge=0.0, le=1.0)
    agreeableness: float = Field(ge=0.0, le=1.0)
    neuroticism: float = Field(ge=0.0, le=1.0)
    risk_tolerance: float = Field(ge=0.0, le=1.0)
    novelty_seeking: float = Field(ge=0.0, le=1.0)
    trust_proof_threshold: float = Field(ge=0.0, le=1.0)
    social_influence_susceptibility: float = Field(ge=0.0, le=1.0)
    category_involvement_or_expertise: float = Field(ge=0.0, le=1.0)
    price_sensitivity: float = Field(ge=0.0, le=1.0)


class TurnDraft(BaseModel):
    """One public turn from one persona. The orchestrator parses this
    out of the LLM response and persists into `discussion_turns`."""

    model_config = ConfigDict(extra="forbid")

    speaker_persona_id: str
    target_persona_id: str | None = None
    turn_type: TurnType
    public_text: str = Field(min_length=1, max_length=4000)
    stance: DiscussionStance | None = None
    referenced_turn_ids: list[str] = Field(default_factory=list)
    referenced_source_record_ids: list[str] = Field(default_factory=list)
    referenced_memory_atom_ids: list[str] = Field(default_factory=list)


class PrivateBallotDraft(BaseModel):
    """One private ballot from one persona for one ballot_stage."""

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    ballot_stage: BallotStage
    private_stance: DiscussionStance
    private_reasoning: str = Field(min_length=1, max_length=4000)
    confidence: Confidence
    public_private_delta: PublicPrivateDelta | None = None
    top_objection: str | None = None
    top_proof_need: str | None = None


class PsychologyTraitSnapshotInput(BaseModel):
    """Compact persona profile fed into prompts (not persisted itself).
    Carries the 11 psychology trait labels + values + the persona's
    role + market traits + key evidence excerpts.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    display_name: str
    normalized_primary_role: str
    psychology_trait_values: dict[str, float]
    psychology_trait_labels: dict[str, str]
    market_traits: list[dict[str, str]]
    evidence_excerpts: list[str]
    prior_simulation_final_stance: str | None = None
