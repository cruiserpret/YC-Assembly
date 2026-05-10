"""Phase 8.4A.2 — dynamic market-entry society-planner tests.

Asserts the planner generates the right shape for multiple
product families using the SAME generic primitives:

  * Triton Drinks (energy drink) → competitor + substitute + use-case
    + universal-objection + universal-buyer-type + geography categories
  * Sunscreen in California → competitor + substitute + use-case +
    universal categories (no energy-drink leakage)
  * Shopify tool → competitor + use-case + universal categories
    (no energy-drink leakage)

NO product-family-specific code paths. NO hardcoded Triton template.
"""
from __future__ import annotations

import pytest

from assembly.pipeline.target_society import build_target_society_plan
from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.dynamic_market_entry_planner import (
    build_dynamic_market_entry_categories,
    looks_like_market_entry_brief,
    parse_substitutes_from_brief,
    parse_use_cases_from_brief,
)
from assembly.pipeline.target_society.schemas import ProductBriefInput


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------


def _triton_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can. Targeted "
            "at college students, athletes, gym-goers, and busy young "
            "adults."
        ),
        price_or_price_structure="$3.99 per can",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society=(
            "California consumers in the energy / sports / functional-"
            "beverage occasion."
        ),
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, "
            "pre-workout powders, electrolyte drinks."
        ),
        simulation_goal=SimulationGoal.TEST_PRICE,
    )


def _sunscreen_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Solara",
        product_type="$10 mineral sunscreen",
        product_description=(
            "Solara is a new unlaunched $10 mineral sunscreen "
            "launching in California. Reef-safe formula, SPF 50. "
            "Targeted at swimmers, beachgoers, and outdoor athletes "
            "who care about clean ingredients."
        ),
        price_or_price_structure="$10 per 4oz bottle",
        competitors=["Banana Boat", "Coppertone", "Neutrogena"],
        target_market_or_society=(
            "California consumers buying sunscreen for daily / beach / "
            "sport use."
        ),
        geography="California, United States",
        intended_user_or_buyer="swimmers, beachgoers, outdoor athletes",
        extra_context=(
            "Substitutes include: chemical sunscreen sprays, hats, "
            "shade umbrellas, UPF clothing."
        ),
        simulation_goal=SimulationGoal.TEST_PRICE,
    )


def _shopify_tool_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="ShopBot",
        product_type="Shopify tool",
        product_description=(
            "ShopBot is a new SaaS tool for Shopify merchants that "
            "automates product imports and inventory. Pre-launch."
        ),
        price_or_price_structure="$29/month",
        competitors=["Klaviyo", "Mailchimp", "WooCommerce"],
        target_market_or_society=(
            "Shopify merchants and DTC operators."
        ),
        geography=None,
        intended_user_or_buyer=(
            "Shopify merchants, DTC founders, e-commerce operators"
        ),
        extra_context="Substitutes include: in-house scripts, freelancers.",
        simulation_goal=SimulationGoal.TEST_PRODUCT_CONCEPT,
    )


# ---------------------------------------------------------------------------
# 1. Auto-detection of market-entry mode
# ---------------------------------------------------------------------------


def test_market_entry_auto_detected_from_unlaunched_text() -> None:
    """Brief whose description contains 'pre-launch' / 'unlaunched' /
    'new product' triggers market-entry mode regardless of goal."""
    brief = _shopify_tool_brief()  # description has "Pre-launch"
    assert looks_like_market_entry_brief(brief) is True


def test_market_entry_auto_detected_from_unlaunched_text_in_extra_context() -> None:
    """Brief whose extra_context contains 'unlaunched' triggers
    market-entry mode regardless of simulation_goal."""
    brief = _triton_brief().model_copy(update={
        "extra_context": (
            "Triton is unlaunched. Substitutes considered: cold brew."
        ),
    })
    assert looks_like_market_entry_brief(brief) is True


def test_market_entry_NOT_auto_detected_from_competitors_alone() -> None:
    """Phase 8.4A.2: a brief with competitors + TEST_PRICE alone is
    NOT enough to trigger market-entry mode. Auto-detection requires
    explicit goal OR explicit text hint ('unlaunched' / 'pre-launch' /
    'new brand'). This avoids capturing launched-product price-tests
    (water bottle, sunscreen relaunch, etc.)."""
    brief = ProductBriefInput(
        product_name="Existing Brand",
        product_type="bottled water",
        product_description="Existing flagship product, well-known.",
        price_or_price_structure="$1.99",
        competitors=["Aquafina", "Dasani"],
        geography="California",
        intended_user_or_buyer="general consumers",
        simulation_goal=SimulationGoal.TEST_PRICE,
    )
    assert looks_like_market_entry_brief(brief) is False


def test_market_entry_NOT_auto_detected_for_test_trust_goal() -> None:
    """TEST_TRUST_OBJECTION_BARRIERS (Amboras pattern) is for
    launched products and explicitly does NOT trigger market-entry."""
    brief = _triton_brief().model_copy(update={
        "simulation_goal": SimulationGoal.TEST_TRUST_OBJECTION_BARRIERS,
        "product_description": (
            "Triton is the existing flagship product."  # no unlaunched markers
        ),
        "extra_context": None,
    })
    assert looks_like_market_entry_brief(brief) is False


# ---------------------------------------------------------------------------
# 2. Triton — dynamic categories shape
# ---------------------------------------------------------------------------


def test_triton_brief_emits_competitor_categories_for_each_competitor() -> None:
    cats = build_dynamic_market_entry_categories(_triton_brief())
    keys = {c.category_key for c in cats}
    for competitor_slug in (
        "competitor_user_red_bull",
        "competitor_user_monster",
        "competitor_user_celsius",
        "competitor_user_prime",
        "competitor_user_gatorade",
    ):
        assert competitor_slug in keys, (
            f"missing competitor category {competitor_slug}"
        )


def test_triton_brief_emits_substitute_categories_from_extra_context() -> None:
    cats = build_dynamic_market_entry_categories(_triton_brief())
    keys = {c.category_key for c in cats}
    for sub_slug in (
        "substitute_user_cold_brew",
        "substitute_user_coffee",
        "substitute_user_pre_workout_powders",
        "substitute_user_electrolyte_drinks",
    ):
        assert sub_slug in keys, f"missing substitute category {sub_slug}"


def test_triton_brief_emits_use_case_categories() -> None:
    cats = build_dynamic_market_entry_categories(_triton_brief())
    keys = {c.category_key for c in cats}
    for uc_slug in (
        "use_case_college_students",
        "use_case_athletes",
        "use_case_gym_goers",
        "use_case_busy_young_adults",
    ):
        assert uc_slug in keys, f"missing use-case category {uc_slug}"


def test_triton_brief_emits_universal_objection_and_buyer_type_categories() -> None:
    cats = build_dynamic_market_entry_categories(_triton_brief())
    keys = {c.category_key for c in cats}
    for k in (
        "objection_price_sensitive",
        "objection_taste_or_quality_skeptic",
        "objection_health_or_safety_skeptic",
        "objection_brand_loyalist_or_skeptic",
        "objection_category_rejector",
        "buyer_type_switcher",
        "buyer_type_impulse_or_convenience_buyer",
        "buyer_type_premium_or_status_buyer",
    ):
        assert k in keys, f"missing universal category {k}"


def test_triton_geography_is_soft_modifier_with_zero_minimums() -> None:
    cats = build_dynamic_market_entry_categories(_triton_brief())
    geo_cats = [c for c in cats if c.category_key.startswith("geography_")]
    assert len(geo_cats) == 1
    g = geo_cats[0]
    # Geography is a SOFT modifier — zero persona minimums in market-entry
    # mode so it never gates readiness.
    assert g.minimum_persona_target_tiny == 0
    assert g.minimum_persona_target_small == 0
    assert g.minimum_persona_target_serious == 0
    assert g.priority == "low"


# ---------------------------------------------------------------------------
# 3. Generalization — sunscreen brief uses SAME primitives
# ---------------------------------------------------------------------------


def test_sunscreen_brief_emits_sunscreen_competitor_categories() -> None:
    cats = build_dynamic_market_entry_categories(_sunscreen_brief())
    keys = {c.category_key for c in cats}
    for k in (
        "competitor_user_banana_boat",
        "competitor_user_coppertone",
        "competitor_user_neutrogena",
    ):
        assert k in keys, f"missing sunscreen competitor {k}"


def test_sunscreen_brief_does_not_emit_energy_drink_categories() -> None:
    """Critical generalization test: sunscreen brief must NOT emit
    Red Bull / Monster / energy-drink-specific categories. The dynamic
    planner has no product-family-specific knowledge."""
    cats = build_dynamic_market_entry_categories(_sunscreen_brief())
    keys = {c.category_key for c in cats}
    for forbidden_slug in (
        "competitor_user_red_bull",
        "competitor_user_monster",
        "competitor_user_celsius",
        "use_case_gym_goers",
    ):
        assert forbidden_slug not in keys, (
            f"sunscreen brief leaked energy-drink category: {forbidden_slug}"
        )


def test_sunscreen_brief_emits_swimmer_beachgoer_use_cases() -> None:
    cats = build_dynamic_market_entry_categories(_sunscreen_brief())
    keys = {c.category_key for c in cats}
    assert "use_case_swimmers" in keys
    assert "use_case_beachgoers" in keys
    assert "use_case_outdoor_athletes" in keys


# ---------------------------------------------------------------------------
# 4. Generalization — Shopify tool brief
# ---------------------------------------------------------------------------


def test_shopify_brief_emits_klaviyo_mailchimp_woocommerce_categories() -> None:
    cats = build_dynamic_market_entry_categories(_shopify_tool_brief())
    keys = {c.category_key for c in cats}
    for k in (
        "competitor_user_klaviyo",
        "competitor_user_mailchimp",
        "competitor_user_woocommerce",
    ):
        assert k in keys


def test_shopify_brief_does_not_emit_energy_drink_categories() -> None:
    cats = build_dynamic_market_entry_categories(_shopify_tool_brief())
    keys = {c.category_key for c in cats}
    for forbidden in (
        "competitor_user_red_bull",
        "competitor_user_monster",
        "use_case_gym_goers",
        "use_case_athletes",
        "use_case_college_students",
    ):
        assert forbidden not in keys, (
            f"Shopify brief leaked energy-drink category: {forbidden}"
        )


def test_shopify_brief_omits_geography_when_brief_has_none() -> None:
    cats = build_dynamic_market_entry_categories(_shopify_tool_brief())
    geo_cats = [c for c in cats if c.category_key.startswith("geography_")]
    assert geo_cats == []


# ---------------------------------------------------------------------------
# 5. Anti-fake-customer guarantees
# ---------------------------------------------------------------------------


def test_no_category_uses_product_name_as_buyer_label() -> None:
    """For an unlaunched product, no category should call its buyers
    'Triton buyers' / 'Solara buyers' / 'ShopBot buyers'. Only
    competitor / substitute / use-case / objection / buyer-type /
    geography categories are valid."""
    for brief in (_triton_brief(), _sunscreen_brief(), _shopify_tool_brief()):
        cats = build_dynamic_market_entry_categories(brief)
        product_name_lower = brief.product_name.lower()
        product_slug = product_name_lower.replace(" ", "_")
        for c in cats:
            assert product_name_lower not in c.category_key.lower(), (
                f"{c.category_key} uses product name {brief.product_name!r}"
            )
            assert product_slug not in c.category_key.lower(), (
                f"{c.category_key} uses product slug for {brief.product_name!r}"
            )
            # Also: display_name should not call buyers <product> users
            # unless that's a substitute (we don't generate that).
            display_lower = c.display_name.lower()
            forbidden_phrases = (
                f"{product_name_lower} user",
                f"{product_name_lower} buyer",
                f"{product_name_lower} loyalist",
                f"{product_name_lower} reviewer",
            )
            for ph in forbidden_phrases:
                assert ph not in display_lower, (
                    f"{c.category_key} display name implies "
                    f"unlaunched-product loyalty: {c.display_name!r}"
                )


def test_dynamic_planner_is_deterministic() -> None:
    """Same brief → same category list (order + content)."""
    brief = _triton_brief()
    a = build_dynamic_market_entry_categories(brief)
    b = build_dynamic_market_entry_categories(brief)
    assert [c.category_key for c in a] == [c.category_key for c in b]


# ---------------------------------------------------------------------------
# 6. Parsing helpers
# ---------------------------------------------------------------------------


def test_parse_substitutes_handles_canonical_phrase() -> None:
    brief = _triton_brief()
    subs = parse_substitutes_from_brief(brief)
    assert "cold brew" in subs
    assert "coffee" in subs
    assert "pre-workout powders" in subs
    assert "electrolyte drinks" in subs


def test_parse_substitutes_drops_competitor_dupes() -> None:
    """If a name appears in both brief.competitors and the substitute
    text, the substitute parser should suppress the duplicate."""
    brief = _triton_brief().model_copy(update={
        "competitors": ["Red Bull", "Monster", "Celsius"],
        "extra_context": (
            "Substitutes include Celsius, cold brew, coffee, "
            "and electrolyte drinks."
        ),
    })
    subs = parse_substitutes_from_brief(brief)
    # Celsius is already a competitor; should NOT appear in substitutes.
    assert "Celsius" not in subs
    assert "cold brew" in subs


def test_parse_use_cases_only_from_intended_user_buyer() -> None:
    """Use-cases are parsed only from `intended_user_or_buyer` —
    `target_market_or_society` is too noisy."""
    brief = _triton_brief()
    ucs = parse_use_cases_from_brief(brief)
    assert ucs == [
        "college students", "athletes", "gym-goers", "busy young adults",
    ]


def test_parse_use_cases_requires_role_shaped_noun() -> None:
    """Generic words like 'sports' / 'occasion' are rejected — they
    aren't role-shaped nouns."""
    brief = _triton_brief().model_copy(update={
        "intended_user_or_buyer": (
            "college students, sports, occasion, busy young adults"
        ),
    })
    ucs = parse_use_cases_from_brief(brief)
    assert "college students" in ucs
    assert "busy young adults" in ucs
    assert "sports" not in ucs
    assert "occasion" not in ucs


# ---------------------------------------------------------------------------
# 7. End-to-end: build_target_society_plan routes correctly
# ---------------------------------------------------------------------------


def test_build_target_society_plan_uses_dynamic_planner_for_market_entry() -> None:
    plan = build_target_society_plan(_triton_brief())
    keys = {c.category_key for c in plan.stakeholder_categories}
    # Dynamic-planner sentinels:
    assert any(k.startswith("competitor_user_") for k in keys)
    assert any(k.startswith("use_case_") for k in keys)
    assert any(k.startswith("objection_") for k in keys)
    # NOT the classic CPG-template categories:
    assert "mass_market_grocery_buyer" not in keys
    assert "current_alternative_red_bull" not in keys


def test_build_target_society_plan_keeps_classic_for_launched_brief() -> None:
    """A brief with TEST_TRUST_OBJECTION_BARRIERS goal AND no
    market-entry text hints uses the classic family-template path
    (Amboras-style backward compatibility). The dynamic-planner
    sentinels (`competitor_user_*`, `use_case_*`, `objection_*`)
    must NOT appear."""
    brief = ProductBriefInput(
        product_name="Old Product",
        product_type="Existing CPG",
        product_description="Existing flagship product, well-known.",
        price_or_price_structure="$5",
        competitors=["Brand A", "Brand B"],
        geography="California",
        intended_user_or_buyer="general consumers",
        simulation_goal=SimulationGoal.TEST_TRUST_OBJECTION_BARRIERS,
    )
    plan = build_target_society_plan(brief)
    keys = {c.category_key for c in plan.stakeholder_categories}
    # Dynamic-planner-specific sentinels must NOT be in classic plan:
    assert not any(k.startswith("competitor_user_") for k in keys)
    assert not any(k.startswith("use_case_") for k in keys)
    assert not any(k.startswith("objection_") for k in keys)
    assert not any(k.startswith("buyer_type_") for k in keys)
    # The classic planner emits its own keys (e.g.
    # `current_alternative_<competitor>`, `primary_target_buyer`,
    # `price_sensitive_buyer`). Validate the classic path produced
    # SOME categories.
    assert len(keys) >= 3


def test_market_entry_softens_geography_requirement() -> None:
    plan = build_target_society_plan(_triton_brief())
    assert plan.coverage_requirements.geography_coverage_required is False


def test_market_entry_weight_profile_prioritizes_competitor_axis() -> None:
    """In market-entry mode, current_alternative_match should carry
    the highest weight (competitor evidence is the load-bearing
    relevance signal)."""
    plan = build_target_society_plan(_triton_brief())
    weights = plan.scorer_weights
    # current_alternative_match should be the highest-weight axis.
    assert weights["current_alternative_match"] > weights["role_context_match"]
    assert weights["current_alternative_match"] > weights["price_budget_match"]
    assert weights["current_alternative_match"] > weights["geography_match"]
