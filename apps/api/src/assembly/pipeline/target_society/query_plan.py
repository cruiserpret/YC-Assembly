"""Phase 8.2G — per-category source-query plan generator.

Produces Tavily-ready query strings for each stakeholder category,
plus competitor / pricing / geography query slices. The planner
NEVER calls Tavily — it only generates the query strings the
Phase-8.2H retrieval layer will execute.
"""
from __future__ import annotations

from assembly.pipeline.target_society.schemas import (
    ProductBriefInput,
    SourceQueryPlan,
    StakeholderCategory,
)


_PUBLIC_DISCUSSION_TARGETS: tuple[str, ...] = (
    "site:reddit.com",
    "site:community.shopify.com",
    "site:news.ycombinator.com",
    "site:quora.com",
    "site:trustpilot.com",
)


_REVIEW_FORUM_PATTERNS: tuple[str, ...] = (
    "review",
    "forum",
    "complaint",
    "discussion",
    "thread",
    "comments",
    "feedback",
)


_FORBIDDEN_QUERIES: tuple[str, ...] = (
    "do not query private profile pages",
    "do not query login-walled pages",
    "do not query paywalled pages",
    "do not query email or phone number lookup",
    "do not query individual person background searches",
    "do not query protected-attribute inference (race / religion / "
    "ethnicity / health / immigration)",
)


def build_source_query_plan_for_category(
    *,
    category: StakeholderCategory,
    brief: ProductBriefInput,
) -> SourceQueryPlan:
    """Build a per-category Tavily-ready query slice."""
    queries = generate_search_queries_for_category(category=category, brief=brief)
    competitor_queries = generate_competitor_queries(
        category=category, brief=brief,
    )
    pricing_queries = generate_pricing_queries(category=category, brief=brief)
    geography_queries = generate_geography_queries(
        category=category, brief=brief,
    )

    return SourceQueryPlan(
        category_key=category.category_key,
        queries=queries,
        preferred_source_types=[
            "public forum thread",
            "public review",
            "public comment / discussion",
            "public Q&A",
            "public blog with first-person language",
        ],
        discussion_targets=list(_PUBLIC_DISCUSSION_TARGETS),
        review_forum_patterns=list(_REVIEW_FORUM_PATTERNS),
        competitor_queries=competitor_queries,
        pricing_queries=pricing_queries,
        geography_queries=geography_queries,
        forbidden_queries=list(_FORBIDDEN_QUERIES),
    )


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def generate_search_queries_for_category(
    *,
    category: StakeholderCategory,
    brief: ProductBriefInput,
) -> list[str]:
    """Generate the per-category Tavily query list.

    Uses the category's `source_query_themes` plus the brief's product
    name / type as anchor terms.
    """
    out: list[str] = []
    anchor = (brief.product_type or brief.product_name or "").strip()
    for theme in category.source_query_themes:
        # Replace placeholder pattern <product_type> if present.
        q = theme.replace("<product_type>", anchor or "product")
        q = q.replace("<product>", anchor or "product")
        out.append(q)
        # Add a forum-shaped variant if the theme didn't already
        # mention site:.
        if "site:" not in q:
            out.append(f"site:reddit.com {q}")
    # Drop blanks + dedup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for q in out:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            deduped.append(q)
    return deduped


def generate_competitor_queries(
    *,
    category: StakeholderCategory,
    brief: ProductBriefInput,
) -> list[str]:
    """For each named competitor in the brief, emit comparison /
    switching queries. Empty list when no competitors named."""
    out: list[str] = []
    if not brief.competitors:
        return out
    for c in brief.competitors[:5]:
        c = c.strip()
        if not c:
            continue
        out.extend([
            f"{c} review {category.display_name}",
            f"switch from {c} {category.display_name}",
            f"{c} complaints",
        ])
    return out


def generate_pricing_queries(
    *,
    category: StakeholderCategory,
    brief: ProductBriefInput,
) -> list[str]:
    """Pricing-specific queries; only emitted when the brief carries a
    price string OR when the category explicitly mentions price."""
    if not brief.price_or_price_structure and category.priority != "high":
        return []
    anchor = (brief.product_type or brief.product_name or "product").strip()
    return [
        f"{anchor} too expensive complaint",
        f"{anchor} pricing concern review",
        f"{anchor} value for money discussion",
    ]


def generate_geography_queries(
    *,
    category: StakeholderCategory,
    brief: ProductBriefInput,
) -> list[str]:
    """Geography-specific queries. Only emitted when the brief has a
    `geography` field; otherwise empty."""
    if not brief.geography:
        return []
    geo = brief.geography.strip()
    anchor = (brief.product_type or brief.product_name or "product").strip()
    return [
        f"{anchor} {geo} consumer review",
        f"{anchor} {geo} buyer forum",
        f"{anchor} availability in {geo}",
    ]


def generate_public_opinion_queries(
    *,
    brief: ProductBriefInput,
) -> list[str]:
    """Top-level public-opinion seed queries that are not bound to a
    single stakeholder category. Useful for the broad-sweep first
    retrieval pass."""
    anchor = (brief.product_type or brief.product_name or "product").strip()
    out = [
        f"{anchor} buyer review forum",
        f"{anchor} public discussion thread",
        f"{anchor} consumer complaint",
    ]
    if brief.competitors:
        for c in brief.competitors[:3]:
            out.append(f"{anchor} vs {c} discussion")
    if brief.geography:
        out.append(f"{anchor} {brief.geography} buyer feedback")
    return out
