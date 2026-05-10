"""Phase 8.5C.1 — schemas for the dynamic ingestion-policy planner.

All schemas use `extra="forbid"` discipline. Closed-set string
literals are constrained via Pydantic.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProductLaunchState = Literal["unlaunched", "launched", "in_market"]
EvidenceStrengthLabel = Literal[
    "very_strong", "strong", "moderate", "weak",
]
SourceRelevanceLabel = Literal["primary", "secondary", "off_brief"]
PersonaValueLabel = Literal["high", "medium", "low", "none"]
DuplicateCheckLabel = Literal["unique", "duplicate", "skipped"]
PolicyGeneratedFromLabel = Literal["deterministic", "llm", "hybrid"]
DecisionLabel = Literal["SELECTED", "REJECTED"]


class PoolSummary(BaseModel):
    """Aggregate summary of the candidate pool, computed once."""

    model_config = ConfigDict(extra="forbid")

    total_candidates: int
    candidates_by_category: dict[str, int]
    candidates_by_confidence: dict[str, int]
    score_p25: float
    score_p50: float
    score_p75: float
    score_max: float
    distinct_competitor_brands_seen: list[str]
    distinct_substitute_terms_seen: list[str]
    distinct_metadata_main_categories_seen: list[str]


class SelectionRule(BaseModel):
    """One declarative selection rule. Human-readable + machine-actionable."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    description: str
    derived_from: Literal[
        "founder_brief", "evidence_anchor_plan",
        "candidate_pool_distribution", "universal_safety",
    ]
    weight: int  # +1..+5; higher = stronger preference


class RejectionRule(BaseModel):
    """One declarative rejection rule."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    description: str
    derived_from: Literal[
        "founder_brief", "evidence_anchor_plan",
        "candidate_pool_distribution", "universal_safety",
    ]
    is_universal: bool


class PlannedSourceRecordPreview(BaseModel):
    """The exact source_record shape that WOULD be inserted if the
    operator approves Phase 8.5C.2 execution. Audit-only — never
    inserted by 8.5C.1."""

    model_config = ConfigDict(extra="forbid")

    source_kind: str
    source_url: str
    content_preview: str  # first 240 chars, for audit
    content_length: int
    content_hash: str  # sha256 hex of full normalized content
    language: str | None
    metadata: dict[str, Any]
    ingested_by: str
    compliance_tag: Literal[
        "public_api", "public_html", "open_dataset",
        "open_aggregate", "manual_seed",
    ]
    captured_at: str  # ISO8601 UTC
    pii_redaction_status: str
    sensitive_scan_status: str
    user_handle_hash: str | None


class CandidateRow(BaseModel):
    """One candidate review reconstructed from the dynamic-anchor
    preflight pipeline. Everything the policy planner needs to make
    a decision on this candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str  # category::parent_asin (stable, dedup-friendly)
    category: str
    parent_asin: str | None
    asin: str | None
    rating: float | None
    verified_purchase: bool | None
    helpful_vote: int | None
    timestamp: int | None
    title: str
    text: str
    user_id_hash: str | None  # always pre-hashed by adapter
    score: int
    confidence: Literal[
        "high_confidence", "medium_confidence",
        "low_confidence", "rejected",
    ]
    matched_terms: list[str]
    denylist_hits: list[str]
    metadata_title: str | None
    metadata_main_category: str | None
    metadata_categories: list[str]


class CandidateDecision(BaseModel):
    """The planner's decision for one candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    decision: DecisionLabel
    selection_rank: int | None  # 1..max_insert_cap if SELECTED
    evidence_strength_label: EvidenceStrengthLabel
    source_relevance_label: SourceRelevanceLabel
    persona_value_label: PersonaValueLabel
    selected_for_persona_roles: list[str]
    decision_reasons: list[str]
    rejection_reasons: list[str]
    scanner_results: dict[str, list[str]]  # scanner_name → issues[]
    duplicate_check: DuplicateCheckLabel
    planned_source_record_preview: PlannedSourceRecordPreview | None


class IngestionPolicy(BaseModel):
    """Per-(brief, candidate-pool) ingestion policy. Audit-only artifact
    — never persisted into the DB. The 8.5C.2 execution phase reads
    this artifact + applies it inside a single bounded transaction
    with rollback on any scanner failure."""

    model_config = ConfigDict(extra="forbid")

    product_name: str
    target_brief_id: str
    source_family: str
    product_launch_state: ProductLaunchState
    evidence_anchor_plan_id: str
    policy_id: str  # sha256 of inputs — stable across runs
    policy_generated_from: PolicyGeneratedFromLabel

    candidate_pool_summary: PoolSummary
    selection_objectives: list[str]
    evidence_quality_dimensions: list[str]
    persona_construction_value_dimensions: list[str]
    dynamic_selection_rules: list[SelectionRule]
    dynamic_rejection_rules: list[RejectionRule]
    universal_guardrails: list[str]
    max_insert_cap: int = Field(ge=1, le=200)
    required_scanners: list[str]
    source_record_shape: dict[str, Any]
    caveats: list[str]
    generated_at: str  # ISO8601 UTC
