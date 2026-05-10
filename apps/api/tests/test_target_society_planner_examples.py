"""Phase 8.2G — planner-on-examples tests.

Asserts the planner produces DIFFERENT, family-appropriate stakeholder
category sets for Amboras / water bottle / iPhone 17 / halal financing.
This is the primary regression test for the "not Amboras-only" rule.
"""
from __future__ import annotations

from assembly.pipeline.target_society import (
    ALL_EXAMPLES,
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    ProductFamily,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


# ---------------------------------------------------------------------------
# Family detection
# ---------------------------------------------------------------------------


def test_amboras_detected_as_commerce_platform() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    assert plan.interpreted_brief.detected_product_family is (
        ProductFamily.COMMERCE_PLATFORM_OR_TOOLING
    )


def test_water_bottle_detected_as_consumer_packaged_good() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    assert plan.interpreted_brief.detected_product_family is (
        ProductFamily.CONSUMER_PACKAGED_GOOD
    )


def test_iphone_detected_as_consumer_electronics() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    assert plan.interpreted_brief.detected_product_family is (
        ProductFamily.CONSUMER_ELECTRONICS
    )


def test_halal_financing_detected_as_financial_product() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    assert plan.interpreted_brief.detected_product_family is (
        ProductFamily.FINANCIAL_PRODUCT
    )


# ---------------------------------------------------------------------------
# Family-specific stakeholder categories
# ---------------------------------------------------------------------------


def test_amboras_categories_are_commerce_shaped() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert "shopify_or_platform_merchant" in keys
    assert "dtc_founder_brand_control" in keys
    assert "ai_skeptical_operator" in keys


def test_water_bottle_categories_are_consumer_shaped() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert "mass_market_grocery_buyer" in keys
    assert "premium_buyer" in keys
    assert "price_sensitive_buyer" in keys
    assert "sustainability_conscious_buyer" in keys
    assert "skeptical_rejector" in keys
    # Must NOT contain commerce-shaped categories.
    assert "shopify_or_platform_merchant" not in keys
    assert "dtc_founder_brand_control" not in keys


def test_iphone_categories_are_electronics_shaped() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert "current_product_user" in keys
    assert "competitor_user" in keys
    assert "upgrade_fatigued_buyer" in keys
    assert "ai_feature_skeptic" in keys
    # Must NOT contain commerce / consumer-packaged-good keys.
    assert "shopify_or_platform_merchant" not in keys
    assert "mass_market_grocery_buyer" not in keys


def test_halal_financing_categories_are_financial_shaped() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert "compliance_conscious_buyer" in keys
    assert "conventional_product_switcher" in keys
    assert "skeptical_borrower_or_investor" in keys
    # Must NOT contain commerce / consumer-packaged-good / electronics.
    assert "shopify_or_platform_merchant" not in keys
    assert "mass_market_grocery_buyer" not in keys
    assert "current_product_user" not in keys


# ---------------------------------------------------------------------------
# Generalization invariants — across ALL example briefs
# ---------------------------------------------------------------------------


def test_each_plan_has_at_least_4_stakeholder_categories() -> None:
    for key, brief in ALL_EXAMPLES:
        plan = build_target_society_plan(brief)
        n = len(plan.stakeholder_categories)
        assert n >= 4, (
            f"{key} plan has only {n} stakeholder categories (min 4)"
        )


def test_each_category_has_inclusion_and_exclusion_signals() -> None:
    for key, brief in ALL_EXAMPLES:
        plan = build_target_society_plan(brief)
        for c in plan.stakeholder_categories:
            assert c.inclusion_signals, (
                f"{key}/{c.category_key} missing inclusion_signals"
            )
            assert c.exclusion_signals, (
                f"{key}/{c.category_key} missing exclusion_signals"
            )


def test_each_plan_produces_a_distinct_category_set() -> None:
    """Pairwise: every two example briefs produce DIFFERENT category-
    key sets. The Amboras-only failure mode is repeated keys across
    products."""
    plans = {key: build_target_society_plan(brief) for key, brief in ALL_EXAMPLES}
    keysets = {
        key: frozenset(c.category_key for c in p.stakeholder_categories)
        for key, p in plans.items()
    }
    keys_list = list(keysets.keys())
    for i in range(len(keys_list)):
        for j in range(i + 1, len(keys_list)):
            a, b = keys_list[i], keys_list[j]
            assert keysets[a] != keysets[b], (
                f"plans for {a} and {b} produce identical category sets"
            )


def test_competitor_categories_are_emitted_when_competitors_named() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    # AMBORAS_BRIEF.competitors = ["Shopify Magic", "Conversion AI Tool"].
    assert any("current_alternative" in k for k in keys)


def test_geography_category_emitted_when_geography_provided() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    # Geography "California" → geography_california category key.
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert any(k.startswith("geography_") for k in keys)


def test_geography_category_omitted_when_geography_absent() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    # IPHONE_17_BRIEF.geography is None → no geography_ category.
    assert not any(k.startswith("geography_") for k in keys)


# ---------------------------------------------------------------------------
# Sensitive / protected-attribute handling
# ---------------------------------------------------------------------------


def test_halal_financing_categories_carry_sensitivity_notes() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    sensitive_count = sum(
        1 for c in plan.stakeholder_categories
        if c.sensitivity_or_compliance_notes
    )
    assert sensitive_count >= 4, (
        "Sensitive markers present → most categories should carry "
        f"compliance notes; only {sensitive_count} did."
    )


def test_water_bottle_categories_have_no_sensitivity_notes() -> None:
    """A non-sensitive brief must not get spurious compliance notes."""
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    # The financial-product family has a built-in
    # `_sensitivity_default=True` flag for compliance_conscious_buyer
    # only. For consumer-packaged-good there should be ZERO sensitive
    # category notes.
    sensitive_count = sum(
        1 for c in plan.stakeholder_categories
        if c.sensitivity_or_compliance_notes
    )
    assert sensitive_count == 0
