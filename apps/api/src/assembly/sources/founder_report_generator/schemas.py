"""Phase 8.5F — schemas for the founder-facing report.

`extra="forbid"` discipline. Closed-set Literals where state branches.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SeverityLabel = Literal["high", "medium", "low"]


class ObjectionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=4)
    explanation: str
    raised_by_personas: list[str]  # display_names
    raised_by_roles: list[str]  # normalized_primary_roles
    evidence_basis: list[str]  # excerpts / source_record_ids
    severity: SeverityLabel
    raised_count: int
    founder_action: str


class PersuasionLeverEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=4)
    why_it_matters: str
    likely_movable_personas: list[str]
    suggested_founder_change: str
    raised_count: int


class CompetitorComparisonEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competitor: str
    mention_count: int
    simulated_strengths: list[str]
    simulated_weaknesses: list[str]
    where_target_product_could_differentiate: list[str]


class ProofNeededEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proof_kind: str  # short label: texture / sweat / runner / price / etc.
    description: str
    suggested_founder_assets: list[str]


class PositioningRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    angle_label: str
    rationale: str
    target_personas: list[str]
    test_idea: str


class ProductOfferRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: Literal[
        "claim_clarity", "packaging", "trial_size",
        "price_or_offer", "proof_assets", "use_case_messaging",
        "competitor_differentiation",
    ]
    suggestion: str
    triggered_by: list[str]  # objections / levers


class TestRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_label: str
    description: str
    expected_signal: str


class PersonaSnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    normalized_primary_role: str
    evidence_theme: str
    source_provider_family: str
    compressed_candidate_id: str
    why_included: str
    final_stance: str | None
    trait_count: int
    evidence_link_count: int
    source_record_count: int


class AppendixSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_to_evidence_map: list[dict[str, Any]]
    round_summary: list[dict[str, Any]]
    quality_scores: dict[str, Any]
    forbidden_claim_audit: dict[str, Any]
    source_persona_traceability: dict[str, Any]


class FounderReport(BaseModel):
    """Top-level founder-facing report artifact."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["8_5f_founder_report_generation"] = (
        "8_5f_founder_report_generation"
    )
    completed_at: str
    simulation_id: str
    run_scope_id: str
    target_brief_id: str
    product_name: str
    launch_state: str

    founder_brief: dict[str, Any]
    input_summary: dict[str, Any]

    executive_summary: list[str]
    simulated_audience_snapshot: list[PersonaSnapshotEntry]
    stance_distribution: dict[str, int]
    overall_reaction: list[str]

    top_objections: list[ObjectionEntry]
    top_persuasion_levers: list[PersuasionLeverEntry]
    competitor_comparison: list[CompetitorComparisonEntry]
    proof_needed: list[ProofNeededEntry]
    positioning_recommendations: list[PositioningRecommendation]
    product_offer_recommendations: list[ProductOfferRecommendation]
    what_to_test_next: list[TestRecommendation]

    caveats: list[str]
    appendix: AppendixSection

    source_traceability: dict[str, Any]
    persona_traceability: dict[str, Any]
    quality_reference: dict[str, Any]
    forbidden_claim_audit: dict[str, Any]
    security_redaction_audit: dict[str, Any]

    ready_for_fresh_end_to_end_test: bool
    rationale: list[str]
