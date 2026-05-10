"""Phase 8.5B.1 — schemas for the dynamic evidence anchor planner.

`extra="forbid"` discipline is preserved across all schemas. Every
field is explicitly typed; closed-set string literals are constrained
via Pydantic.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductBriefForPlanning(BaseModel):
    """Founder-style input — the ONLY input the planner accepts."""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(min_length=1)
    product_description: str = Field(min_length=8)
    price_or_price_structure: str | None = None
    launch_geography: str | None = None
    target_customers: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    optional_constraints: list[str] = Field(default_factory=list)


class AmbiguousEntity(BaseModel):
    """One competitor / brand token with multiple real-world senses."""

    model_config = ConfigDict(extra="forbid")

    entity: str  # e.g. "Prime"
    intended_sense_label: str  # human-readable, e.g. "Prime energy drink"
    intended_sense_phrases: list[str]  # accept-context tokens
    wrong_sense_categories: list[str]  # keys from UNIVERSAL_AMBIGUITY_CONTEXTS
    wrong_sense_phrases: list[str]  # collected denylist phrases


class MetadataRelevanceRule(BaseModel):
    """A rule the scorer applies against joined Amazon metadata.

    `kind` is closed:
      * `category_includes_any` — at least one term in `values`
        appears in metadata.main_category + metadata.categories.
      * `title_contains_any` — at least one term in `values` appears
        in metadata.title.
      * `category_excludes_any` — NONE of `values` appear in
        metadata.main_category + metadata.categories.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "category_includes_any",
        "title_contains_any",
        "category_excludes_any",
    ]
    values: list[str]
    weight: int  # +/- contribution to confidence score


class EvidenceAnchorPlan(BaseModel):
    """Full per-product anchor plan derived from the founder brief.

    Used by the dynamic Amazon scorer to filter / rank reviews. Also
    the artifact written into audit JSON so the operator can see what
    the planner inferred."""

    model_config = ConfigDict(extra="forbid")

    # --- Inputs echoed for audit traceability ---
    product_name: str
    product_type: str  # the planner's best guess from description
    launch_geography: str | None
    target_customers: list[str]
    competitors: list[str]
    substitutes: list[str]

    # --- Inferred anchor lists ---
    positive_anchor_terms: list[str]
    competitor_anchor_terms: list[str]
    substitute_anchor_terms: list[str]
    use_case_anchor_terms: list[str]
    objection_anchor_terms: list[str]

    # --- Universal lists, attached for transparency ---
    generic_modifier_terms: list[str]

    # --- Cross-domain ambiguity handling ---
    ambiguous_entities: list[AmbiguousEntity]
    negative_context_terms: list[str]

    # --- Metadata-side relevance rules ---
    metadata_relevance_rules: list[MetadataRelevanceRule]

    # --- Provenance + caveats ---
    generated_from: Literal["deterministic", "llm", "hybrid"]
    caveats: list[str]
    plan_id: str  # hash of the brief — stable across runs
    generated_at: str  # ISO8601 UTC
