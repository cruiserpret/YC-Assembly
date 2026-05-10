"""Phase 8.2I — convert Phase 8.2H top-up recommendations into a
concrete `RunScopedTopUpPlan` ready for live Tavily execution.

Pure function. No DB, no LLM, no network.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from assembly.pipeline.audience_retrieval.schemas import (
    RunScopedAudienceRetrievalResult,
    TopUpRecommendation,
)
from assembly.pipeline.run_scoped_topup.schemas import RunScopedTopUpPlan


# Default caps — explicit so an operator can read them.
_DEFAULT_MAX_CATEGORIES = 5
_DEFAULT_MAX_QUERIES_PER_CATEGORY = 3
_DEFAULT_MAX_TOTAL_QUERIES = 15
_DEFAULT_MAX_RESULTS_PER_QUERY = 10
_DEFAULT_MAX_ACCEPTED_RECORDS = 100
_DEFAULT_MAX_CONTENT_CHARS = 4000
_DEFAULT_PERSONA_WRITE_CAP = 50
_DEFAULT_COST_CAP_USD = Decimal("2.00")


def build_topup_plan_from_audience_retrieval(
    *,
    brief_label: str,
    audience_result: RunScopedAudienceRetrievalResult,
    max_categories: int = _DEFAULT_MAX_CATEGORIES,
    max_queries_per_category: int = _DEFAULT_MAX_QUERIES_PER_CATEGORY,
    max_total_queries: int = _DEFAULT_MAX_TOTAL_QUERIES,
    max_results_per_query: int = _DEFAULT_MAX_RESULTS_PER_QUERY,
    max_accepted_records: int = _DEFAULT_MAX_ACCEPTED_RECORDS,
    max_content_chars: int = _DEFAULT_MAX_CONTENT_CHARS,
    persona_write_cap: int = _DEFAULT_PERSONA_WRITE_CAP,
    cost_cap_usd: Decimal = _DEFAULT_COST_CAP_USD,
    approve_sensitive_topup: bool = False,
) -> RunScopedTopUpPlan:
    """Pick the top-N missing/thin categories from the audience-
    retrieval result, slice each category's queries to
    `max_queries_per_category`, and cap the total at
    `max_total_queries`.

    Sensitive top-up: by default, recommendations marked
    `requires_extra_compliance_review=True` are EXCLUDED unless
    `approve_sensitive_topup=True`. Sensitive caveats are surfaced on
    the plan regardless.
    """
    if max_categories < 1 or max_categories > 30:
        raise ValueError(f"max_categories out of range [1, 30]: {max_categories}")
    if max_total_queries > 30:
        raise ValueError(f"max_total_queries > 30: {max_total_queries}")

    # Sort recommendations: high-priority categories first, then by
    # number of suggested queries available (more queries → richer
    # potential signal).
    rank_by_priority = _rank_categories_by_priority(audience_result)

    selected: list[TopUpRecommendation] = []
    sensitive_caveats: list[str] = []
    requires_compliance = False
    skipped_sensitive: list[str] = []

    for rec in audience_result.topup_recommendations:
        priority_rank = rank_by_priority.get(rec.stakeholder_category_key, 99)
        if rec.requires_extra_compliance_review:
            sensitive_caveats.extend(rec.caveats)
            requires_compliance = True
            if not approve_sensitive_topup:
                skipped_sensitive.append(rec.stakeholder_category_key)
                continue
        selected.append(rec)
    # Stable sort by priority rank.
    selected.sort(key=lambda r: rank_by_priority.get(r.stakeholder_category_key, 99))
    selected = selected[:max_categories]

    queries_by_category: dict[str, list[str]] = {}
    total = 0
    for rec in selected:
        slot = max_total_queries - total
        if slot <= 0:
            break
        per_cat = min(max_queries_per_category, slot)
        queries = list(rec.suggested_queries)[:per_cat]
        if not queries:
            continue
        queries_by_category[rec.stakeholder_category_key] = queries
        total += len(queries)

    if not queries_by_category:
        # Defensive — if every selected rec was empty, build a minimal
        # plan with the first available category's queries even if it
        # was sensitive (operator can re-run with explicit approval).
        raise ValueError(
            "No queries selected for top-up; either no top-up recs in "
            "audience result, all recs were sensitive (without "
            "approve_sensitive_topup=True), or every rec had no queries."
        )

    target_categories = list(queries_by_category.keys())

    # De-dup sensitive caveats while preserving order.
    seen: set[str] = set()
    sensitive_caveats_dedup: list[str] = []
    for c in sensitive_caveats:
        if c in seen:
            continue
        seen.add(c)
        sensitive_caveats_dedup.append(c)
    if skipped_sensitive:
        sensitive_caveats_dedup.append(
            "Skipped sensitive top-up categories without explicit approval: "
            + ", ".join(sorted(skipped_sensitive))
        )

    return RunScopedTopUpPlan(
        brief_label=brief_label,
        target_categories=target_categories,
        queries_by_category=queries_by_category,
        total_queries=total,
        max_queries_per_category=max_queries_per_category,
        max_total_queries=max_total_queries,
        max_results_per_query=max_results_per_query,
        max_accepted_records=max_accepted_records,
        max_content_chars=max_content_chars,
        persona_write_cap=persona_write_cap,
        cost_cap_usd=cost_cap_usd,
        sensitive_caveats=sensitive_caveats_dedup,
        requires_compliance_approval=requires_compliance,
    )


def _rank_categories_by_priority(
    audience_result: RunScopedAudienceRetrievalResult,
) -> dict[str, int]:
    """Build a priority rank from the audience result's coverage list.
    high=0, medium=1, low=2. MISSING > THIN > acceptable_for_tiny."""
    out: dict[str, int] = {}
    for cov in audience_result.category_coverage:
        prio = {"high": 0, "medium": 1, "low": 2}.get(cov.priority, 9)
        # missing first, thin second, acceptable_for_tiny third
        from assembly.pipeline.audience_retrieval.schemas import (
            CategoryCoverageLabel,
        )
        label_rank = {
            CategoryCoverageLabel.MISSING: 0,
            CategoryCoverageLabel.THIN: 1,
            CategoryCoverageLabel.ACCEPTABLE_FOR_TINY: 2,
            CategoryCoverageLabel.ACCEPTABLE_FOR_SMALL: 5,
            CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS: 6,
        }.get(cov.coverage_label, 9)
        out[cov.category_key] = prio * 3 + label_rank
    return out


def flatten_plan_to_query_to_category_map(
    plan: RunScopedTopUpPlan,
) -> dict[str, str]:
    """Return a {query_string: category_key} map for the Tavily adapter.
    The adapter uses this to tag each result with the
    `target_missing_category` it's aimed at."""
    out: dict[str, str] = {}
    for cat_key, qs in plan.queries_by_category.items():
        for q in qs:
            out[q] = cat_key
    return out
