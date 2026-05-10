"""Phase 8.5E — schemas for the run-scoped persona simulation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Round types — matches the Phase 6 engine concepts but uses
# market-entry-friendly labels per the 8.5E spec. There is no DB
# CHECK on `simulation_rounds.round_type` at the model level, so
# either label works at the persistence layer.
AGENT_ROUND_TYPES: tuple[str, ...] = (
    "baseline_context",
    "first_exposure",
    "objection_formation",
    "competitor_comparison",
    "proof_exposure",
    "social_influence",
    "final_stance",
)
RoundType = Literal[
    "baseline_context", "first_exposure", "objection_formation",
    "competitor_comparison", "proof_exposure", "social_influence",
    "final_stance",
]

# Market-entry-safe stance labels per the 8.5E spec. The existing
# `agent_responses.stance` column is `String(32)` with no DB CHECK,
# so these labels are persistence-compatible.
MARKET_ENTRY_STANCES: tuple[str, ...] = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)
SimulationStanceLabel = Literal[
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
]


class RoundOutputAudit(BaseModel):
    """Per-(agent, round) audit row produced by the simulation."""

    model_config = ConfigDict(extra="forbid")

    agent_persona_id: str
    display_name: str
    compressed_candidate_id: str | None = None
    normalized_primary_role: str
    round_type: RoundType
    round_number: int
    # Free-form to allow the audit to RECORD invalid labels too;
    # validity is enforced by `validate_market_entry_stance_label` and
    # by the quality evaluator's stance_validity_score.
    stance: str | None
    reasoning: str
    objections: list[dict[str, Any]]
    persuasion_levers: list[dict[str, Any]]
    competitor_mentions: list[str]
    shift_from_previous: dict[str, Any] | None = None
    forbidden_claim_audit: list[str]  # populated by universal validator
    raw_text: str
