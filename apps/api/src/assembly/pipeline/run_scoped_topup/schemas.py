"""Phase 8.2I — schemas for the run-scoped top-up loop."""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from assembly.pipeline.audience_retrieval.schemas import (
    NextStepRecommendation,
)


# ---------------------------------------------------------------------------
# Plan — produced by the dry-run path; consumed by the live path.
# ---------------------------------------------------------------------------


class RunScopedTopUpPlan(BaseModel):
    """Concrete plan for one run-scoped top-up loop.

    Built by `build_topup_plan_from_audience_retrieval` from a Phase
    8.2H `RunScopedAudienceRetrievalResult`. The plan is the durable
    contract the live executor consumes; the operator script can
    inspect / approve / modify it before any live run.
    """

    model_config = ConfigDict(extra="forbid")

    brief_label: str = Field(min_length=1, max_length=64)
    target_categories: list[str] = Field(min_length=1, max_length=30)
    queries_by_category: dict[str, list[str]]
    # Phase 8.2I.1 raised the per-plan total-query ceiling from 15 to
    # 30 to accommodate the Amboras refined retry (6 categories × 5
    # queries).
    total_queries: int = Field(ge=1, le=30)
    max_queries_per_category: int = Field(ge=1, le=10)
    max_total_queries: int = Field(ge=1, le=30)
    max_results_per_query: int = Field(ge=1, le=10)
    max_accepted_records: int = Field(ge=1, le=200)
    max_content_chars: int = Field(ge=500, le=8000)
    persona_write_cap: int = Field(ge=1, le=300)
    cost_cap_usd: Decimal
    sensitive_caveats: list[str] = Field(default_factory=list)
    requires_compliance_approval: bool
    # Phase 8.2I.1 — operator label for the query-refinement pass.
    # `None` means a default (audience-retrieval-driven) plan; non-None
    # means an explicit refined catalog (e.g. "8.2I.1") is in use.
    query_refinement_version: str | None = None


# ---------------------------------------------------------------------------
# Per-stage execution results
# ---------------------------------------------------------------------------


class TopUpExecutionResult(BaseModel):
    """Result of the Tavily top-up ingest stage."""

    model_config = ConfigDict(extra="forbid")

    fetched_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    deduped_count: int = Field(ge=0)
    accepted_by_category: dict[str, int] = Field(default_factory=dict)
    new_source_record_ids: list[str] = Field(default_factory=list)
    rejected_reason_codes: dict[str, int] = Field(default_factory=dict)
    accepted_source_domains: dict[str, int] = Field(default_factory=dict)
    runtime_seconds: float = Field(ge=0.0)
    live_network_used: bool


class TopUpPersonaWriteResult(BaseModel):
    """Result of the persona-construction stage on the new shells."""

    model_config = ConfigDict(extra="forbid")

    candidate_shells: int = Field(ge=0)
    strong_signal_shells: int = Field(ge=0)
    weak_signal_shells: int = Field(ge=0)
    context_only_shells: int = Field(ge=0)
    personas_created: int = Field(ge=0)
    personas_skipped: int = Field(ge=0)
    traits_created: int = Field(ge=0)
    traits_rejected: int = Field(ge=0)
    evidence_links_created: int = Field(ge=0)
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    new_persona_ids: list[str] = Field(default_factory=list)
    cost_estimate_usd: float | None = None
    cost_actual_usd: float | None = None


# ---------------------------------------------------------------------------
# Re-audit — before / after coverage delta
# ---------------------------------------------------------------------------


class CategoryBeforeAfter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str
    display_name: str
    before_matched: int = Field(ge=0)
    after_matched: int = Field(ge=0)
    delta: int
    coverage_label_before: str
    coverage_label_after: str


class RunScopedReauditResult(BaseModel):
    """Compares Phase 8.2H audience retrieval result before & after the
    top-up. Tracks readiness flips, per-category delta, new caveats."""

    model_config = ConfigDict(extra="forbid")

    before_matched_count: int = Field(ge=0)
    after_matched_count: int = Field(ge=0)
    matched_delta: int
    before_tiny_ready: bool
    after_tiny_ready: bool
    before_small_ready: bool
    after_small_ready: bool
    before_serious_ready: bool
    after_serious_ready: bool
    per_category: list[CategoryBeforeAfter]
    new_caveats: list[str] = Field(default_factory=list)
    remaining_missing_categories: list[str] = Field(default_factory=list)
    next_step_recommendation_before: NextStepRecommendation
    next_step_recommendation_after: NextStepRecommendation


# ---------------------------------------------------------------------------
# Top-level loop result
# ---------------------------------------------------------------------------


class RunScopedTopUpLoopResult(BaseModel):
    """Final operator-facing result for one top-up loop. In dry-run
    mode `ingestion`, `persona_write`, and `reaudit` are None; the
    plan is the only meaningful output."""

    model_config = ConfigDict(extra="forbid")

    brief_label: str
    plan: RunScopedTopUpPlan
    dry_run: bool
    ingestion: TopUpExecutionResult | None = None
    persona_write: TopUpPersonaWriteResult | None = None
    reaudit: RunScopedReauditResult | None = None
    summary_text: str
    safety_assertions: list[str] = Field(default_factory=list)
