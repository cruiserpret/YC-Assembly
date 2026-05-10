"""Phase 8.2G — coverage + readiness-gate tests."""
from __future__ import annotations

from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


def test_readiness_gates_minimums_increase_by_mode() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    g = plan.simulation_readiness_gates
    assert g.tiny_minimum_personas < g.small_minimum_personas < (
        g.serious_minimum_personas
    ) < g.scaled_minimum_personas


def test_readiness_gates_block_when_competitors_named() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    assert plan.simulation_readiness_gates.block_if_no_competitor_evidence is True


def test_readiness_gates_do_not_block_competitor_when_no_competitors() -> None:
    no_comp = AMBORAS_BRIEF.model_copy(update={"competitors": []})
    plan = build_target_society_plan(no_comp)
    assert plan.simulation_readiness_gates.block_if_no_competitor_evidence is False


def test_readiness_gates_block_thin_geography_when_geography_provided() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)  # has geography
    assert plan.simulation_readiness_gates.block_if_thin_geography is True


def test_readiness_gates_do_not_block_geography_when_absent() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)  # no geography
    assert plan.simulation_readiness_gates.block_if_thin_geography is False


def test_coverage_geography_required_only_when_brief_has_geography() -> None:
    plan_geo = build_target_society_plan(WATER_BOTTLE_BRIEF)
    assert plan_geo.coverage_requirements.geography_coverage_required is True
    plan_no_geo = build_target_society_plan(IPHONE_17_BRIEF)
    assert plan_no_geo.coverage_requirements.geography_coverage_required is False


def test_coverage_competitor_required_only_when_competitors_named() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)  # has competitors
    assert plan.coverage_requirements.competitor_evidence_required is True
    no_comp = AMBORAS_BRIEF.model_copy(update={"competitors": []})
    plan_no = build_target_society_plan(no_comp)
    assert plan_no.coverage_requirements.competitor_evidence_required is False


def test_coverage_price_required_for_consumer_categories() -> None:
    """Consumer-packaged-good and consumer-electronics categories must
    require price evidence regardless of whether a price is in the brief."""
    plan_water = build_target_society_plan(WATER_BOTTLE_BRIEF)
    plan_iphone = build_target_society_plan(IPHONE_17_BRIEF)
    assert plan_water.coverage_requirements.price_evidence_required is True
    assert plan_iphone.coverage_requirements.price_evidence_required is True


def test_coverage_min_categories_at_least_4() -> None:
    for brief in (
        AMBORAS_BRIEF, WATER_BOTTLE_BRIEF, IPHONE_17_BRIEF, HALAL_FINANCING_BRIEF,
    ):
        plan = build_target_society_plan(brief)
        assert plan.coverage_requirements.minimum_categories_represented >= 4


def test_minimum_persona_targets_differ_per_mode() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    for c in plan.stakeholder_categories:
        assert (
            c.minimum_persona_target_tiny
            <= c.minimum_persona_target_small
            <= c.minimum_persona_target_serious
        ), (
            f"{c.category_key}: targets must monotonically increase "
            f"({c.minimum_persona_target_tiny}, "
            f"{c.minimum_persona_target_small}, "
            f"{c.minimum_persona_target_serious})"
        )


def test_serious_target_at_least_doubles_tiny_for_high_priority() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    for c in plan.stakeholder_categories:
        if c.priority == "high":
            assert (
                c.minimum_persona_target_serious
                >= c.minimum_persona_target_tiny * 2
            ), (
                f"high-priority {c.category_key}: serious target should "
                "be ≥ 2× tiny."
            )
