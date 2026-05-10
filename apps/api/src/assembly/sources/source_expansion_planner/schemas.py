"""Phase 8.5D.1D — schemas for the dynamic source-expansion planner.

`extra="forbid"` discipline. Closed-set string Literals everywhere
state can branch.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ProviderName = Literal[
    "brave_search", "youtube_data_api", "tavily_search",
]
ExpansionQueryKind = Literal[
    "competitor_review",
    "competitor_vs_competitor",
    "undercovered_competitor",
    "substitute_review",
    "use_case_problem",
    "use_case_alternative",
    "category_overview",
    "objection_query",
]
ExpectedEvidenceType = Literal[
    "blog_review", "comparison_article", "buyer_guide",
    "video_review", "video_comment_thread",
    "category_overview_article", "forum_thread", "snippet_only",
]


class ExpansionQuery(BaseModel):
    """One bounded provider query with full provenance."""

    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1)
    provider: ProviderName
    kind: ExpansionQueryKind
    generated_from_fields: list[str]
    rationale: str
    expected_evidence_types: list[ExpectedEvidenceType]
    max_results: int = Field(ge=1, le=20)
    safety_notes: list[str]


class ProviderQueryPlan(BaseModel):
    """Per-provider bounded query batch."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    is_provider_configured: bool
    max_queries: int = Field(ge=0, le=50)
    max_results_per_query: int = Field(ge=0, le=20)
    max_total_results: int = Field(ge=0, le=500)
    queries: list[ExpansionQuery]
    skipped_reason: str | None = None  # set when configured=False


class SourceExpansionPlan(BaseModel):
    """Top-level planner artifact."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str  # sha256-prefix of inputs (deterministic)
    target_brief_id: str
    product_name: str
    launch_state: Literal["unlaunched", "launched", "in_market"]

    # Echo of the inputs that drove the plan (audit traceability)
    diversity_recommendation_in: Literal[
        "READY", "DEFER_DIVERSIFY",
        "DEFER_SOURCE_COVERAGE", "DEFER_NO_CANDIDATES",
    ]
    undercovered_competitor_themes: list[str]
    over_concentrated_competitor: str | None  # e.g. "Body Glide"

    # Provider plans
    provider_query_plans: list[ProviderQueryPlan]
    total_planned_queries: int
    total_planned_max_results: int

    # Provenance
    generated_from: Literal["deterministic", "llm", "hybrid"]
    rationale: list[str]
    safety_caveats: list[str]
    generated_at: str  # ISO8601 UTC
