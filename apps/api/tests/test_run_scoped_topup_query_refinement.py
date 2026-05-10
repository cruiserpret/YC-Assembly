"""Phase 8.2I.1 — refined Amboras query catalog tests."""
from __future__ import annotations

import re

import pytest

from assembly.pipeline.run_scoped_topup import (
    AMBORAS_REFINED_QUERIES_V1,
    REFINEMENT_VERSION,
    build_amboras_refined_topup_plan,
)
from assembly.pipeline.target_society.constants import ProductFamily
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF, build_target_society_plan,
)


# ---------------------------------------------------------------------------
# Catalog shape
# ---------------------------------------------------------------------------


def test_refinement_version_is_8_2_i_1() -> None:
    assert REFINEMENT_VERSION == "8.2I.1"


def test_catalog_targets_six_amboras_categories() -> None:
    """The refined catalog covers the 6 audit-flagged categories."""
    expected = {
        "shopify_or_platform_merchant",
        "dtc_founder_brand_control",
        "agency_dependent_merchant",
        "ai_skeptical_operator",
        "nontechnical_founder",
        "lock_in_worried_operator",
    }
    assert set(AMBORAS_REFINED_QUERIES_V1.keys()) == expected


def test_catalog_has_5_queries_per_category() -> None:
    for cat, qs in AMBORAS_REFINED_QUERIES_V1.items():
        assert len(qs) == 5, (
            f"category {cat} should have 5 queries; got {len(qs)}"
        )


# ---------------------------------------------------------------------------
# Query content discipline — TIGHT, not generic.
# ---------------------------------------------------------------------------


def test_every_query_uses_quoted_phrases() -> None:
    """Every refined query must contain at least 2 quoted phrases.
    The whole point of refinement is exact-phrase Tavily ranking."""
    quote_re = re.compile(r'"[^"]+"')
    for cat, qs in AMBORAS_REFINED_QUERIES_V1.items():
        for q in qs:
            n_quoted = len(quote_re.findall(q))
            assert n_quoted >= 2, (
                f"refined query in {cat} should have ≥2 quoted phrases: {q!r}"
            )


def test_every_query_mentions_amboras_anchor_term() -> None:
    """Every query must mention an Amboras-relevant anchor term:
    Shopify, DTC, ecommerce, or AI store builder. The previous
    8.2I queries were too generic ('merchant agency cost') — that
    pattern is forbidden in 8.2I.1."""
    anchor_re = re.compile(
        r"\bshopify\b|\bdtc\b|\becommerce\b|\bai store builder\b|"
        r"\bai website builder\b|\bai generated store\b|\bai ecommerce store\b",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for cat, qs in AMBORAS_REFINED_QUERIES_V1.items():
        for q in qs:
            if not anchor_re.search(q):
                offenders.append(f"[{cat}] {q!r}")
    assert offenders == [], (
        "refined queries must mention Shopify/DTC/ecommerce/AI-store anchor:\n"
        + "\n  ".join(offenders)
    )


def test_pain_signal_queries_carry_pain_phrases() -> None:
    """The shopify_or_platform_merchant + nontechnical categories
    should mention plugin/app/setup pain phrases."""
    pain_terms = (
        "plugin bloat", "too many apps", "monthly fees", "app fatigue",
        "apps are expensive", "too complicated", "overwhelming",
    )
    text = "\n".join(
        q for cat in (
            "shopify_or_platform_merchant", "nontechnical_founder",
        )
        for q in AMBORAS_REFINED_QUERIES_V1[cat]
    ).lower()
    hits = sum(1 for p in pain_terms if p in text)
    assert hits >= 4, (
        f"pain-signal categories should include ≥4 pain phrases; got {hits}"
    )


def test_lock_in_queries_explicitly_target_lock_in() -> None:
    """The lock_in_worried_operator queries must mention lock-in
    explicitly."""
    lock_in_count = sum(
        1 for q in AMBORAS_REFINED_QUERIES_V1["lock_in_worried_operator"]
        if "lock-in" in q.lower() or "lock in" in q.lower()
        or "switching" in q.lower() or "leaving" in q.lower()
    )
    assert lock_in_count >= 4


def test_no_generic_short_queries() -> None:
    """The previous 8.2I queries had bag-of-words like
    'AI skepticism merchant' (3 words, no quotes). Refined queries
    must have ≥2 quoted phrases (the real discipline; tested above)
    AND ≥3 words. Short-but-quoted queries like
    `"nontechnical founder" "Shopify"` are acceptable."""
    short_queries = [
        q for cat, qs in AMBORAS_REFINED_QUERIES_V1.items() for q in qs
        if len(q.split()) < 3
    ]
    assert short_queries == [], (
        f"refined queries should be ≥3 words; short ones: {short_queries}"
    )


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def test_default_plan_has_correct_caps() -> None:
    plan = build_amboras_refined_topup_plan()
    assert plan.brief_label == "amboras"
    assert plan.max_queries_per_category == 5
    assert plan.max_total_queries == 30
    assert plan.max_results_per_query == 8
    assert plan.max_accepted_records == 120
    assert plan.persona_write_cap == 50
    assert plan.query_refinement_version == "8.2I.1"


def test_default_plan_emits_30_queries_across_6_categories() -> None:
    plan = build_amboras_refined_topup_plan()
    assert plan.total_queries == 30
    assert len(plan.target_categories) == 6
    for cat, qs in plan.queries_by_category.items():
        assert len(qs) == 5


def test_plan_caps_can_be_lowered() -> None:
    plan = build_amboras_refined_topup_plan(
        max_categories=3,
        max_queries_per_category=2,
        max_total_queries=6,
    )
    assert len(plan.target_categories) == 3
    assert plan.total_queries == 6


def test_plan_max_total_queries_above_30_rejected() -> None:
    with pytest.raises(ValueError):
        build_amboras_refined_topup_plan(max_total_queries=31)


def test_plan_max_categories_above_30_rejected() -> None:
    with pytest.raises(ValueError):
        build_amboras_refined_topup_plan(max_categories=31)


def test_plan_max_queries_per_category_above_10_rejected() -> None:
    with pytest.raises(ValueError):
        build_amboras_refined_topup_plan(max_queries_per_category=11)


# ---------------------------------------------------------------------------
# Refined plan does NOT change scorer thresholds
# ---------------------------------------------------------------------------


def test_amboras_target_society_threshold_remains_27() -> None:
    """The Phase 8.2G persona_retrieval_plan.minimum_relevance_threshold
    must remain at 27 (the spec rule). 8.2I.1 must NOT lower this."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    assert plan.persona_retrieval_plan.minimum_relevance_threshold == 27


def test_classification_thresholds_unchanged() -> None:
    """Phase 8.2F.7 closed-enum classification thresholds remain at
    36 / 27 / 18. 8.2I.1 must NOT lower them."""
    from assembly.pipeline.persona_relevance.rubric import (
        CLASSIFICATION_THRESHOLDS,
        RelevanceClassification,
    )
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.HIGHLY_RELEVANT] == 36
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.RELEVANT] == 27
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.WEAKLY_RELEVANT] == 18


def test_amboras_stakeholder_categories_unchanged() -> None:
    """The 8.2G commerce_platform stakeholder template must include
    exactly the same baseline categories. 8.2I.1 must NOT
    artificially broaden it."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    assert plan.interpreted_brief.detected_product_family is (
        ProductFamily.COMMERCE_PLATFORM_OR_TOOLING
    )
    expected_baseline = {
        "shopify_or_platform_merchant",
        "dtc_founder_brand_control",
        "agency_dependent_merchant",
        "ai_skeptical_operator",
        "nontechnical_founder",
    }
    actual = {c.category_key for c in plan.stakeholder_categories}
    assert expected_baseline.issubset(actual), (
        f"Amboras commerce baseline categories must be present: "
        f"missing={expected_baseline - actual}"
    )
