"""The 9 output sections.

Subjective language is NOT enforced at the type level — the deterministic output
validator in `assembly.pipeline.aggregation.validator` runs across these fields
after generation. We keep the types loose here so the validator can flag
violations as a separate, regeneratable pass."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------- 1. Subjective Public Opinion Sentiment ---------------------------


class PublicOpinionSentiment(BaseModel):
    overall_interpretation: str
    market_mood: str = Field(
        ..., description="Free-text, e.g. 'cautiously interested', 'skeptical', 'curious'."
    )
    most_receptive_segment: str
    most_resistant_segment: str
    subjective_summary: str


# ---------- 2. Persuasion analysis ------------------------------------------


class PersuasionAnalysis(BaseModel):
    top_reasons_people_were_persuaded: list[str] = Field(default_factory=list)
    top_reasons_people_were_not_persuaded: list[str] = Field(default_factory=list)


# ---------- 3. Market acceptance requirement --------------------------------


class MarketAcceptanceRequirement(BaseModel):
    one_thing_needed: str
    why_it_matters: str


# ---------- 4. Product trajectory -------------------------------------------


class ProductTrajectory(BaseModel):
    trajectory: str
    summary: str


# ---------- 5. Competitor analysis ------------------------------------------


class CompetitorAnalysis(BaseModel):
    direct_competitors: list[str] = Field(default_factory=list)
    indirect_alternatives: list[str] = Field(default_factory=list)
    stronger_than_competitors: list[str] = Field(default_factory=list)
    weaker_than_competitors: list[str] = Field(default_factory=list)
    pricing_implication: str


# ---------- 6. Recommendations -----------------------------------------------


class Recommendations(BaseModel):
    target_audience: str
    market_positioning: str
    price_structure: str


# ---------- 7. Debate shift markers -----------------------------------------


class DebateShiftMarker(BaseModel):
    description: str
    segment_affected: str
    shifted_from: str
    shifted_to: str
    triggered_by: str


# ---------- 8. Split confidence ---------------------------------------------

ConfidenceLevel = Literal["low", "medium", "high"]


class SplitConfidence(BaseModel):
    reaction: ConfidenceLevel
    segment: ConfidenceLevel
    configuration: ConfidenceLevel
    numeric_forecast: ConfidenceLevel = Field(
        default="low",
        description="Defaults to 'low'. Assembly does not emit numeric forecasts in V0.",
    )


# ---------- 9. Evidence ledger ----------------------------------------------


class EvidenceLedger(BaseModel):
    direct_evidence: list[str] = Field(default_factory=list)
    analogical_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


# ---------- Top-level report -------------------------------------------------


class SimulationReport(BaseModel):
    """Returned by `GET /simulations/{id}` once status='completed'."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None = None

    public_opinion_sentiment: PublicOpinionSentiment | None = None
    persuasion_analysis: PersuasionAnalysis | None = None
    market_acceptance_requirement: MarketAcceptanceRequirement | None = None
    product_trajectory: ProductTrajectory | None = None
    competitor_analysis: CompetitorAnalysis | None = None
    recommendations: Recommendations | None = None
    debate_shift_markers: list[DebateShiftMarker] = Field(default_factory=list)
    confidence: SplitConfidence | None = None
    evidence_ledger: EvidenceLedger | None = None

    # Operational
    validator_passed: bool = True
    validator_notes: dict = Field(default_factory=dict)
    schema_version: str = "v0.1"


class SimulationStatus(BaseModel):
    """Lightweight polling response while a simulation is running."""

    id: UUID
    status: Literal[
        "pending", "parsing", "evidence_building", "society_building",
        "simulating", "simulation_completed", "aggregating", "reported",
        "failed",
    ]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict | None = None
    failed_stage: str | None = None
    progress: dict | None = None  # serialized Progress object
    current_round: int | None = Field(
        default=None, description="1..7 while simulating; None before/after."
    )
