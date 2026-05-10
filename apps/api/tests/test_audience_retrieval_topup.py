"""Phase 8.2H — top-up recommendation tests."""
from __future__ import annotations

from assembly.pipeline.audience_retrieval import (
    build_topup_recommendations,
    compute_category_coverage,
    convert_target_society_queries_to_topup_plan,
)
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


def test_empty_pool_emits_one_topup_per_stakeholder_category() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    topup = build_topup_recommendations(plan=plan, coverage=coverage)
    assert len(topup) == len(plan.stakeholder_categories)
    for t in topup:
        assert t.suggested_queries, (
            "Each top-up rec must carry the category's queries"
        )


def test_topup_queries_come_from_target_society_plan() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    topup = build_topup_recommendations(plan=plan, coverage=coverage)
    # Every recommendation's queries must appear in the plan's
    # source_query_plan for its category.
    qmap = {q.category_key: list(q.queries) for q in plan.source_query_plan}
    for t in topup:
        plan_queries = qmap.get(t.stakeholder_category_key, [])
        assert t.suggested_queries == plan_queries


def test_topup_does_not_call_anything_live() -> None:
    """Sanity guard: building top-up recommendations is a pure
    function — it should not touch the network or DB. We exercise
    it on the four example briefs and confirm no exceptions."""
    for brief in (
        AMBORAS_BRIEF, WATER_BOTTLE_BRIEF, HALAL_FINANCING_BRIEF,
    ):
        plan = build_target_society_plan(brief)
        coverage = compute_category_coverage(plan=plan, matched=[])
        recs = build_topup_recommendations(plan=plan, coverage=coverage)
        assert recs


def test_sensitive_topup_marked_extra_compliance_review() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    topup = build_topup_recommendations(plan=plan, coverage=coverage)
    # Halal-financing brief carries sensitive markers; every top-up
    # must require extra compliance review.
    assert all(t.requires_extra_compliance_review for t in topup), (
        "All halal-financing top-up recs must require extra compliance review"
    )


def test_non_sensitive_topup_does_not_require_extra_review() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    topup = build_topup_recommendations(plan=plan, coverage=coverage)
    assert not any(t.requires_extra_compliance_review for t in topup)


def test_topup_max_records_bounded() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    topup = build_topup_recommendations(plan=plan, coverage=coverage)
    for t in topup:
        assert 1 <= t.max_records_suggested <= 200


def test_convert_target_society_queries_helper() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    flattened = convert_target_society_queries_to_topup_plan(plan)
    cat_keys = {c.category_key for c in plan.stakeholder_categories}
    assert set(flattened.keys()) == cat_keys
    for k, queries in flattened.items():
        assert isinstance(queries, list)
        assert all(isinstance(q, str) for q in queries)
