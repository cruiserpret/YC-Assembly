"""Phase 8.5D.1E — schemas for persona-set compression.

`extra="forbid"` discipline. Closed-set Literals.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CompressionRejectionReason = Literal[
    "duplicate_role_and_theme",
    "duplicate_role_and_traits",
    "duplicate_role_and_objections",
    "duplicate_role_and_provider",
    "below_quality_floor",
    "fake_target_product_use",
    "missing_evidence",
    "below_min_traits",
    "weaker_than_kept_candidate",
    "non_brief_scoped_or_global_persona",
    "hard_cap_overflow",
]


class CompressionPolicy(BaseModel):
    """Universal selection / rejection rules. Echoed in audit so the
    operator can see exactly what the compressor enforced — and
    diff between runs."""

    model_config = ConfigDict(extra="forbid")

    grouping_dimensions: list[str]
    selection_rules: list[str]
    rejection_rules: list[str]
    max_target_range: tuple[int, int] = Field(
        default=(6, 8),
        description="Soft target. Quality always beats count.",
    )
    quality_floor: dict[str, Any]
    min_behavioral_differential_for_second_same_role: int = 2


class CompressedPersonaCandidate(BaseModel):
    """One candidate kept in the compressed mini-society set.

    Mirrors the persona_role_planner.PersonaCandidate shape but with
    explicit `pre_normalization_role` + `kept_reason` audit fields.
    The candidate is still BRIEF-SCOPED, RUN-SCOPED, DRY-RUN-ONLY.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    scope: Literal["brief_scoped"] = "brief_scoped"
    persistence_status: Literal["dry_run_only"] = "dry_run_only"
    target_brief: str
    generated_for_phase: str
    not_global_persona: Literal[True] = True

    pre_normalization_role: str
    normalized_primary_role: str
    secondary_persona_roles: list[str] = Field(default_factory=list)
    role_inference_basis: list[str]
    segment_label: str

    source_record_ids: list[str] = Field(min_length=1)
    evidence_summary: str
    evidence_snippets: list[str] = Field(min_length=1)
    evidence_theme: str  # primary competitor / substitute / use-case bucket
    source_provider_family: str  # brave_search / youtube_data_api / amazon_reviews_2023_local

    inferred_traits: list[dict[str, Any]] = Field(min_length=2)
    inferred_preferences: list[str]
    inferred_objections: list[str]
    inferred_behaviors: list[str]
    hypothetical_target_product_reaction: str

    confidence: Literal["high", "medium", "low"]
    evidence_strength: Literal["very_strong", "strong", "moderate", "weak"]
    quality_score: float
    caveats: list[str]
    simulation_usefulness_summary: str
    persistence_recommendation: Literal[
        "PERSIST_IN_8_5D_2", "DEFER", "DROP",
    ]
    kept_reason: str  # why this candidate survived compression


class CompressionRejection(BaseModel):
    """One candidate that was dropped during compression."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    pre_normalization_role: str
    normalized_primary_role: str
    rejection_reason: CompressionRejectionReason
    rejection_explanation: str
    stronger_candidate_kept_id: str | None = None


class CompressionDiffSummary(BaseModel):
    """Compact pre/post snapshot for the audit report."""

    model_config = ConfigDict(extra="forbid")

    before_count: int
    after_count: int
    rejected_count: int
    roles_before: list[str]
    roles_after: list[str]
    duplicate_role_clusters_before: int
    duplicate_role_clusters_after: int
    provider_families_before: list[str]
    provider_families_after: list[str]
    diversity_score_before: float
    diversity_score_after: float
    competitor_concentration_before: float
    competitor_concentration_after: float


class CompressedPersonaSet(BaseModel):
    """Top-level compressor artifact."""

    model_config = ConfigDict(extra="forbid")

    target_brief_id: str
    product_name: str
    launch_state: Literal["unlaunched", "launched", "in_market"]
    generated_for_phase: str
    plan_id: str  # sha256 of inputs

    policy: CompressionPolicy
    compressed_candidates: list[CompressedPersonaCandidate]
    rejected_candidates: list[CompressionRejection]
    diff_summary: CompressionDiffSummary

    rationale: list[str]
    caveats: list[str]
    generated_at: str  # ISO8601 UTC
