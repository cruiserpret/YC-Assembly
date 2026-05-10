"""Phase 8.2G — query-plan tests."""
from __future__ import annotations

from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
    generate_competitor_queries,
    generate_geography_queries,
    generate_pricing_queries,
    generate_public_opinion_queries,
    generate_search_queries_for_category,
)


# ---------------------------------------------------------------------------
# Per-category queries
# ---------------------------------------------------------------------------


def test_each_category_produces_queries() -> None:
    for brief in (
        AMBORAS_BRIEF, WATER_BOTTLE_BRIEF, IPHONE_17_BRIEF,
        HALAL_FINANCING_BRIEF,
    ):
        plan = build_target_society_plan(brief)
        # Every category in stakeholder_categories has a matching plan.
        plan_keys = {p.category_key for p in plan.source_query_plan}
        cat_keys = {c.category_key for c in plan.stakeholder_categories}
        assert cat_keys == plan_keys
        for p in plan.source_query_plan:
            assert p.queries, (
                f"plan for {p.category_key} has no queries"
            )


def test_queries_are_category_specific() -> None:
    """Across categories within one brief, the first query is distinct
    (i.e. each category gets a plan derived from its own
    source_query_themes, not a copy of a single global query)."""
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    first_queries = [q.queries[0] for q in plan.source_query_plan if q.queries]
    # At least 60% of category queries should be unique across the
    # brief (some shared structure is OK, e.g. site:reddit prefix).
    assert len(set(first_queries)) >= int(0.6 * len(first_queries))


def test_queries_target_review_or_forum_patterns() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    flat = [q for p in plan.source_query_plan for q in p.queries]
    pattern_words = ("review", "forum", "complaint", "discussion", "thread")
    matched = sum(
        1 for q in flat
        if any(p in q.lower() for p in pattern_words)
    )
    assert matched >= 1, (
        "no queries match review/forum/complaint/discussion patterns"
    )


# ---------------------------------------------------------------------------
# Competitor queries — only when brief carries competitors
# ---------------------------------------------------------------------------


def test_competitor_queries_use_named_competitors() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    competitor_qs: list[str] = []
    for p in plan.source_query_plan:
        competitor_qs.extend(p.competitor_queries)
    assert any("samsung" in q.lower() for q in competitor_qs), (
        "Samsung Galaxy was a brief competitor; no Samsung-targeted query"
    )
    assert any("pixel" in q.lower() for q in competitor_qs)


def test_no_competitor_queries_when_no_competitors_in_brief() -> None:
    """Use the AMBORAS brief minus its competitors to confirm
    competitor queries are empty when the brief omits them."""
    from assembly.pipeline.target_society.examples import AMBORAS_BRIEF
    no_comp = AMBORAS_BRIEF.model_copy(update={"competitors": []})
    plan = build_target_society_plan(no_comp)
    for p in plan.source_query_plan:
        assert p.competitor_queries == []


# ---------------------------------------------------------------------------
# Geography queries — only when brief carries geography
# ---------------------------------------------------------------------------


def test_geography_queries_use_brief_geography() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    geo_qs: list[str] = []
    for p in plan.source_query_plan:
        geo_qs.extend(p.geography_queries)
    assert geo_qs, "expected geography queries for California brief"
    assert all("california" in q.lower() for q in geo_qs)


def test_no_geography_queries_when_geography_absent() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    for p in plan.source_query_plan:
        assert p.geography_queries == []


# ---------------------------------------------------------------------------
# Pricing queries
# ---------------------------------------------------------------------------


def test_pricing_queries_emitted_when_price_provided() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    n_pricing = sum(
        len(p.pricing_queries) for p in plan.source_query_plan
    )
    assert n_pricing > 0


# ---------------------------------------------------------------------------
# Forbidden queries
# ---------------------------------------------------------------------------


def test_forbidden_queries_listed_for_every_plan() -> None:
    """Every category's plan must explicitly list the forbidden-query
    rules (private-data / login-walled / protected-attribute lookup)."""
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    for p in plan.source_query_plan:
        assert p.forbidden_queries, (
            f"plan for {p.category_key} has no forbidden_queries list"
        )
        # At least one forbidden query mentions protected-attribute risk.
        forbidden_blob = "\n".join(p.forbidden_queries).lower()
        assert "protected-attribute" in forbidden_blob or "religion" in forbidden_blob


# ---------------------------------------------------------------------------
# Public-opinion seed queries
# ---------------------------------------------------------------------------


def test_public_opinion_seed_queries_use_brief_anchor() -> None:
    qs = generate_public_opinion_queries(brief=IPHONE_17_BRIEF)
    assert qs
    # Anchor token should appear in at least one query.
    assert any("smartphone" in q.lower() or "iphone" in q.lower() for q in qs)
