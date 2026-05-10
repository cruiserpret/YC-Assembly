"""Phase 8.2J — weighted scorer calibration tests.

Asserts:
  * thresholds (27 / 36 / 18) remain UNCHANGED
  * stakeholder categories remain UNCHANGED
  * uniform-weight default produces 8.2H-identical results (regression
    against the existing 8.2H test fixtures)
  * derived weights normalize to sum 8.0
  * weights vary by brief shape (competitors / geography / TEST_PRICE
    simulation goal)
  * cross-domain isolation: weighted scorer cannot rescue an Amboras-
    shape persona on a water-bottle / iPhone / halal plan (because
    role + pain + cat sub-scores are zero)
  * a near-miss Amboras-shape persona that is strong on role + pain +
    cat + source can clear 27 under the Amboras weight vector
  * a generic adjacent persona (source-strong but role/pain/cat all
    zero) still fails
"""
from __future__ import annotations

import math
from uuid import uuid4

from assembly.pipeline.audience_retrieval import (
    TOTAL_WEIGHT_SUM,
    UNIFORM_WEIGHTS,
    WEIGHTED_AXES,
    apply_weights_to_breakdown,
    classify_persona_match,
    derive_scorer_weights_for_plan,
    retrieve_personas_for_target_society,
    score_persona_against_category,
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
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


def _trait(field_name, support_level, value, source_ids=()):
    return TraitView(
        field_name=field_name, support_level=support_level, value=value,
        confidence=0.85, source_ids=tuple(source_ids),
    )


def _link(persona_id, source_id, *, excerpt, field):
    return EvidenceLinkView(
        persona_id=persona_id, source_record_id=source_id,
        contribution_kind="direct", contribution_field=field,
        excerpt=excerpt, source_likely_human_signal=True,
    )


# ---------------------------------------------------------------------------
# Threshold + stakeholder-category invariants
# ---------------------------------------------------------------------------


def test_relevance_thresholds_remain_27_36_18() -> None:
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.RELEVANT] == 27
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.HIGHLY_RELEVANT] == 36
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.WEAKLY_RELEVANT] == 18
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.NOT_RELEVANT] == 0


def test_amboras_minimum_relevance_threshold_in_plan_remains_27() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    assert plan.persona_retrieval_plan.minimum_relevance_threshold == 27


def test_amboras_stakeholder_categories_unchanged_after_8_2_j() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert {
        "shopify_or_platform_merchant", "dtc_founder_brand_control",
        "agency_dependent_merchant", "ai_skeptical_operator",
        "nontechnical_founder",
    }.issubset(keys)


# ---------------------------------------------------------------------------
# Uniform-weight default == Phase 8.2H behavior (regression guard)
# ---------------------------------------------------------------------------


def test_uniform_weights_sum_to_8_per_axis_count() -> None:
    """Uniform weights = 1.0 per axis × 8 axes = 8 total. The
    derived weights are also normalized to 8.0."""
    assert sum(UNIFORM_WEIGHTS.values()) == len(WEIGHTED_AXES)
    assert sum(UNIFORM_WEIGHTS.values()) == TOTAL_WEIGHT_SUM


def test_uniform_weighted_total_equals_raw_sum() -> None:
    """With uniform weights, weighted total = raw sum. This is the
    backwards-compat property the existing 8.2H tests rely on."""
    sub = {
        "role_context_match": 5, "pain_objection_match": 4,
        "current_alternative_match": 3, "price_budget_match": 2,
        "trust_trigger_match": 3, "category_specific_match": 5,
        "geography_match": 1, "source_strength": 5,
    }
    weighted = apply_weights_to_breakdown(sub, UNIFORM_WEIGHTS)
    assert weighted == sum(sub.values())


# ---------------------------------------------------------------------------
# Derived weights — normalization invariant
# ---------------------------------------------------------------------------


def test_derived_weights_always_sum_to_8() -> None:
    """Across every combination of (has_competitors, has_geography,
    simulation_goal_is_price_test), derived weights normalize to 8.0."""
    for has_comp in (True, False):
        for has_geo in (True, False):
            for is_price in (True, False):
                w = derive_scorer_weights_for_plan(
                    has_competitors=has_comp,
                    has_geography=has_geo,
                    simulation_goal_is_price_test=is_price,
                )
                assert math.isclose(sum(w.values()), TOTAL_WEIGHT_SUM, abs_tol=0.01), (
                    f"weights for ({has_comp}, {has_geo}, {is_price}) sum to "
                    f"{sum(w.values())}, expected ~8.0"
                )


def test_derived_weights_emphasize_role_pain_cat_source() -> None:
    """Across every brief shape, the four load-bearing axes
    (role + pain + cat + source) collectively exceed 50% of total."""
    for has_comp in (True, False):
        for has_geo in (True, False):
            for is_price in (True, False):
                w = derive_scorer_weights_for_plan(
                    has_competitors=has_comp,
                    has_geography=has_geo,
                    simulation_goal_is_price_test=is_price,
                )
                load_bearing = (
                    w["role_context_match"] + w["pain_objection_match"]
                    + w["category_specific_match"] + w["source_strength"]
                )
                assert load_bearing >= 4.0, (
                    f"load-bearing weight share too low: {load_bearing} for "
                    f"({has_comp}, {has_geo}, {is_price})"
                )


def test_price_weight_higher_when_test_price_goal() -> None:
    no_price = derive_scorer_weights_for_plan(
        has_competitors=True, has_geography=True,
        simulation_goal_is_price_test=False,
    )
    price = derive_scorer_weights_for_plan(
        has_competitors=True, has_geography=True,
        simulation_goal_is_price_test=True,
    )
    assert price["price_budget_match"] > no_price["price_budget_match"]


def test_geography_weight_higher_when_brief_has_geography() -> None:
    no_geo = derive_scorer_weights_for_plan(
        has_competitors=True, has_geography=False,
        simulation_goal_is_price_test=False,
    )
    geo = derive_scorer_weights_for_plan(
        has_competitors=True, has_geography=True,
        simulation_goal_is_price_test=False,
    )
    assert geo["geography_match"] > no_geo["geography_match"]


def test_alternative_weight_higher_when_competitors_named() -> None:
    no_comp = derive_scorer_weights_for_plan(
        has_competitors=False, has_geography=True,
        simulation_goal_is_price_test=False,
    )
    with_comp = derive_scorer_weights_for_plan(
        has_competitors=True, has_geography=True,
        simulation_goal_is_price_test=False,
    )
    assert with_comp["current_alternative_match"] > no_comp["current_alternative_match"]


# ---------------------------------------------------------------------------
# Per-axis behavior with weights
# ---------------------------------------------------------------------------


def test_zero_role_pain_cat_persona_cannot_clear_threshold_under_any_weights() -> None:
    """A persona that scores 0 on role + pain + cat axes cannot reach
    27 even if source_strength + price + trust + geo are all 5,
    because the load-bearing axes carry > 50% of total weight."""
    sub = {
        "role_context_match": 0, "pain_objection_match": 0,
        "category_specific_match": 0, "source_strength": 5,
        "current_alternative_match": 5, "trust_trigger_match": 5,
        "price_budget_match": 5, "geography_match": 5,
    }
    for has_comp in (True, False):
        for has_geo in (True, False):
            for is_price in (True, False):
                w = derive_scorer_weights_for_plan(
                    has_competitors=has_comp, has_geography=has_geo,
                    simulation_goal_is_price_test=is_price,
                )
                total = apply_weights_to_breakdown(sub, w)
                assert total < 27, (
                    f"zero-on-load-bearing-axes persona reached {total} "
                    f"(should be <27) under weights {w}"
                )


def test_strong_load_bearing_persona_can_clear_threshold_under_amboras_weights() -> None:
    """A persona strong on role + pain + cat + source (5 each) plus
    moderate elsewhere clears 27 under the Amboras weight vector."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    w = plan.scorer_weights
    sub = {
        "role_context_match": 5, "pain_objection_match": 5,
        "category_specific_match": 5, "source_strength": 5,
        "current_alternative_match": 3, "trust_trigger_match": 2,
        "price_budget_match": 1, "geography_match": 0,
    }
    total = apply_weights_to_breakdown(sub, w)
    assert total >= 27, (
        f"strong-load-bearing persona scored {total} under Amboras weights"
    )


# ---------------------------------------------------------------------------
# Cross-domain isolation — water bottle / iPhone / halal still reject Amboras
# ---------------------------------------------------------------------------


def _amboras_persona() -> PersonaAuditInput:
    pid = uuid4()
    s1, s2, s3, s4 = uuid4(), uuid4(), uuid4(), uuid4()
    return PersonaAuditInput(
        persona_id=pid, display_name="Tatum G.",
        traits=(
            _trait("role_or_context", "direct",
                   "Shopify merchant doing $30k/month", (s1,)),
            _trait("objection_patterns", "direct",
                   "frustrated with plugin bloat and too many apps", (s2,)),
            _trait("current_alternatives", "direct",
                   "Shopify apps Klaviyo Oberlo agency", (s3,)),
            _trait("price_sensitivity", "direct",
                   "high; cumulative monthly fees plus plugin fees", (s4,)),
            _trait("trust_triggers", "inferred",
                   "wants brand control and transparency", (s2,)),
            _trait("interests", "direct",
                   "ecommerce store management", (s1,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a Shopify merchant doing $30k/month"),
            _link(pid, s2, field="objection_patterns",
                  excerpt="my plugin stack is overwhelming"),
            _link(pid, s3, field="current_alternatives",
                  excerpt="using Klaviyo and Oberlo"),
            _link(pid, s4, field="price_sensitivity",
                  excerpt="cumulative monthly fees are expensive"),
        ),
    )


def test_amboras_persona_excluded_from_water_bottle_plan_under_weights() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=WATER_BOTTLE_BRIEF, plan=plan, personas=[persona],
    )
    assert len(result.matched_personas) == 0


def test_amboras_persona_excluded_from_iphone_plan_under_weights() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=IPHONE_17_BRIEF, plan=plan, personas=[persona],
    )
    assert len(result.matched_personas) == 0


def test_amboras_persona_excluded_from_halal_plan_under_weights() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=HALAL_FINANCING_BRIEF, plan=plan, personas=[persona],
    )
    assert len(result.matched_personas) == 0


def test_halal_plan_still_emits_sensitive_caveats_under_weights() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    result = retrieve_personas_for_target_society(
        brief=HALAL_FINANCING_BRIEF, plan=plan, personas=[],
    )
    has_sensitive = any(
        "sensitive" in w.lower() or "compliance" in w.lower()
        for w in result.warnings_and_caveats
    )
    assert has_sensitive


# ---------------------------------------------------------------------------
# Amboras-on-Amboras: a strong persona DOES clear under weights
# ---------------------------------------------------------------------------


def test_amboras_persona_clears_threshold_under_weighted_scorer() -> None:
    """The same Amboras-shape persona that already cleared the 27
    threshold under uniform weights (Phase 8.2H) should still clear
    under the weighted scorer (Phase 8.2J). This is the regression
    guard against any weighting that would degrade the existing
    relevant-persona pool."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=AMBORAS_BRIEF, plan=plan, personas=[persona],
    )
    assert len(result.matched_personas) >= 1


# ---------------------------------------------------------------------------
# Plan carries scorer_weights field
# ---------------------------------------------------------------------------


def test_plan_carries_scorer_weights_after_8_2_j() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    assert plan.scorer_weights is not None
    assert set(plan.scorer_weights.keys()) == set(WEIGHTED_AXES)
    assert math.isclose(sum(plan.scorer_weights.values()), 8.0, abs_tol=0.01)
