"""Phase 8.2F.7 — rubric tests (pure)."""
from __future__ import annotations

import pytest

from assembly.pipeline.persona_relevance.rubric import (
    CLASSIFICATION_THRESHOLDS,
    RelevanceClassification,
    SCORE_FIELDS,
    SCORE_MAX_PER_FIELD,
    STAKEHOLDER_CATEGORIES,
    TOTAL_MAX,
    classify_total_score,
)


def test_total_max_is_45() -> None:
    assert TOTAL_MAX == 45
    assert SCORE_MAX_PER_FIELD == 5
    assert len(SCORE_FIELDS) == 9


def test_score_fields_are_unique() -> None:
    assert len(set(SCORE_FIELDS)) == len(SCORE_FIELDS)


def test_classification_threshold_boundaries() -> None:
    """Inclusive lower bounds match the spec exactly."""
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.NOT_RELEVANT] == 0
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.WEAKLY_RELEVANT] == 18
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.RELEVANT] == 27
    assert CLASSIFICATION_THRESHOLDS[RelevanceClassification.HIGHLY_RELEVANT] == 36


@pytest.mark.parametrize("score, expected", [
    (0, RelevanceClassification.NOT_RELEVANT),
    (5, RelevanceClassification.NOT_RELEVANT),
    (17, RelevanceClassification.NOT_RELEVANT),
    (18, RelevanceClassification.WEAKLY_RELEVANT),
    (26, RelevanceClassification.WEAKLY_RELEVANT),
    (27, RelevanceClassification.RELEVANT),
    (35, RelevanceClassification.RELEVANT),
    (36, RelevanceClassification.HIGHLY_RELEVANT),
    (45, RelevanceClassification.HIGHLY_RELEVANT),
])
def test_classify_total_score_boundaries(score: int, expected: RelevanceClassification) -> None:
    assert classify_total_score(score) is expected


def test_classify_total_score_rejects_out_of_bounds() -> None:
    with pytest.raises(ValueError):
        classify_total_score(-1)
    with pytest.raises(ValueError):
        classify_total_score(46)


def test_stakeholder_categories_minimum_count() -> None:
    """Phase 8.2F.7 requires coverage across diverse commerce stakeholders."""
    assert len(STAKEHOLDER_CATEGORIES) >= 8
    # All entries unique:
    assert len(set(STAKEHOLDER_CATEGORIES)) == len(STAKEHOLDER_CATEGORIES)
