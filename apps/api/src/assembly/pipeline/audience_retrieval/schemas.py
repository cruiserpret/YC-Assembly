"""Phase 8.2H — schemas for run-scoped audience retrieval.

A `RunScopedAudienceRetrievalResult` answers the seven framework
questions for one (brief × plan × existing-persona-pool) tuple:

  1. which existing personas are relevant to this product brief?
  2. which stakeholder category does each persona best match?
  3. which categories are well-covered?
  4. which categories are missing or thin?
  5. is the audience tiny / small / serious ready?
  6. if not ready, what top-up queries should be run?
  7. what caveats should the user see before simulation?

All fields are Pydantic with `extra='forbid'` to prevent silent shape
drift.
"""
from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from assembly.pipeline.persona_relevance.rubric import RelevanceClassification


# ---------------------------------------------------------------------------
# Coverage label — closed enum
# ---------------------------------------------------------------------------


class CategoryCoverageLabel(str, enum.Enum):
    MISSING = "missing"                          # 0 matched
    THIN = "thin"                                # < tiny target
    ACCEPTABLE_FOR_TINY = "acceptable_for_tiny"
    ACCEPTABLE_FOR_SMALL = "acceptable_for_small"
    ACCEPTABLE_FOR_SERIOUS = "acceptable_for_serious"


class NextStepRecommendation(str, enum.Enum):
    PROCEED_TO_TINY_SIMULATION = "A_proceed_to_tiny_simulation"
    PROCEED_TO_SMALL_SIMULATION = "B_proceed_to_small_simulation"
    PROCEED_TO_SERIOUS_SIMULATION = "C_proceed_to_serious_simulation"
    RUN_TOPUP_INGESTION_FIRST = "D_run_topup_ingestion_first"
    HOLD_FOR_COMPLIANCE_REVIEW = "E_hold_for_compliance_review"


# ---------------------------------------------------------------------------
# Match-level
# ---------------------------------------------------------------------------


class PersonaMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str
    display_name: str
    matched_category_key: str
    matched_category_display_name: str
    relevance_score: int = Field(ge=-20, le=45)
    classification: RelevanceClassification
    matched_signals: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
    evidence_link_count: int = Field(ge=0)
    source_domains: list[str] = Field(default_factory=list)
    why_included: str
    caveats: list[str] = Field(default_factory=list)
    # Phase 8.4A.4 — production-wired market-entry inclusion gate.
    # All optional with sensible defaults so classic launched-product
    # callers (Amboras-style) keep their existing PersonaMatch shape.
    # Only populated when retrieve_personas_for_target_society routed
    # through the market-entry path.
    final_tier: str | None = None
    base_tier: str | None = None
    gate_reason: str | None = None
    anchor_has: bool = False
    anchor_types: list[str] = Field(default_factory=list)
    matched_anchor_terms: list[str] = Field(default_factory=list)
    anchor_evidence_excerpts: list[str] = Field(default_factory=list)


class PersonaExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str
    display_name: str
    exclusion_reason: str
    best_possible_category: str | None = None
    score: int
    # Phase 8.4A.4 — populated only when the market-entry gate routed
    # this exclusion. For classic-path exclusions these stay None / [].
    base_tier: str | None = None
    final_tier: str | None = None
    gate_reason: str | None = None
    anchor_has: bool = False
    anchor_types: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


class CategoryCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str
    display_name: str
    priority: Literal["high", "medium", "low"]
    required_min_tiny: int
    required_min_small: int
    required_min_serious: int
    matched_highly_relevant: int = Field(ge=0)
    matched_relevant: int = Field(ge=0)
    matched_weak: int = Field(ge=0)
    matched_total: int = Field(ge=0)
    coverage_label: CategoryCoverageLabel
    missing_signals: list[str] = Field(default_factory=list)
    recommended_topup_queries: list[str] = Field(default_factory=list)


class SourceDiversitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distinct_source_domains: int = Field(ge=0)
    domains: list[str] = Field(default_factory=list)
    minimum_required: int = Field(ge=0)
    single_source_risk: bool


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


class ReadinessByMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tiny_ready: bool
    small_ready: bool
    serious_ready: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-up
# ---------------------------------------------------------------------------


class TopUpRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stakeholder_category_key: str = Field(min_length=1, max_length=64)
    reason_for_topup: str = Field(min_length=1)
    missing_signals: list[str] = Field(default_factory=list)
    suggested_queries: list[str] = Field(min_length=1)
    preferred_source_types: list[str] = Field(default_factory=list)
    max_records_suggested: int = Field(ge=1, le=200)
    expected_persona_yield_range: str
    caveats: list[str] = Field(default_factory=list)
    requires_extra_compliance_review: bool = False


# ---------------------------------------------------------------------------
# Top-level result
# ---------------------------------------------------------------------------


class RunScopedAudienceRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief_summary: str
    target_society_plan_summary: str
    matched_personas: list[PersonaMatch] = Field(default_factory=list)
    excluded_personas: list[PersonaExclusion] = Field(default_factory=list)
    category_coverage: list[CategoryCoverage]
    source_diversity_summary: SourceDiversitySummary
    readiness_by_mode: ReadinessByMode
    topup_recommendations: list[TopUpRecommendation] = Field(default_factory=list)
    warnings_and_caveats: list[str] = Field(default_factory=list)
    next_step_recommendation: NextStepRecommendation
