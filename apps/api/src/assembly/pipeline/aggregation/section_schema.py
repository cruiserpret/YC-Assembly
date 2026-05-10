"""Phase 7 — Pydantic schemas for the 9 report sections.

Every section carries the same shape skeleton:

  summary             # subjective prose; runs through validate_text
  evidence_anchors    # list[UUID] referencing real evidence_items.id
  simulation_references # references to agent_responses / debate_turns / rounds
  confidence          # subjective qualitative ("clear" / "moderate" / "thin")
  validator_notes     # collected violations from the post-parse sweep

Every UUID field is enforced by Pydantic; downstream resolution to actual
DB rows happens in `synthesis.py` post-parse. Schemas use `extra='forbid'`
so the LLM cannot smuggle in extra keys (same lesson as Phase 6.5's
response-shape footer).

Traceability rule (per Phase 7 Clarification 1):
  - Every section ALWAYS stores: summary, evidence_anchors, simulation_references,
    confidence, validator_notes.
  - Sections may store additional structured payloads where mechanical
    derivation produces them (debate_shift_markers list, split confidence
    metric, etc.) — those are typed below.
  - The human-facing prose in `summary` does not need to expose UUIDs;
    auditability comes from the JSONB columns.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ConfidenceLevel = Literal["thin", "moderate", "clear"]

ClaimType = Literal["support", "contradiction", "observation", "objection"]
ClaimBasis = Literal["direct", "analogical"]


# ---------------------------------------------------------------------------
# Reusable building blocks
# ---------------------------------------------------------------------------


class SimulationReference(BaseModel):
    """Pointer to a piece of simulation state. `kind` tells the reader what
    `target_id` refers to. Used to make every report claim auditable back to
    a real row in `agent_responses`, `debate_turns`, or `simulation_rounds`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "agent_response", "debate_turn", "simulation_round", "agent",
        "evidence_item",  # for cases where an anchor isn't already in evidence_anchors
    ]
    target_id: UUID
    note: str | None = None


class FactualClaim(BaseModel):
    """A factual claim emitted by the LLM that MUST bind to evidence.
    Persisted as a `claims` row by the aggregation persistence layer.

    Per Clarification 2: subjective interpretation does NOT use this — it
    flows in `summary` and gets validator-checked but no claim row.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    source_evidence_id: UUID
    source_excerpt: str
    claim_type: ClaimType
    basis: ClaimBasis
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 9-section base + per-section schemas
# ---------------------------------------------------------------------------


class _SectionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    evidence_anchors: list[UUID] = Field(default_factory=list)
    simulation_references: list[SimulationReference] = Field(default_factory=list)
    confidence: ConfidenceLevel = "moderate"
    validator_notes: list[str] = Field(default_factory=list)


# Sections 1–4 — Call A
class PublicOpinionSentimentSection(_SectionBase):
    pass


class PersuadedSection(_SectionBase):
    factual_claims: list[FactualClaim] = Field(default_factory=list)


class NotPersuadedSection(_SectionBase):
    factual_claims: list[FactualClaim] = Field(default_factory=list)


class MarketAcceptanceRequirementSection(_SectionBase):
    factual_claims: list[FactualClaim] = Field(default_factory=list)


class SectionAOut(BaseModel):
    """Wrapper for Call A's structured output."""

    model_config = ConfigDict(extra="forbid")

    public_opinion_sentiment: PublicOpinionSentimentSection
    persuaded: PersuadedSection
    not_persuaded: NotPersuadedSection
    market_acceptance_requirement: MarketAcceptanceRequirementSection


# Sections 5–6 — Call B
class CompetitorMention(BaseModel):
    """One competitor in the analysis. `competitor_name` MUST come from a
    real `competitor_evidence` item (validator rejects invented names)."""

    model_config = ConfigDict(extra="forbid")

    competitor_name: str
    comparison_summary: str
    evidence_anchors: list[UUID] = Field(default_factory=list)
    factual_claims: list[FactualClaim] = Field(default_factory=list)


class ProductTrajectorySection(_SectionBase):
    pass


class CompetitorAnalysisSection(_SectionBase):
    competitors: list[CompetitorMention] = Field(default_factory=list)


class SectionBOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_trajectory: ProductTrajectorySection
    competitor_analysis: CompetitorAnalysisSection


# Section 7 — Call C
class TargetAudienceSection(_SectionBase):
    pass


class PositioningSection(_SectionBase):
    factual_claims: list[FactualClaim] = Field(default_factory=list)


class PriceStructureSection(_SectionBase):
    factual_claims: list[FactualClaim] = Field(default_factory=list)


class SectionCOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_audience: TargetAudienceSection
    positioning: PositioningSection
    price_structure: PriceStructureSection


# Sections 8–9 are mechanical — pure dataclasses produced by mechanical.py.


class StanceCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stance: str
    count: int


class DebateShiftMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_number: int
    from_stance: str
    to_stance: str
    count: int
    triggered_by: str | None = None
    debate_turn_id: UUID | None = None
    speaker_agent_id: UUID | None = None
    target_agent_id: UUID | None = None
    example_argument: str | None = None


class DebateShiftMarkersSection(BaseModel):
    """Section 8 — fully mechanical, pulled from debate_turns + agent_responses
    shifts. No LLM. Every entry references real ids."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    markers: list[DebateShiftMarker] = Field(default_factory=list)
    rounds_with_shifts: list[int] = Field(default_factory=list)


class SplitConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    largest_bucket_stance: str
    largest_bucket_count: int
    second_bucket_stance: str | None
    second_bucket_count: int
    separation_ratio: float  # largest / total
    entropy_round_1: float
    entropy_round_7: float
    interpretation: str  # "narrow" | "split" | "broad" — qualitative label


class EvidenceLedgerCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direct_count: int
    analogical_count: int
    missing_count: int


class MissingEvidenceLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    node_class: str
    summary: str  # short text from content / metadata


class ClaimTraceabilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    claim_text: str
    source_evidence_id: UUID
    source_url: str | None
    source_excerpt: str
    claim_type: ClaimType
    basis: ClaimBasis


class EvidenceLedgerSection(BaseModel):
    """Section 9b — mechanical evidence ledger sourced from the graph
    service. Surfaces direct + analogical + missing counts and a
    claim-by-claim traceability map."""

    model_config = ConfigDict(extra="forbid")

    counts: EvidenceLedgerCounts
    missing: list[MissingEvidenceLedgerEntry] = Field(default_factory=list)
    claim_traceability: list[ClaimTraceabilityEntry] = Field(default_factory=list)


class ConfidenceSection(BaseModel):
    """Section 9a — split confidence + qualitative narrative."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    split_confidence: SplitConfidence
    stance_distribution_by_round: list[list[StanceCount]] = Field(default_factory=list)
