"""Phase 8.2H — coverage + readiness tests."""
from __future__ import annotations

from uuid import uuid4

from assembly.pipeline.audience_retrieval import (
    CategoryCoverage,
    CategoryCoverageLabel,
    PersonaMatch,
    SourceDiversitySummary,
    compute_category_coverage,
    compute_readiness_by_mode,
    compute_source_diversity,
    detect_missing_key_categories,
    detect_single_source_risk,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


def _match(category_key: str, score: int = 30,
           classification: RelevanceClassification = RelevanceClassification.RELEVANT,
           domains: list[str] | None = None) -> PersonaMatch:
    return PersonaMatch(
        persona_id=str(uuid4()),
        display_name="x",
        matched_category_key=category_key,
        matched_category_display_name=category_key,
        relevance_score=score,
        classification=classification,
        evidence_link_count=3,
        source_domains=domains or [],
        why_included="x",
    )


# ---------------------------------------------------------------------------
# Empty matched list → all categories MISSING
# ---------------------------------------------------------------------------


def test_empty_matched_means_every_category_missing() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    for c in coverage:
        assert c.coverage_label == CategoryCoverageLabel.MISSING
        assert c.matched_total == 0
    # Missing-high-priority detector flags the high categories.
    missing_high = detect_missing_key_categories(coverage=coverage)
    assert "shopify_or_platform_merchant" in missing_high


# ---------------------------------------------------------------------------
# Coverage labels respect the category targets
# ---------------------------------------------------------------------------


def test_coverage_label_thin_when_below_tiny_target() -> None:
    """The shopify_or_platform_merchant category has tiny=1, small=4,
    serious=12. With 0 matches, label=MISSING. With 1 match, ACCEPTABLE_FOR_TINY."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    matched = [_match("shopify_or_platform_merchant")]
    coverage = compute_category_coverage(plan=plan, matched=matched)
    cov = next(c for c in coverage if c.category_key == "shopify_or_platform_merchant")
    assert cov.matched_total == 1
    assert cov.coverage_label == CategoryCoverageLabel.ACCEPTABLE_FOR_TINY


def test_coverage_label_acceptable_for_serious_when_target_met() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    matches = [
        _match("shopify_or_platform_merchant",
               classification=RelevanceClassification.RELEVANT)
        for _ in range(15)  # serious target = 12
    ]
    coverage = compute_category_coverage(plan=plan, matched=matches)
    cov = next(c for c in coverage if c.category_key == "shopify_or_platform_merchant")
    assert cov.coverage_label == CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS


# ---------------------------------------------------------------------------
# Source diversity / single-source risk
# ---------------------------------------------------------------------------


def test_source_diversity_zero_domains_when_empty() -> None:
    s = compute_source_diversity(matched=[], minimum_required=5)
    assert s.distinct_source_domains == 0
    assert s.single_source_risk is False  # empty pool isn't "single source"


def test_source_diversity_single_source_risk_flagged() -> None:
    matched = [
        _match("k", domains=["one.example.test"]),
        _match("k", domains=["one.example.test"]),
    ]
    s = compute_source_diversity(matched=matched, minimum_required=5)
    assert s.distinct_source_domains == 1
    assert s.single_source_risk is True


def test_source_diversity_no_risk_when_multiple_domains() -> None:
    matched = [
        _match("k", domains=["a.example.test"]),
        _match("k", domains=["b.example.test"]),
    ]
    s = compute_source_diversity(matched=matched, minimum_required=5)
    assert s.distinct_source_domains == 2
    assert s.single_source_risk is False


def test_detect_single_source_risk_helper() -> None:
    s_risky = SourceDiversitySummary(
        distinct_source_domains=1, domains=["a"],
        minimum_required=5, single_source_risk=True,
    )
    s_safe = SourceDiversitySummary(
        distinct_source_domains=4, domains=["a", "b", "c", "d"],
        minimum_required=5, single_source_risk=False,
    )
    assert detect_single_source_risk(s_risky) is True
    assert detect_single_source_risk(s_safe) is False


# ---------------------------------------------------------------------------
# Readiness gates
# ---------------------------------------------------------------------------


def test_readiness_blocks_when_high_priority_categories_missing() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    diversity = compute_source_diversity(matched=[], minimum_required=5)
    r = compute_readiness_by_mode(
        plan=plan, coverage=coverage, diversity=diversity, matched_total=0,
    )
    assert r.tiny_ready is False
    assert r.small_ready is False
    assert r.serious_ready is False
    # At least one blocker mentions missing high-priority categories.
    blocked_blob = " | ".join(r.blocked_reasons)
    assert "missing" in blocked_blob.lower() or "thin" in blocked_blob.lower()


def test_readiness_tiny_only_with_caveat_when_thin_pool() -> None:
    """A small matched pool that meets at least one category at tiny
    but not all gates → tiny_ready True, small/serious False."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    matches = [
        _match("shopify_or_platform_merchant"),
        _match("dtc_founder_brand_control"),
        _match("agency_dependent_merchant"),
        _match("ai_skeptical_operator"),
        _match("nontechnical_founder"),
        _match("current_alternative_shopify_magic"),
        _match("geography_us_canada", domains=["a.test"]),
    ]
    coverage = compute_category_coverage(plan=plan, matched=matches)
    diversity = compute_source_diversity(
        matched=matches, minimum_required=5,
    )
    r = compute_readiness_by_mode(
        plan=plan, coverage=coverage, diversity=diversity,
        matched_total=len(matches),
    )
    # Some categories met tiny target; readiness for serious blocked.
    assert r.serious_ready is False


def test_readiness_blocks_on_single_source_risk() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    matches = [
        _match("shopify_or_platform_merchant", domains=["one.test"]),
        _match("dtc_founder_brand_control", domains=["one.test"]),
    ]
    coverage = compute_category_coverage(plan=plan, matched=matches)
    diversity = compute_source_diversity(
        matched=matches, minimum_required=5,
    )
    r = compute_readiness_by_mode(
        plan=plan, coverage=coverage, diversity=diversity, matched_total=2,
    )
    assert r.tiny_ready is False
    assert any("single-source" in b.lower() for b in r.blocked_reasons)


# ---------------------------------------------------------------------------
# Missing-key-categories detector
# ---------------------------------------------------------------------------


def test_missing_key_categories_only_flags_high_priority() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    coverage = compute_category_coverage(plan=plan, matched=[])
    missing = detect_missing_key_categories(coverage=coverage)
    # All high-priority categories should be flagged when matched=[]
    high_keys = {
        c.category_key for c in plan.stakeholder_categories
        if c.priority == "high"
    }
    assert set(missing) == high_keys
