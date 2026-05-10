"""Phase 8.2G — schema tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from assembly.pipeline.target_society.schemas import (
    CoverageRequirements,
    PersonaRetrievalPlan,
    ProductBriefInput,
    SimulationReadinessGates,
    SocietyPlanWarning,
    SourceQueryPlan,
    StakeholderCategory,
)
from assembly.pipeline.target_society.constants import (
    SimulationGoal,
    WarningSeverity,
)


def test_product_brief_input_validates_minimum_fields() -> None:
    b = ProductBriefInput(
        product_name="Test",
        product_description="A test product description",
    )
    assert b.product_name == "Test"
    assert b.competitors == []


def test_product_brief_input_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        ProductBriefInput(product_name="", product_description="x")


def test_product_brief_input_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProductBriefInput(
            product_name="x", product_description="x",
            unknown_field="value",  # type: ignore[call-arg]
        )


def test_stakeholder_category_requires_evidence_needed() -> None:
    with pytest.raises(ValidationError):
        StakeholderCategory(
            category_key="x",
            display_name="x",
            description="x",
            why_relevant="x",
            evidence_needed=[],
            inclusion_signals=["x"],
            exclusion_signals=["x"],
            minimum_persona_target_tiny=1,
            minimum_persona_target_small=1,
            minimum_persona_target_serious=1,
        )


def test_stakeholder_category_requires_inclusion_and_exclusion_signals() -> None:
    with pytest.raises(ValidationError):
        StakeholderCategory(
            category_key="x",
            display_name="x",
            description="x",
            why_relevant="x",
            evidence_needed=["e"],
            inclusion_signals=[],
            exclusion_signals=["x"],
            minimum_persona_target_tiny=1,
            minimum_persona_target_small=1,
            minimum_persona_target_serious=1,
        )
    with pytest.raises(ValidationError):
        StakeholderCategory(
            category_key="x",
            display_name="x",
            description="x",
            why_relevant="x",
            evidence_needed=["e"],
            inclusion_signals=["x"],
            exclusion_signals=[],
            minimum_persona_target_tiny=1,
            minimum_persona_target_small=1,
            minimum_persona_target_serious=1,
        )


def test_source_query_plan_requires_category_binding() -> None:
    """Empty category_key is a required-non-empty error."""
    with pytest.raises(ValidationError):
        SourceQueryPlan(category_key="", queries=["q"])


def test_source_query_plan_accepts_minimum_payload() -> None:
    p = SourceQueryPlan(category_key="some_category")
    assert p.category_key == "some_category"
    # Defaults should be empty lists.
    assert p.queries == []


def test_warning_validates_known_severity() -> None:
    SocietyPlanWarning(
        code="x", message="m", severity=WarningSeverity.CAVEAT,
    )
    with pytest.raises(ValidationError):
        SocietyPlanWarning(code="x", message="m", severity="not_a_severity")


def test_coverage_requirements_bounds() -> None:
    CoverageRequirements(
        minimum_categories_represented=4,
        minimum_strong_persona_signal_shells=10,
        minimum_source_diversity_domains=5,
        minimum_direct_inferred_traits_per_persona=3,
        geography_coverage_required=False,
        competitor_evidence_required=True,
        price_evidence_required=False,
        trust_objection_evidence_required=True,
    )
    with pytest.raises(ValidationError):
        CoverageRequirements(
            minimum_categories_represented=0,  # below minimum
            minimum_strong_persona_signal_shells=10,
            minimum_source_diversity_domains=5,
            minimum_direct_inferred_traits_per_persona=3,
            geography_coverage_required=False,
            competitor_evidence_required=True,
            price_evidence_required=False,
            trust_objection_evidence_required=True,
        )


def test_simulation_readiness_gates_minimums() -> None:
    SimulationReadinessGates(
        tiny_minimum_personas=6,
        small_minimum_personas=20,
        serious_minimum_personas=80,
        scaled_minimum_personas=250,
        block_if_single_source=True,
        block_if_key_category_missing=True,
        block_if_thin_geography=False,
        block_if_no_competitor_evidence=True,
        allow_tiny_mode_with_caveat=True,
    )
    with pytest.raises(ValidationError):
        SimulationReadinessGates(
            tiny_minimum_personas=0,  # below minimum
            small_minimum_personas=20,
            serious_minimum_personas=80,
            scaled_minimum_personas=250,
            block_if_single_source=True,
            block_if_key_category_missing=True,
            block_if_thin_geography=False,
            block_if_no_competitor_evidence=True,
            allow_tiny_mode_with_caveat=True,
        )


def test_persona_retrieval_plan_threshold_bounds() -> None:
    PersonaRetrievalPlan(minimum_relevance_threshold=0)
    PersonaRetrievalPlan(minimum_relevance_threshold=45)
    with pytest.raises(ValidationError):
        PersonaRetrievalPlan(minimum_relevance_threshold=-1)
    with pytest.raises(ValidationError):
        PersonaRetrievalPlan(minimum_relevance_threshold=46)
