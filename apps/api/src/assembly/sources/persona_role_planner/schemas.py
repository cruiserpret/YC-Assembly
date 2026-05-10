"""Phase 8.5D.1 — schemas for the persona-candidate planner.

`extra="forbid"` discipline. Closed-set Literals for every label.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProductLaunchState = Literal["unlaunched", "launched", "in_market"]
PersonaCandidateConfidence = Literal["high", "medium", "low"]
EvidenceStrengthLabel = Literal["very_strong", "strong", "moderate", "weak"]
RejectionReason = Literal[
    "no_source_evidence",
    "single_token_only_evidence",
    "duplicate_role_and_evidence",
    "fabricated_unlaunched_target_product_use",
    "below_min_traits",
    "below_min_role_evidence",
    "global_template_shape",
]


class EffectiveSourceRecord(BaseModel):
    """One row in the lineage-aware effective source pool. Either an
    original 8.5C.2 preview row (when no companion exists) OR an
    8.5C.4 full-text companion row (when one exists, replacing the
    preview)."""

    model_config = ConfigDict(extra="forbid")

    source_record_id: str
    effective_kind: Literal[
        "preview_used_as_is",       # original 8.5C.2 row, no companion needed
        "preview_used_thin",        # USABLE_BUT_THIN, no companion exists
        "fulltext_companion_used",  # 8.5C.4 row, supersedes a preview
    ]
    superseded_preview_source_record_id: str | None = None
    parent_asin: str | None
    asin: str | None
    category: str
    metadata_title: str | None
    rating: float | None
    verified_purchase: bool | None
    helpful_vote: int | None
    timestamp: int | None
    content_length: int
    content: str  # FULL content (whatever's in source_records.content)
    metadata: dict[str, Any]


class InferredPersonaTrait(BaseModel):
    """One trait inferred from a single source. Source-supported."""

    model_config = ConfigDict(extra="forbid")

    trait_name: str
    trait_value: str
    evidence_source_record_id: str
    evidence_excerpt: str  # short, ≤ 240 chars
    confidence: PersonaCandidateConfidence
    caveat: str | None = None


class PersonaCandidate(BaseModel):
    """One brief-scoped, run-scoped, evidence-backed persona candidate.

    DRY-RUN ONLY artifact. Never written to the DB. Carries explicit
    `scope`, `persistence_status`, `target_brief`, and
    `not_global_persona` fields so any future code path that touches
    this struct cannot mistake it for a global persona row.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    scope: Literal["brief_scoped"] = "brief_scoped"
    persistence_status: Literal["dry_run_only"] = "dry_run_only"
    target_brief: str
    generated_for_phase: str
    not_global_persona: Literal[True] = True

    # Inferred role(s)
    inferred_persona_role: str  # e.g. "competitor_user_celsius"
    secondary_persona_roles: list[str] = Field(default_factory=list)
    role_inference_basis: list[str]  # short evidence-tied reasons
    segment_label: str  # human-readable

    # Evidence lineage
    source_record_ids: list[str] = Field(min_length=1)
    superseded_preview_source_record_ids: list[str] = Field(
        default_factory=list,
    )
    evidence_summary: str
    evidence_snippets: list[str] = Field(min_length=1)

    # Inferred persona shape
    inferred_traits: list[InferredPersonaTrait]
    inferred_preferences: list[str]
    inferred_objections: list[str]
    inferred_behaviors: list[str]
    hypothetical_target_product_reaction: str

    # Quality signals
    confidence: PersonaCandidateConfidence
    evidence_strength: EvidenceStrengthLabel
    caveats: list[str]
    simulation_usefulness_summary: str
    persistence_recommendation: Literal[
        "PERSIST_IN_8_5D_2", "DEFER", "DROP",
    ]


class PersonaCandidateRejection(BaseModel):
    """One rejected candidate idea — recorded in the audit so the
    operator can see what was discarded and why."""

    model_config = ConfigDict(extra="forbid")

    rejected_idea_label: str
    source_record_ids: list[str]
    rejection_reason: RejectionReason
    explanation: str


class LaunchStateClaimValidationResult(BaseModel):
    """Result of running the universal launch-state-aware claim
    validator over a candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    launch_state: ProductLaunchState
    forbidden_phrases_matched: list[str]
    is_valid: bool
    rejection_reason: RejectionReason | None = None


class PersonaRolePlan(BaseModel):
    """The full audit artifact for one phase 8.5D.1 dry-run."""

    model_config = ConfigDict(extra="forbid")

    target_brief_id: str
    product_name: str
    launch_state: ProductLaunchState
    generated_for_phase: str
    plan_id: str  # sha256 of inputs
    role_inference_method: Literal["deterministic", "llm", "hybrid"]

    # Source selection
    preview_rows_found: int
    companion_rows_found: int
    superseded_preview_rows_excluded: list[str]
    effective_source_records_count: int
    effective_source_record_ids: list[str]

    # Role inference
    inferred_roles: list[str]
    evidence_basis_by_role: dict[str, list[str]]  # role → snippet ids
    rejected_role_ideas: list[str]

    # Candidate generation
    persona_candidates: list[PersonaCandidate]
    rejected_candidate_ideas: list[PersonaCandidateRejection]

    # Validation
    launch_state_validation_results: list[LaunchStateClaimValidationResult]

    # Distribution
    persona_role_distribution: dict[str, int]
    evidence_coverage_summary: dict[str, Any]

    # Provenance
    caveats: list[str]
    generated_at: str

    # Outcome
    recommendation: str
    ready_for_8_5d_2: bool
