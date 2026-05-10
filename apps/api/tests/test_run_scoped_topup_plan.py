"""Phase 8.2I — ingestion-plan tests (pure)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from assembly.pipeline.audience_retrieval import (
    retrieve_personas_for_target_society,
)
from assembly.pipeline.run_scoped_topup import (
    build_topup_plan_from_audience_retrieval,
    flatten_plan_to_query_to_category_map,
)
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


def _audience_for(brief):
    plan = build_target_society_plan(brief)
    return retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[],
    )


# ---------------------------------------------------------------------------
# Builds a plan
# ---------------------------------------------------------------------------


def test_build_plan_for_amboras_with_empty_pool() -> None:
    audience = _audience_for(AMBORAS_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="amboras", audience_result=audience,
    )
    assert plan.brief_label == "amboras"
    assert 1 <= len(plan.target_categories) <= 5
    assert 1 <= plan.total_queries <= plan.max_total_queries
    # Queries must come from the target_society plan (i.e. each query
    # appears in the audience top-up recs).
    audience_queries = set()
    for t in audience.topup_recommendations:
        audience_queries.update(t.suggested_queries)
    for cat, qs in plan.queries_by_category.items():
        for q in qs:
            assert q in audience_queries


def test_max_categories_caps_target_count() -> None:
    audience = _audience_for(AMBORAS_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="amboras",
        audience_result=audience,
        max_categories=3,
        max_queries_per_category=2,
        max_total_queries=6,
    )
    assert len(plan.target_categories) <= 3
    assert plan.total_queries <= 6
    for cat, qs in plan.queries_by_category.items():
        assert len(qs) <= 2


def test_max_total_queries_caps_aggregate() -> None:
    audience = _audience_for(AMBORAS_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="amboras",
        audience_result=audience,
        max_total_queries=4,
    )
    assert plan.total_queries <= 4
    total = sum(len(v) for v in plan.queries_by_category.values())
    assert total <= 4


def test_max_total_queries_above_cap_rejected() -> None:
    audience = _audience_for(AMBORAS_BRIEF)
    with pytest.raises(ValueError):
        build_topup_plan_from_audience_retrieval(
            brief_label="amboras",
            audience_result=audience,
            max_total_queries=31,  # > schema ceiling 30 (8.2I.1 raised from 15)
        )


# ---------------------------------------------------------------------------
# Sensitive top-up: excluded by default
# ---------------------------------------------------------------------------


def test_sensitive_topup_excluded_when_not_approved() -> None:
    audience = _audience_for(HALAL_FINANCING_BRIEF)
    # Without approve_sensitive_topup=True, every sensitive rec must
    # be skipped. Halal-financing audience has 9 sensitive recs.
    with pytest.raises(ValueError):
        build_topup_plan_from_audience_retrieval(
            brief_label="halal_financing",
            audience_result=audience,
            approve_sensitive_topup=False,
        )


def test_sensitive_topup_included_when_approved() -> None:
    audience = _audience_for(HALAL_FINANCING_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="halal_financing",
        audience_result=audience,
        approve_sensitive_topup=True,
    )
    assert plan.requires_compliance_approval is True
    assert plan.sensitive_caveats, "sensitive caveats must be surfaced"


# ---------------------------------------------------------------------------
# Plan defaults / limits
# ---------------------------------------------------------------------------


def test_plan_default_caps_match_spec() -> None:
    audience = _audience_for(AMBORAS_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="amboras", audience_result=audience,
    )
    # Spec defaults:
    #   max_results_per_query=10, max_accepted_records=100,
    #   max_content_chars=4000, persona_write_cap=50, cost_cap_usd=2.00
    assert plan.max_results_per_query == 10
    assert plan.max_accepted_records == 100
    assert plan.max_content_chars == 4000
    assert plan.persona_write_cap == 50
    assert plan.cost_cap_usd == Decimal("2.00")


def test_water_bottle_plan_uses_consumer_categories() -> None:
    audience = _audience_for(WATER_BOTTLE_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="water_bottle_california", audience_result=audience,
    )
    # No commerce-shape categories should sneak in.
    for k in plan.target_categories:
        assert "shopify" not in k
        assert "merchant" not in k


# ---------------------------------------------------------------------------
# Helper: flatten_plan_to_query_to_category_map
# ---------------------------------------------------------------------------


def test_flatten_plan_to_query_to_category_map() -> None:
    audience = _audience_for(AMBORAS_BRIEF)
    plan = build_topup_plan_from_audience_retrieval(
        brief_label="amboras", audience_result=audience,
    )
    qmap = flatten_plan_to_query_to_category_map(plan)
    assert sum(len(v) for v in plan.queries_by_category.values()) == len(qmap)
    for cat, qs in plan.queries_by_category.items():
        for q in qs:
            assert qmap[q] == cat
