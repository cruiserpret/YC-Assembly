"""Phase 8.2I.1 — refined Amboras query catalog + plan builder.

Phase 8.2I's first Amboras top-up returned 15 well-formed personas
that were ALL adjacent commerce voices (GDPR / Google Merchant Center
/ payments) rather than tightly Shopify-merchant-with-Amboras-pain.
The audience-retrieval scorer correctly refused to claim them as
relevant.

This module ships a tighter query catalog. Every query uses
quoted-phrase operators (`"Shopify merchant" "plugin bloat"`) to
push Tavily toward exact-phrase matches over generic ranking.

CRITICAL invariants this module preserves:
  * minimum_relevance_threshold remains 27
  * scorer thresholds untouched
  * stakeholder categories untouched
  * `build_amboras_refined_topup_plan` directly emits a
    `RunScopedTopUpPlan` from this catalog — it does NOT take a
    `RunScopedAudienceRetrievalResult` as input. The before/after
    audience retrieval still runs at executor time, just from this
    explicit plan rather than the audience-retrieval-driven one.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Final

from assembly.pipeline.run_scoped_topup.schemas import RunScopedTopUpPlan


REFINEMENT_VERSION: Final[str] = "8.2I.1"


# Refined Amboras query catalog. Each entry maps a category_key (which
# matches a Phase 8.2G `StakeholderCategory.category_key` for the
# commerce family) to a list of tightly-quoted Tavily queries.
#
# Quoted phrases force Tavily toward exact-match ranking; that's the
# whole point of the refinement vs. Phase 8.2I's looser bag-of-words.
AMBORAS_REFINED_QUERIES_V1: Final[dict[str, tuple[str, ...]]] = {
    "shopify_or_platform_merchant": (
        '"Shopify merchant" "plugin bloat"',
        '"Shopify" "too many apps" "merchant"',
        '"Shopify apps" "monthly fees" "small business"',
        '"Shopify merchant" "app fatigue"',
        '"Shopify" "apps are expensive" "merchant"',
    ),
    "dtc_founder_brand_control": (
        '"DTC founder" "brand control" "Shopify"',
        '"Shopify" "brand control" "DTC"',
        '"ecommerce founder" "brand identity" "Shopify"',
        '"Shopify" "custom storefront" "brand control"',
        '"DTC brand" "Shopify limitations"',
    ),
    "agency_dependent_merchant": (
        '"Shopify merchant" "agency cost"',
        '"Shopify" "custom theme" "agency" "expensive"',
        '"ecommerce founder" "hired agency" "Shopify"',
        '"Shopify store" "freelancer" "problems"',
        '"Shopify merchant" "custom theme" "cost"',
    ),
    "ai_skeptical_operator": (
        '"Shopify merchant" "AI store builder" "skeptical"',
        '"AI store builder" "Shopify" "concerns"',
        '"Shopify" "AI generated store" "trust"',
        '"ecommerce founder" "AI website builder" "concerns"',
        '"AI ecommerce store" "brand control"',
    ),
    "nontechnical_founder": (
        '"nontechnical founder" "Shopify"',
        '"small business owner" "Shopify setup" "overwhelming"',
        '"Shopify setup" "too complicated" "merchant"',
        '"Shopify beginner" "too many apps"',
        '"Shopify store setup" "frustrated" "small business"',
    ),
    # Phase 8.2I.1 also targets the lock-in segment that Phase 8.2F.7
    # flagged as missing. The target_society plan generates the
    # `lock_in_worried_operator` category only when the brief carries
    # the appropriate signals; in 8.2I.1 we surface queries for it
    # explicitly so the operator can route Tavily toward that segment
    # even if the base plan didn't auto-emit a category for it.
    "lock_in_worried_operator": (
        '"Shopify lock-in" "merchant"',
        '"Shopify" "vendor lock-in" "ecommerce"',
        '"Shopify merchant" "platform lock-in"',
        '"leaving Shopify" "merchant" "apps"',
        '"Shopify" "switching platforms" "merchant"',
    ),
}


def build_amboras_refined_topup_plan(
    *,
    brief_label: str = "amboras",
    catalog: Mapping[str, tuple[str, ...] | list[str]] | None = None,
    max_categories: int = 6,
    max_queries_per_category: int = 5,
    max_total_queries: int = 30,
    max_results_per_query: int = 8,
    max_accepted_records: int = 120,
    max_content_chars: int = 4000,
    persona_write_cap: int = 50,
    cost_cap_usd: Decimal = Decimal("2.00"),
) -> RunScopedTopUpPlan:
    """Emit a `RunScopedTopUpPlan` directly from the refined catalog.

    NO audience-retrieval input required — this path bypasses the
    audience-retrieval-driven query picker and uses the explicit
    refined catalog. The before/after audience retrieval still runs
    at executor time so the re-audit captures the actual coverage
    delta.

    Caps default to the 8.2I.1 spec:
      * max_categories: 6
      * max_queries_per_category: 5
      * max_total_queries: 30
      * max_results_per_query: 8
      * max_accepted_records: 120
      * persona_write_cap: 50
      * cost_cap_usd: $2.00
    """
    if max_categories < 1 or max_categories > 30:
        raise ValueError(f"max_categories out of range [1, 30]: {max_categories}")
    if max_total_queries > 30:
        raise ValueError(f"max_total_queries > 30: {max_total_queries}")
    if max_queries_per_category > 10:
        raise ValueError(
            f"max_queries_per_category > 10: {max_queries_per_category}"
        )

    src = (
        {k: tuple(v) for k, v in catalog.items()}
        if catalog is not None
        else dict(AMBORAS_REFINED_QUERIES_V1)
    )

    selected_categories: list[str] = list(src.keys())[:max_categories]
    queries_by_category: dict[str, list[str]] = {}
    total = 0
    for cat in selected_categories:
        slot = max_total_queries - total
        if slot <= 0:
            break
        per_cat = min(max_queries_per_category, slot)
        cat_queries = list(src[cat])[:per_cat]
        if not cat_queries:
            continue
        queries_by_category[cat] = cat_queries
        total += len(cat_queries)
    if total == 0:
        raise ValueError(
            "refined catalog yielded zero queries — empty catalog or "
            "all caps set to 0"
        )

    return RunScopedTopUpPlan(
        brief_label=brief_label,
        target_categories=list(queries_by_category.keys()),
        queries_by_category=queries_by_category,
        total_queries=total,
        max_queries_per_category=max_queries_per_category,
        max_total_queries=max_total_queries,
        max_results_per_query=max_results_per_query,
        max_accepted_records=max_accepted_records,
        max_content_chars=max_content_chars,
        persona_write_cap=persona_write_cap,
        cost_cap_usd=cost_cap_usd,
        sensitive_caveats=[],
        requires_compliance_approval=False,
        query_refinement_version=REFINEMENT_VERSION,
    )
