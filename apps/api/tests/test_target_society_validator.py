"""Phase 8.2G — validator tests."""
from __future__ import annotations

from copy import deepcopy

from assembly.pipeline.target_society import (
    ALL_EXAMPLES,
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    ProductFamily,
    SocietyPlanWarning,
    StakeholderCategory,
    WATER_BOTTLE_BRIEF,
    WarningSeverity,
    build_target_society_plan,
    validate_target_society_plan,
)
from assembly.pipeline.target_society.constants import (
    WARNING_SENSITIVE_TARGETING_CAVEAT,
)


# ---------------------------------------------------------------------------
# Happy path: every example brief produces a valid plan
# ---------------------------------------------------------------------------


def test_every_example_brief_produces_a_valid_plan() -> None:
    for key, brief in ALL_EXAMPLES:
        plan = build_target_society_plan(brief)
        r = validate_target_society_plan(plan, brief=brief)
        assert r.passed, (
            f"{key} plan failed validation: "
            + "; ".join(f"{v.rule_id}@{v.field_path}" for v in r.violations[:5])
        )


# ---------------------------------------------------------------------------
# Amboras-leak detection
# ---------------------------------------------------------------------------


def test_validator_rejects_amboras_categories_in_non_commerce_plan() -> None:
    """Manually construct a plan that pretends to be a water-bottle
    plan but uses Amboras-shape commerce categories. The validator
    must flag the leak."""
    real_water_plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    real_amboras_plan = build_target_society_plan(AMBORAS_BRIEF)
    leaked_plan = deepcopy(real_water_plan)
    # Inject 3 commerce-shaped categories.
    leak_keys = (
        "shopify_or_platform_merchant",
        "dtc_founder_brand_control",
        "ai_skeptical_operator",
    )
    leak_categories = [
        c for c in real_amboras_plan.stakeholder_categories
        if c.category_key in leak_keys
    ]
    assert len(leak_categories) == 3
    leaked_plan.stakeholder_categories = (
        leak_categories + leaked_plan.stakeholder_categories[:3]
    )
    r = validate_target_society_plan(leaked_plan, brief=WATER_BOTTLE_BRIEF)
    assert not r.passed
    assert any(
        v.rule_id == "target_society.amboras_leak" for v in r.violations
    )


# ---------------------------------------------------------------------------
# Forecast / verdict language
# ---------------------------------------------------------------------------


def test_validator_rejects_will_succeed_in_description() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    bad = deepcopy(plan)
    bad.stakeholder_categories[0] = bad.stakeholder_categories[0].model_copy(
        update={
            "description": (
                "This category will succeed in the simulation and the "
                "product will dominate."
            )
        },
    )
    r = validate_target_society_plan(bad, brief=WATER_BOTTLE_BRIEF)
    assert not r.passed
    assert any(
        v.rule_id == "target_society.forecast_or_verdict_language"
        for v in r.violations
    )


def test_validator_rejects_should_build_recommendation_in_description() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    bad = deepcopy(plan)
    bad.stakeholder_categories[0] = bad.stakeholder_categories[0].model_copy(
        update={
            "description": (
                "Based on this segment, we should build the product. "
                "It is a clear go decision."
            )
        },
    )
    r = validate_target_society_plan(bad, brief=WATER_BOTTLE_BRIEF)
    assert not r.passed


def test_validator_rejects_explicit_verdict_marker() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    bad = deepcopy(plan)
    bad.stakeholder_categories[0] = bad.stakeholder_categories[0].model_copy(
        update={
            "description": "Verdict: this category endorses the product.",
        },
    )
    r = validate_target_society_plan(bad, brief=WATER_BOTTLE_BRIEF)
    assert not r.passed


def test_validator_allows_negated_forecast_language() -> None:
    """Phrases like 'not a probabilistic forecast' (in CAVEAT warnings)
    must NOT trigger the validator. Without the fix, the prior regex
    matched bare 'forecast' anywhere."""
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    r = validate_target_society_plan(plan, brief=WATER_BOTTLE_BRIEF)
    assert r.passed
    # And confirm the LLM-simulation-limitation caveat is present
    # (it contains the word "forecast" in a negated context).
    assert any(
        "forecast" in w.message.lower()
        for w in plan.warnings_and_limitations
    )


def test_validator_allows_skeptical_rejector_description() -> None:
    """'rejects the product' inside a description of the
    skeptical_rejector stakeholder must NOT trigger the validator."""
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    keys = {c.category_key for c in plan.stakeholder_categories}
    assert "skeptical_rejector" in keys
    r = validate_target_society_plan(plan, brief=WATER_BOTTLE_BRIEF)
    assert r.passed


# ---------------------------------------------------------------------------
# Sensitive markers → must emit caveat warning
# ---------------------------------------------------------------------------


def test_halal_financing_plan_carries_sensitive_targeting_warning() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    codes = {w.code for w in plan.warnings_and_limitations}
    assert WARNING_SENSITIVE_TARGETING_CAVEAT in codes


def test_validator_rejects_sensitive_categories_without_caveat() -> None:
    """Strip the SENSITIVE_TARGETING_CAVEAT from a halal-financing plan
    while keeping the sensitive notes on categories — the validator
    must catch the mismatch."""
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    bad = deepcopy(plan)
    bad.warnings_and_limitations = [
        w for w in bad.warnings_and_limitations
        if w.code != WARNING_SENSITIVE_TARGETING_CAVEAT
    ]
    r = validate_target_society_plan(bad, brief=HALAL_FINANCING_BRIEF)
    assert not r.passed
    assert any(
        v.rule_id == "target_society.sensitive_category_missing_warning"
        for v in r.violations
    )


# ---------------------------------------------------------------------------
# Missing-input warnings
# ---------------------------------------------------------------------------


def test_validator_requires_missing_inputs_for_minimal_brief() -> None:
    """A brief with no geography / no competitors / no price must have
    those fields recorded in interpreted_brief.missing_inputs."""
    minimal = AMBORAS_BRIEF.model_copy(update={
        "geography": None,
        "competitors": [],
        "price_or_price_structure": None,
    })
    plan = build_target_society_plan(minimal)
    assert "geography" in plan.interpreted_brief.missing_inputs
    assert "competitors" in plan.interpreted_brief.missing_inputs
    assert "price_or_price_structure" in plan.interpreted_brief.missing_inputs
    r = validate_target_society_plan(plan, brief=minimal)
    assert r.passed


# ---------------------------------------------------------------------------
# Minimum-category threshold
# ---------------------------------------------------------------------------


def test_validator_rejects_plan_with_fewer_than_4_categories() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    bad = deepcopy(plan)
    bad.stakeholder_categories = bad.stakeholder_categories[:2]
    r = validate_target_society_plan(bad, brief=WATER_BOTTLE_BRIEF)
    assert not r.passed
    assert any(
        v.rule_id == "target_society.min_categories" for v in r.violations
    )
