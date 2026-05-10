"""Phase 8.4A.2 — market-entry relevance scoring + threshold-discipline tests.

Asserts:
  * Market-entry weight profile sums to 8.0 (preserves max-40 band).
  * Market-entry profile prioritizes competitor + category-specific +
    pain-objection axes over role-context + price + geography.
  * `InclusionTier` thresholds match the existing relevance-thresholds:
      score >= 27       → CORE_RELEVANT
      score in [18, 27) → ADJACENT_RELEVANT
      score < 18        → EXCLUDED
  * Off-topic personas (zero category keywords) remain EXCLUDED under
    BOTH profiles (the weight rebalance does NOT loosen the bar).
  * A persona with strong competitor + category-specific evidence
    against a dynamically-generated competitor category clears
    CORE_RELEVANT.
  * A persona with only weak / partial market-entry signal lands at
    ADJACENT_RELEVANT.
  * Threshold (27 / 36) is unchanged in both profiles.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from assembly.pipeline.audience_retrieval.inclusion_tier import (
    InclusionTier,
    classify_inclusion_tier,
    classify_inclusion_tier_from_score,
)
from assembly.pipeline.audience_retrieval.scorer import (
    score_persona_against_category,
)
from assembly.pipeline.audience_retrieval.weights import (
    TOTAL_WEIGHT_SUM,
    derive_scorer_weights_for_plan,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.persona_relevance.rubric import (
    CLASSIFICATION_THRESHOLDS,
    RelevanceClassification,
)
from assembly.pipeline.target_society import build_target_society_plan
from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.dynamic_market_entry_planner import (
    build_dynamic_market_entry_categories,
)
from assembly.pipeline.target_society.schemas import ProductBriefInput


# ---------------------------------------------------------------------------
# Brief + persona fixtures
# ---------------------------------------------------------------------------


def _triton_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can. Targeted "
            "at college students, athletes, gym-goers, busy young adults."
        ),
        price_or_price_structure="$3.99 per can",
        competitors=["Red Bull", "Monster", "Celsius"],
        target_market_or_society="California consumers.",
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, "
            "pre-workout powders."
        ),
        simulation_goal=SimulationGoal.TEST_PRICE,
    )


def _persona(
    *,
    name: str,
    traits: dict[str, str],
    excerpts: list[str],
) -> PersonaAuditInput:
    pid = uuid4()
    trait_views = tuple(
        TraitView(
            field_name=fn,
            support_level="direct",
            value=v,
            confidence=0.9,
            source_ids=tuple(),
            rationale=None,
        )
        for fn, v in traits.items()
    )
    link_views = tuple(
        EvidenceLinkView(
            persona_id=pid,
            source_record_id=uuid4(),
            contribution_kind="direct",
            contribution_field=list(traits.keys())[0] if traits else "interests",
            excerpt=ex,
            source_likely_human_signal=True,
        )
        for ex in excerpts
    )
    return PersonaAuditInput(
        persona_id=pid,
        display_name=name,
        traits=trait_views,
        evidence_links=link_views,
    )


def _strong_red_bull_user() -> PersonaAuditInput:
    """Mock persona: explicit Red Bull user with multiple Red Bull
    mentions + role-context + competitor + category evidence."""
    return _persona(
        name="Strong Red Bull User",
        traits={
            "role_or_context": "Red Bull user and college student",
            "current_alternatives": "Red Bull is my daily energy drink",
            "objection_patterns": "Other energy drinks taste worse than Red Bull",
            "interests": "Red Bull, energy drinks, caffeine for studying",
            "price_sensitivity": "Red Bull is expensive but worth it",
        },
        excerpts=[
            "I drink Red Bull every day for studying. Red Bull works "
            "best for me. Red Bull's taste beats Monster.",
            "I tried Monster but Red Bull is the only energy drink I "
            "stick with as a college student.",
        ],
    )


def _weak_market_entry_persona() -> PersonaAuditInput:
    """Mock persona: only mentions a competitor once + thin role
    evidence. Should land in ADJACENT_RELEVANT or low CORE_RELEVANT."""
    return _persona(
        name="Weak Market-Entry Persona",
        traits={
            "interests": "I tried Monster once",
            "role_or_context": "general beverage consumer",
        },
        excerpts=[
            "Tried Monster a year ago. Wasn't sure about the taste.",
        ],
    )


def _off_topic_persona() -> PersonaAuditInput:
    """Mock persona: no category keywords at all. Must remain EXCLUDED."""
    return _persona(
        name="Off-Topic Persona",
        traits={
            "interests": "knitting, gardening, classical music",
            "role_or_context": "Retired teacher in Vermont",
        },
        excerpts=[
            "I love knitting wool sweaters in winter. Classical music "
            "calms me. Gardening is my passion.",
        ],
    )


# ---------------------------------------------------------------------------
# 1. Weight-profile invariants
# ---------------------------------------------------------------------------


def test_market_entry_weights_sum_to_8() -> None:
    weights = derive_scorer_weights_for_plan(
        has_competitors=True,
        has_geography=True,
        simulation_goal_is_price_test=True,
        is_market_entry=True,
    )
    total = sum(weights.values())
    assert abs(total - TOTAL_WEIGHT_SUM) < 0.01, (
        f"market-entry weights must sum to {TOTAL_WEIGHT_SUM}, got {total}"
    )


def test_classic_weights_sum_to_8_unchanged() -> None:
    """The classic (launched-product) weight profile is unchanged
    by Phase 8.4A.2 — sum still equals 8.0."""
    weights = derive_scorer_weights_for_plan(
        has_competitors=True,
        has_geography=True,
        simulation_goal_is_price_test=True,
        is_market_entry=False,
    )
    total = sum(weights.values())
    assert abs(total - TOTAL_WEIGHT_SUM) < 0.01


def test_market_entry_prioritizes_competitor_axis_over_role_context() -> None:
    """The forensic finding from 8.4A.1: role_context_match is unreliable
    for unlaunched-product personas (scored 0.0 across all 44). The
    market-entry weight profile compensates by making competitor
    evidence the highest-weight axis."""
    weights = derive_scorer_weights_for_plan(
        has_competitors=True,
        has_geography=True,
        simulation_goal_is_price_test=True,
        is_market_entry=True,
    )
    assert weights["current_alternative_match"] > weights["role_context_match"]
    assert weights["category_specific_match"] > weights["role_context_match"]
    assert weights["pain_objection_match"] > weights["price_budget_match"]
    assert weights["pain_objection_match"] > weights["geography_match"]


def test_market_entry_makes_geography_soft() -> None:
    """Geography is a SOFT bonus in market-entry mode — its weight is
    low so its absence doesn't gate the persona."""
    weights = derive_scorer_weights_for_plan(
        has_competitors=True,
        has_geography=True,
        simulation_goal_is_price_test=True,
        is_market_entry=True,
    )
    assert weights["geography_match"] < weights["category_specific_match"]
    assert weights["geography_match"] < weights["pain_objection_match"]


def test_market_entry_makes_price_soft() -> None:
    """Exact-price evidence is a bonus, not a gate, in market-entry mode."""
    weights = derive_scorer_weights_for_plan(
        has_competitors=True,
        has_geography=True,
        simulation_goal_is_price_test=True,
        is_market_entry=True,
    )
    # Price-budget weight in market-entry should be < competitor +
    # category-specific weights even when price-test goal is set.
    assert weights["price_budget_match"] < weights["current_alternative_match"]
    assert weights["price_budget_match"] < weights["category_specific_match"]


# ---------------------------------------------------------------------------
# 2. Threshold discipline
# ---------------------------------------------------------------------------


def test_global_thresholds_unchanged() -> None:
    """The classification thresholds (27 / 36) are NOT moved by
    Phase 8.4A.2. The user's non-negotiable rule."""
    assert CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.WEAKLY_RELEVANT
    ] == 18
    assert CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.RELEVANT
    ] == 27
    assert CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.HIGHLY_RELEVANT
    ] == 36


def test_inclusion_tier_thresholds_match_relevance_classification() -> None:
    assert classify_inclusion_tier_from_score(36) == InclusionTier.CORE_RELEVANT
    assert classify_inclusion_tier_from_score(27) == InclusionTier.CORE_RELEVANT
    assert classify_inclusion_tier_from_score(26) == InclusionTier.ADJACENT_RELEVANT
    assert classify_inclusion_tier_from_score(18) == InclusionTier.ADJACENT_RELEVANT
    assert classify_inclusion_tier_from_score(17) == InclusionTier.EXCLUDED
    assert classify_inclusion_tier_from_score(0) == InclusionTier.EXCLUDED
    assert classify_inclusion_tier_from_score(-5) == InclusionTier.EXCLUDED


def test_inclusion_tier_from_classification() -> None:
    assert (
        classify_inclusion_tier(RelevanceClassification.HIGHLY_RELEVANT)
        == InclusionTier.CORE_RELEVANT
    )
    assert (
        classify_inclusion_tier(RelevanceClassification.RELEVANT)
        == InclusionTier.CORE_RELEVANT
    )
    assert (
        classify_inclusion_tier(RelevanceClassification.WEAKLY_RELEVANT)
        == InclusionTier.ADJACENT_RELEVANT
    )
    assert (
        classify_inclusion_tier(RelevanceClassification.NOT_RELEVANT)
        == InclusionTier.EXCLUDED
    )


# ---------------------------------------------------------------------------
# 3. Anti-loosening guardrails: off-topic personas remain EXCLUDED
# ---------------------------------------------------------------------------


def test_off_topic_persona_remains_excluded_in_market_entry_mode() -> None:
    """A persona with zero category-relevant keywords scores below
    18 against ALL Triton categories under the market-entry weight
    profile. The weight rebalance does NOT rescue off-topic personas."""
    plan = build_target_society_plan(_triton_brief())
    persona = _off_topic_persona()
    best_score = -100
    for cat in plan.stakeholder_categories:
        bd = score_persona_against_category(
            persona, cat,
            geography_required=False,  # market-entry mode
            weights=plan.scorer_weights,
        )
        best_score = max(best_score, bd.total_score)
    assert best_score < 18, (
        f"off-topic persona should be EXCLUDED but scored {best_score}"
    )
    assert classify_inclusion_tier_from_score(best_score) == InclusionTier.EXCLUDED


def test_strong_competitor_persona_can_clear_core_relevant() -> None:
    """A persona with explicit Red Bull / category / role evidence
    should score against a `competitor_user_red_bull` category at
    CORE_RELEVANT under the market-entry weight profile."""
    plan = build_target_society_plan(_triton_brief())
    persona = _strong_red_bull_user()
    rb_cat = next(
        c for c in plan.stakeholder_categories
        if c.category_key == "competitor_user_red_bull"
    )
    bd = score_persona_against_category(
        persona, rb_cat,
        geography_required=False,
        weights=plan.scorer_weights,
    )
    # The persona has Red Bull mentions across multiple traits + excerpts;
    # under the market-entry profile this should clear the 27 threshold.
    assert bd.total_score >= 27, (
        f"strong Red Bull user must score CORE_RELEVANT against "
        f"competitor_user_red_bull, got {bd.total_score}"
    )
    assert classify_inclusion_tier_from_score(bd.total_score) == (
        InclusionTier.CORE_RELEVANT
    )


def test_weak_market_entry_persona_lands_in_adjacent_or_excluded() -> None:
    """A persona with only one passing competitor mention + thin role
    evidence should land in ADJACENT_RELEVANT (18-26) or below.
    Critical: should NOT clear CORE_RELEVANT (27+) on weak evidence."""
    plan = build_target_society_plan(_triton_brief())
    persona = _weak_market_entry_persona()
    best_score = -100
    for cat in plan.stakeholder_categories:
        bd = score_persona_against_category(
            persona, cat,
            geography_required=False,
            weights=plan.scorer_weights,
        )
        best_score = max(best_score, bd.total_score)
    # Weak persona must NOT clear CORE_RELEVANT.
    assert best_score < 27, (
        f"weak persona should not clear CORE_RELEVANT, got {best_score}"
    )


# ---------------------------------------------------------------------------
# 4. Cross-domain isolation: Triton-relevant persona does NOT match a
#    Shopify category (anti-cross-pollination)
# ---------------------------------------------------------------------------


def test_triton_persona_does_not_match_shopify_categories() -> None:
    """Strong Red Bull user persona should score very low against a
    Shopify-merchant category. Cross-domain isolation under the
    dynamic planner is preserved."""
    shopify_brief = ProductBriefInput(
        product_name="ShopBot",
        product_type="Shopify tool",
        product_description="Pre-launch SaaS for Shopify merchants.",
        price_or_price_structure="$29/month",
        competitors=["Klaviyo", "Mailchimp"],
        intended_user_or_buyer="Shopify merchants, DTC founders",
    )
    plan = build_target_society_plan(shopify_brief)
    persona = _strong_red_bull_user()
    best_score = -100
    for cat in plan.stakeholder_categories:
        bd = score_persona_against_category(
            persona, cat,
            geography_required=False,
            weights=plan.scorer_weights,
        )
        best_score = max(best_score, bd.total_score)
    # Red Bull persona should NOT clear CORE_RELEVANT in a Shopify-tool
    # plan — there's no category match.
    assert best_score < 27, (
        f"cross-domain leak: Red Bull persona scored {best_score} "
        f"against Shopify plan"
    )
