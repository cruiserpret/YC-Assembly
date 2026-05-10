"""Phase 8.2G — Pydantic schemas for the target-society planner."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from assembly.pipeline.target_society.constants import (
    ProductFamily,
    SimulationGoal,
    WarningSeverity,
)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class ProductBriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(min_length=1, max_length=200)
    product_type: str | None = Field(default=None, max_length=200)
    product_description: str = Field(min_length=1, max_length=4000)
    price_or_price_structure: str | None = Field(default=None, max_length=200)
    competitors: list[str] = Field(default_factory=list, max_length=20)
    target_market_or_society: str | None = Field(default=None, max_length=2000)
    geography: str | None = Field(default=None, max_length=200)
    intended_user_or_buyer: str | None = Field(default=None, max_length=400)
    optional_url: str | None = Field(default=None, max_length=400)
    extra_context: str | None = Field(default=None, max_length=4000)
    simulation_goal: SimulationGoal | None = None


# ---------------------------------------------------------------------------
# Stakeholder category
# ---------------------------------------------------------------------------


_PRIORITIES = ("high", "medium", "low")


class StakeholderCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    why_relevant: str = Field(min_length=1, max_length=1000)
    likely_pains: list[str] = Field(default_factory=list)
    likely_objections: list[str] = Field(default_factory=list)
    likely_current_alternatives: list[str] = Field(default_factory=list)
    evidence_needed: list[str] = Field(min_length=1)
    source_query_themes: list[str] = Field(default_factory=list)
    inclusion_signals: list[str] = Field(min_length=1)
    exclusion_signals: list[str] = Field(min_length=1)
    minimum_persona_target_tiny: int = Field(ge=0, le=200)
    minimum_persona_target_small: int = Field(ge=0, le=400)
    minimum_persona_target_serious: int = Field(ge=0, le=1000)
    sensitivity_or_compliance_notes: str | None = None
    priority: Literal["high", "medium", "low"] = "medium"


# ---------------------------------------------------------------------------
# Source query plan
# ---------------------------------------------------------------------------


class SourceQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str = Field(min_length=1, max_length=64)
    queries: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    discussion_targets: list[str] = Field(default_factory=list)
    review_forum_patterns: list[str] = Field(default_factory=list)
    competitor_queries: list[str] = Field(default_factory=list)
    pricing_queries: list[str] = Field(default_factory=list)
    geography_queries: list[str] = Field(default_factory=list)
    forbidden_queries: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Persona retrieval plan
# ---------------------------------------------------------------------------


class PersonaRetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trait_fields_to_match: list[str] = Field(default_factory=list)
    relevance_signals: list[str] = Field(default_factory=list)
    exclusion_rules: list[str] = Field(default_factory=list)
    minimum_relevance_threshold: int = Field(ge=0, le=45)
    use_existing_personas_when: list[str] = Field(default_factory=list)
    trigger_topup_when: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Coverage requirements
# ---------------------------------------------------------------------------


class CoverageRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_categories_represented: int = Field(ge=1, le=50)
    minimum_strong_persona_signal_shells: int = Field(ge=0, le=1000)
    minimum_source_diversity_domains: int = Field(ge=1, le=200)
    minimum_direct_inferred_traits_per_persona: int = Field(ge=1, le=20)
    geography_coverage_required: bool
    competitor_evidence_required: bool
    price_evidence_required: bool
    trust_objection_evidence_required: bool


# ---------------------------------------------------------------------------
# Simulation readiness gates
# ---------------------------------------------------------------------------


class SimulationReadinessGates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tiny_minimum_personas: int = Field(ge=1, le=200)
    small_minimum_personas: int = Field(ge=1, le=500)
    serious_minimum_personas: int = Field(ge=1, le=2000)
    scaled_minimum_personas: int = Field(ge=1, le=10000)
    block_if_single_source: bool
    block_if_key_category_missing: bool
    block_if_thin_geography: bool
    block_if_no_competitor_evidence: bool
    allow_tiny_mode_with_caveat: bool


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


class SocietyPlanWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    severity: WarningSeverity


# ---------------------------------------------------------------------------
# Expected outputs
# ---------------------------------------------------------------------------


class ExpectedOutputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerable_questions: list[str] = Field(default_factory=list)
    unanswerable_questions: list[str] = Field(default_factory=list)
    expected_report_sections: list[str] = Field(default_factory=list)
    expected_society_map_categories: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Interpreted brief
# ---------------------------------------------------------------------------


class InterpretedBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_summary: str = Field(min_length=1, max_length=2000)
    target_market_interpretation: str = Field(min_length=1, max_length=2000)
    competitor_interpretation: str = Field(min_length=1, max_length=2000)
    price_context: str | None = None
    geography_context: str | None = None
    detected_product_family: ProductFamily
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Full plan
# ---------------------------------------------------------------------------


class TargetSocietyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interpreted_brief: InterpretedBrief
    stakeholder_categories: list[StakeholderCategory] = Field(min_length=1)
    source_query_plan: list[SourceQueryPlan] = Field(min_length=1)
    persona_retrieval_plan: PersonaRetrievalPlan
    coverage_requirements: CoverageRequirements
    simulation_readiness_gates: SimulationReadinessGates
    expected_outputs: ExpectedOutputs
    warnings_and_limitations: list[SocietyPlanWarning] = Field(default_factory=list)
    # Phase 8.2J — derived weighted-scorer weights. None means
    # backwards-compat uniform weights (Phase 8.2H behavior). When
    # populated, the audience-retrieval scorer multiplies each axis's
    # 0–5 sub-score by the corresponding weight before summing. The
    # weight vector ALWAYS sums to 8.0 (so max total stays at 40, the
    # band the 27 / 36 thresholds were calibrated for).
    scorer_weights: dict[str, float] | None = None
