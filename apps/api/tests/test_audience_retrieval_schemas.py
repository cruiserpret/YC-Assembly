"""Phase 8.2H — schema tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverage,
    CategoryCoverageLabel,
    NextStepRecommendation,
    PersonaExclusion,
    PersonaMatch,
    ReadinessByMode,
    SourceDiversitySummary,
    TopUpRecommendation,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification


def test_persona_match_validates() -> None:
    PersonaMatch(
        persona_id="abc",
        display_name="x",
        matched_category_key="k",
        matched_category_display_name="K",
        relevance_score=27,
        classification=RelevanceClassification.RELEVANT,
        evidence_link_count=3,
        why_included="why",
    )


def test_persona_match_score_bounds() -> None:
    """Negative scores allowed (exclusion penalty); but bound at -20."""
    PersonaMatch(
        persona_id="x", display_name="x",
        matched_category_key="k", matched_category_display_name="K",
        relevance_score=-20,
        classification=RelevanceClassification.NOT_RELEVANT,
        evidence_link_count=0, why_included="x",
    )
    with pytest.raises(ValidationError):
        PersonaMatch(
            persona_id="x", display_name="x",
            matched_category_key="k", matched_category_display_name="K",
            relevance_score=-21,  # below -20
            classification=RelevanceClassification.NOT_RELEVANT,
            evidence_link_count=0, why_included="x",
        )


def test_persona_match_classification_must_be_known() -> None:
    with pytest.raises(ValidationError):
        PersonaMatch(
            persona_id="x", display_name="x",
            matched_category_key="k", matched_category_display_name="K",
            relevance_score=27,
            classification="not_a_classification",
            evidence_link_count=0, why_included="x",
        )


def test_persona_match_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        PersonaMatch(
            persona_id="x", display_name="x",
            matched_category_key="k", matched_category_display_name="K",
            relevance_score=27,
            classification=RelevanceClassification.RELEVANT,
            evidence_link_count=0, why_included="x",
            extra_field="oops",  # type: ignore[call-arg]
        )


def test_persona_exclusion_validates() -> None:
    PersonaExclusion(
        persona_id="x", display_name="x",
        exclusion_reason="below threshold",
        best_possible_category="k",
        score=10,
    )


def test_category_coverage_label_values() -> None:
    assert CategoryCoverageLabel("missing") == CategoryCoverageLabel.MISSING
    assert CategoryCoverageLabel("acceptable_for_serious") == (
        CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS
    )
    with pytest.raises(ValueError):
        CategoryCoverageLabel("not_a_label")


def test_category_coverage_validates() -> None:
    CategoryCoverage(
        category_key="k", display_name="K", priority="high",
        required_min_tiny=1, required_min_small=3, required_min_serious=10,
        matched_highly_relevant=2, matched_relevant=1, matched_weak=0,
        matched_total=3,
        coverage_label=CategoryCoverageLabel.ACCEPTABLE_FOR_SMALL,
    )


def test_category_coverage_priority_must_be_known() -> None:
    with pytest.raises(ValidationError):
        CategoryCoverage(
            category_key="k", display_name="K", priority="urgent",
            required_min_tiny=1, required_min_small=3, required_min_serious=10,
            matched_highly_relevant=0, matched_relevant=0, matched_weak=0,
            matched_total=0,
            coverage_label=CategoryCoverageLabel.MISSING,
        )


def test_topup_recommendation_requires_category_key_and_queries() -> None:
    TopUpRecommendation(
        stakeholder_category_key="k",
        reason_for_topup="r",
        suggested_queries=["q1", "q2"],
        max_records_suggested=20,
        expected_persona_yield_range="3-6",
    )
    with pytest.raises(ValidationError):
        TopUpRecommendation(
            stakeholder_category_key="",  # empty
            reason_for_topup="r",
            suggested_queries=["q1"],
            max_records_suggested=20,
            expected_persona_yield_range="x",
        )


def test_topup_recommendation_max_records_bounds() -> None:
    with pytest.raises(ValidationError):
        TopUpRecommendation(
            stakeholder_category_key="k",
            reason_for_topup="r",
            suggested_queries=["q"],
            max_records_suggested=0,  # below 1
            expected_persona_yield_range="x",
        )
    with pytest.raises(ValidationError):
        TopUpRecommendation(
            stakeholder_category_key="k",
            reason_for_topup="r",
            suggested_queries=["q"],
            max_records_suggested=201,  # above 200
            expected_persona_yield_range="x",
        )


def test_readiness_by_mode_validates() -> None:
    ReadinessByMode(
        tiny_ready=True, small_ready=False, serious_ready=False,
    )


def test_source_diversity_validates() -> None:
    SourceDiversitySummary(
        distinct_source_domains=3, domains=["a", "b", "c"],
        minimum_required=5, single_source_risk=False,
    )


def test_next_step_recommendation_values_are_known() -> None:
    assert NextStepRecommendation("D_run_topup_ingestion_first")
    with pytest.raises(ValueError):
        NextStepRecommendation("not_a_label")
