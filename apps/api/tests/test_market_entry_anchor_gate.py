"""Phase 8.4A.3 — market-entry anchor gate tests.

Covers the 11 scenarios in the operator spec:
  1. competitor anchor passes
  2. substitute anchor passes
  3. use-case anchor passes
  4. category-objection anchor passes (adjacent or core depending on score)
  5. demographic-only failure (college student, no caffeine evidence)
  6. generic-athlete failure
  7. generic-consumer failure
  8. off-topic regression: known false positive (taurine ingredient
     comment) is downgraded to EXCLUDED
  9. true-relevant preservation (mock strong Red Bull user passes)
 10. threshold discipline (gate doesn't move 18 / 27)
 11. cross-domain: sunscreen + Shopify briefs use the SAME anchor
     mechanism with no energy-drink leakage
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from assembly.pipeline.audience_retrieval.anchor_detector import (
    ANCHOR_BUYER_TYPE,
    ANCHOR_CATEGORY_OBJECTION,
    ANCHOR_COMPETITOR,
    ANCHOR_SUBSTITUTE,
    ANCHOR_USE_CASE,
    detect_market_entry_anchors,
)
from assembly.pipeline.audience_retrieval.inclusion_tier import (
    InclusionTier,
)
from assembly.pipeline.audience_retrieval.market_entry_gate import (
    GATE_REASON_BELOW_THRESHOLD,
    GATE_REASON_INSUFFICIENT_EVIDENCE,
    GATE_REASON_NO_ANCHOR,
    GATE_REASON_PASS,
    apply_market_entry_inclusion_gate,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.target_society import build_target_society_plan
from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.schemas import ProductBriefInput


# ---------------------------------------------------------------------------
# Brief fixtures (must include 'unlaunched' in description so the
# planner routes to dynamic market-entry mode).
# ---------------------------------------------------------------------------


def _triton_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can."
        ),
        price_or_price_structure="$3.99 per can",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society="California consumers",
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, "
            "pre-workout powders, electrolyte drinks. Triton is unlaunched."
        ),
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


def _sunscreen_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Solara",
        product_type="$10 mineral sunscreen",
        product_description=(
            "Solara is an unlaunched $10 mineral sunscreen launching "
            "in California."
        ),
        price_or_price_structure="$10",
        competitors=["Banana Boat", "Coppertone", "Neutrogena"],
        intended_user_or_buyer=(
            "swimmers, beachgoers, outdoor athletes"
        ),
        geography="California, United States",
        extra_context=(
            "Substitutes include: chemical sunscreen sprays, hats, "
            "shade umbrellas, UPF clothing."
        ),
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


def _shopify_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="ShopBot",
        product_type="Shopify tool",
        product_description=(
            "ShopBot is an unlaunched SaaS for Shopify merchants."
        ),
        price_or_price_structure="$29/mo",
        competitors=["Klaviyo", "Mailchimp", "WooCommerce"],
        intended_user_or_buyer=(
            "Shopify merchants, DTC founders, e-commerce operators"
        ),
        extra_context="Substitutes include: in-house scripts, freelancers.",
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


# ---------------------------------------------------------------------------
# Persona helper
# ---------------------------------------------------------------------------


def _persona(
    *,
    name: str,
    traits: dict[str, str],
    excerpts: list[str],
) -> PersonaAuditInput:
    pid = uuid4()
    trait_views = tuple(
        TraitView(
            field_name=fn, support_level="direct", value=v,
            confidence=0.9, source_ids=tuple(), rationale=None,
        )
        for fn, v in traits.items()
    )
    link_views = tuple(
        EvidenceLinkView(
            persona_id=pid, source_record_id=uuid4(),
            contribution_kind="direct",
            contribution_field=(
                list(traits.keys())[0] if traits else "interests"
            ),
            excerpt=ex, source_likely_human_signal=True,
        )
        for ex in excerpts
    )
    return PersonaAuditInput(
        persona_id=pid, display_name=name,
        traits=trait_views, evidence_links=link_views,
    )


# ---------------------------------------------------------------------------
# 1. Competitor anchor — Red Bull / Monster persona passes
# ---------------------------------------------------------------------------


def test_competitor_anchor_red_bull_persona_passes() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Red Bull User",
        traits={
            "interests": "I drink Red Bull every day for studying.",
            "current_alternatives": "Red Bull is my daily energy drink.",
        },
        excerpts=[
            "I tried Monster but Red Bull is the only energy drink I "
            "stick with as a college student.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True
    assert ANCHOR_COMPETITOR in report.anchor_types


def test_competitor_anchor_via_inclusion_gate_at_core_score() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Red Bull User",
        traits={"interests": "Red Bull is my daily energy drink"},
        excerpts=["I drink Red Bull every day. Red Bull rules."],
    )
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=30,
    )
    assert result.final_tier == InclusionTier.CORE_RELEVANT
    assert result.reason == GATE_REASON_PASS


# ---------------------------------------------------------------------------
# 2. Substitute anchor passes
# ---------------------------------------------------------------------------


def test_substitute_anchor_pre_workout_persona_passes() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Pre-Workout User",
        traits={
            "interests": "I take pre-workout before every gym session.",
        },
        excerpts=[
            "Their powdered pre-workout is decent. I prefer canned "
            "pre-workout drinks.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True
    # Could match substitute (pre-workout) AND/OR use-case (gym).
    assert (
        ANCHOR_SUBSTITUTE in report.anchor_types
        or ANCHOR_USE_CASE in report.anchor_types
    )


# ---------------------------------------------------------------------------
# 3. Use-case anchor passes
# ---------------------------------------------------------------------------


def test_use_case_anchor_college_caffeine_persona_passes() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="College Caffeine User",
        traits={
            "interests": (
                "As a college student, I rely on caffeine for "
                "studying late at night."
            ),
        },
        excerpts=[
            "I'm a college student and I drink coffee for finals.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True
    # Should match either use_case or substitute (coffee).
    assert any(
        a in report.anchor_types
        for a in (ANCHOR_USE_CASE, ANCHOR_SUBSTITUTE)
    )


# ---------------------------------------------------------------------------
# 4. Category-objection anchor passes
# ---------------------------------------------------------------------------


def test_category_objection_anchor_taste_complaint_persona_passes() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Taste Skeptic",
        traits={
            "objection_patterns": (
                "Most energy drinks taste terrible — chemical, sweet "
                "aftertaste."
            ),
        },
        excerpts=[
            "The taste is gross. Sweet aftertaste makes me sick.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True
    # "energy drink", "taste", "flavor" → can match either substitute
    # (energy drink general) or category-objection (taste).
    assert (
        ANCHOR_CATEGORY_OBJECTION in report.anchor_types
        or ANCHOR_SUBSTITUTE in report.anchor_types
    )


# ---------------------------------------------------------------------------
# 5. Demographic-only failure (college student, NO caffeine evidence)
# ---------------------------------------------------------------------------


def test_demographic_only_college_student_with_no_caffeine_excluded() -> None:
    """A persona that says 'I'm a college student' but has zero
    caffeine / drink / energy-drink / coffee evidence MUST be
    excluded by the gate. Demographic alone is not an anchor."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Generic College Student",
        traits={
            "interests": "I like reading classic novels and walking my dog.",
            "communication_style": "thoughtful, polite",
        },
        excerpts=[
            "I'm working on my thesis about 19th-century literature.",
            "Walked the dog twice today. Pleasant weather.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    # No competitor / substitute / use-case-relevant terms in this
    # persona's text.
    assert report.has_anchor is False
    # Even at a high score (impossible, but test the gate logic):
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=30,
    )
    assert result.final_tier == InclusionTier.EXCLUDED
    assert result.reason == GATE_REASON_NO_ANCHOR


def test_demographic_only_college_student_passes_anchor_when_caffeine_evidence_added() -> None:
    """The same persona with explicit caffeine + study evidence DOES
    pass — confirming the test above isn't catching demographic-only
    by accident, it's catching the absence of category evidence."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="College Student with Caffeine Evidence",
        traits={
            "interests": (
                "As a college student studying for finals, "
                "caffeine is my lifeline."
            ),
        },
        excerpts=[
            "I'm a college student. I drink coffee every night during "
            "finals to keep studying late.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True


# ---------------------------------------------------------------------------
# 6. Generic athlete failure (no drink / performance / caffeine evidence)
# ---------------------------------------------------------------------------


def test_generic_athlete_with_no_drink_evidence_excluded() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Generic Athlete",
        traits={
            "interests": "I run marathons and lift weights. I love yoga.",
        },
        excerpts=[
            "Ran a half-marathon last weekend. Yoga keeps me centered.",
        ],
    )
    # Note: "weights" / "yoga" / "marathon" / "ran" don't match any
    # of the dynamic plan's anchor terms (competitor names like
    # Red Bull, substitute names like cold brew, use-case roles like
    # athlete / gym-goers — wait, "athlete" IS a use-case in the plan).
    # The anchor would fire ONLY if persona text contains 'athlete'
    # literally. Let me check the persona text...
    # The persona text doesn't say 'athlete' — it says 'I run' and
    # 'I lift' and 'yoga'. None of those are anchor terms.
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is False


# ---------------------------------------------------------------------------
# 7. Generic consumer failure (no energy / sports / caffeine evidence)
# ---------------------------------------------------------------------------


def test_generic_consumer_with_no_category_evidence_excluded() -> None:
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Generic Beverage Consumer",
        traits={
            "interests": "I enjoy beverages of various kinds.",
            "role_or_context": "general consumer",
        },
        excerpts=[
            "I prefer water with my meals. Sometimes juice on weekends.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is False
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=22,
    )
    assert result.final_tier == InclusionTier.EXCLUDED
    assert result.reason == GATE_REASON_NO_ANCHOR


# ---------------------------------------------------------------------------
# 8. Off-topic regression: the known false positive
# ---------------------------------------------------------------------------


def test_off_topic_taurine_persona_downgraded_to_excluded() -> None:
    """The Phase 8.4A.2 replay surfaced one off-topic persona
    (Oakley J., score=18) that landed in ADJACENT_RELEVANT despite
    its evidence being a generic ingredient comment ('taurine is
    probably the ingredient that's most likely to freak people out').
    The anchor gate must downgrade it to EXCLUDED."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Taurine Ingredient Commenter",
        traits={
            "interests": (
                "ingredient analysis, food science, body chemistry"
            ),
        },
        excerpts=[
            "taurine is probably the ingredient that's most likely "
            "to freak people out. However, it's a highly normal body "
            "component.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    # No competitor / substitute / use-case-role / category-objection
    # term appears in the excerpt.
    assert report.has_anchor is False
    # Score that would have been ADJACENT_RELEVANT (18..26):
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=18,
    )
    assert result.final_tier == InclusionTier.EXCLUDED
    assert result.reason == GATE_REASON_NO_ANCHOR


# ---------------------------------------------------------------------------
# 9. True-relevant preservation
# ---------------------------------------------------------------------------


def test_true_relevant_red_bull_persona_preserved_through_gate() -> None:
    """A persona with multi-trait + multi-excerpt Red Bull evidence
    must remain CORE_RELEVANT after gating."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Strong Red Bull User",
        traits={
            "role_or_context": "Red Bull user and college student",
            "current_alternatives": "Red Bull is my daily energy drink",
            "objection_patterns": (
                "Other energy drinks taste worse than Red Bull"
            ),
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
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=30,
    )
    assert result.final_tier == InclusionTier.CORE_RELEVANT
    assert result.reason == GATE_REASON_PASS
    assert result.anchor_report.has_anchor is True


def test_true_relevant_pre_workout_athlete_preserved_through_adjacent_gate() -> None:
    """An ADJACENT-tier persona with grounded pre-workout + gym
    evidence passes the gate (anchor + excerpt-grounded)."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Pre-Workout Athlete",
        traits={
            "interests": "pre-workout, gym, strength training",
        },
        excerpts=[
            "I take pre-workout before every gym session. Caffeine "
            "for the lift. Athletes I train with use Celsius too.",
        ],
    )
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=22,  # ADJACENT_RELEVANT band
    )
    assert result.final_tier == InclusionTier.ADJACENT_RELEVANT
    assert result.reason == GATE_REASON_PASS


# ---------------------------------------------------------------------------
# 10. Threshold discipline
# ---------------------------------------------------------------------------


def test_gate_does_not_change_score_thresholds() -> None:
    """Score 17 is BELOW the WEAKLY_RELEVANT floor (18). The gate
    must report EXCLUDED via `below_inclusion_threshold`, not via
    the anchor logic."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Strong Red Bull Persona at Below-Threshold Score",
        traits={"interests": "Red Bull is my daily drink"},
        excerpts=["I drink Red Bull every day."],
    )
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=17,  # below 18
    )
    assert result.final_tier == InclusionTier.EXCLUDED
    assert result.reason == GATE_REASON_BELOW_THRESHOLD


def test_gate_at_exactly_threshold_uses_anchor_logic() -> None:
    """Score exactly 18 is ADJACENT band. With anchor + grounded
    excerpt, persona passes. Without, downgraded."""
    plan = build_target_society_plan(_triton_brief())
    # With anchor:
    persona_with = _persona(
        name="Red Bull Persona at 18",
        traits={"interests": "Red Bull energy drinker"},
        excerpts=["I drink Red Bull daily."],
    )
    res_with = apply_market_entry_inclusion_gate(
        persona=persona_with, plan=plan, score=18,
    )
    assert res_with.final_tier == InclusionTier.ADJACENT_RELEVANT
    # Without anchor:
    persona_without = _persona(
        name="Generic Persona at 18",
        traits={"interests": "I like sunsets"},
        excerpts=["Sunsets are pretty."],
    )
    res_without = apply_market_entry_inclusion_gate(
        persona=persona_without, plan=plan, score=18,
    )
    assert res_without.final_tier == InclusionTier.EXCLUDED


# ---------------------------------------------------------------------------
# 11. Cross-domain generalization
# ---------------------------------------------------------------------------


def test_sunscreen_brief_competitor_anchor_works() -> None:
    plan = build_target_society_plan(_sunscreen_brief())
    persona = _persona(
        name="Banana Boat User",
        traits={"interests": "Banana Boat is my go-to sunscreen"},
        excerpts=[
            "I use Banana Boat at the beach every summer.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True
    assert ANCHOR_COMPETITOR in report.anchor_types
    # And: NO energy-drink anchor terms can appear in the matched list
    # for a sunscreen plan.
    matched_blob = " ".join(report.matched_anchor_terms).lower()
    assert "red bull" not in matched_blob
    assert "monster" not in matched_blob
    assert "celsius" not in matched_blob


def test_shopify_brief_competitor_anchor_works() -> None:
    plan = build_target_society_plan(_shopify_brief())
    persona = _persona(
        name="Klaviyo User",
        traits={"interests": "Klaviyo is our email tool of choice"},
        excerpts=["We've been on Klaviyo for 2 years for our DTC store."],
    )
    report = detect_market_entry_anchors(persona, plan)
    assert report.has_anchor is True
    assert ANCHOR_COMPETITOR in report.anchor_types


def test_red_bull_persona_does_not_anchor_against_shopify_plan() -> None:
    """Cross-domain isolation: a Red Bull-heavy persona must NOT
    fire the anchor gate when the plan is a Shopify-tool plan."""
    plan = build_target_society_plan(_shopify_brief())
    persona = _persona(
        name="Strong Red Bull User",
        traits={
            "interests": "Red Bull is my daily energy drink",
            "current_alternatives": "Red Bull, Monster, Celsius",
        },
        excerpts=[
            "I drink Red Bull every day. Monster is my backup.",
        ],
    )
    report = detect_market_entry_anchors(persona, plan)
    # Energy-drink terms aren't in the Shopify plan's anchor terms.
    # The anchor MIGHT fire on universal categories (objection_*,
    # buyer_type_*) if the persona contains those patterns — but Red
    # Bull doesn't, so anchor should be False or only fire on those
    # universal categories which are generic (and won't match a
    # Shopify-tool persona anyway since the persona has no Shopify
    # context).
    # Concretely: if anchor fires, it must NOT be competitor_anchor.
    if report.has_anchor:
        assert ANCHOR_COMPETITOR not in report.anchor_types


# ---------------------------------------------------------------------------
# 12. Below-threshold persona exits via threshold reason, not anchor
# ---------------------------------------------------------------------------


def test_persona_with_anchor_but_low_score_excluded_by_threshold() -> None:
    """A persona with a competitor anchor but score=10 is excluded
    via the `below_inclusion_threshold` reason — the anchor gate
    is a layer ON TOP of the score threshold, not a replacement."""
    plan = build_target_society_plan(_triton_brief())
    persona = _persona(
        name="Brief Red Bull Mention",
        traits={"interests": "I drink Red Bull sometimes"},
        excerpts=["Red Bull on Friday nights."],
    )
    result = apply_market_entry_inclusion_gate(
        persona=persona, plan=plan, score=10,
    )
    assert result.final_tier == InclusionTier.EXCLUDED
    assert result.reason == GATE_REASON_BELOW_THRESHOLD
