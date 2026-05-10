"""Phase 8.4A.2 — inclusion-tier presentation layer.

The `InclusionTier` enum maps existing `RelevanceClassification` values
(unchanged thresholds: 18 / 27 / 36) onto a market-entry-friendly
3-tier presentation:

  CORE_RELEVANT      — score >= 27 (RELEVANT or HIGHLY_RELEVANT)
                       Strong evidence-backed participant; usable in
                       simulation with normal weight.
  ADJACENT_RELEVANT  — score 18..26 (WEAKLY_RELEVANT)
                       Evidence-backed category / substitute / use-
                       case participant; usable for market-entry
                       simulation with REDUCED weight + caveat.
  EXCLUDED           — score < 18 (NOT_RELEVANT)
                       Off-topic, generic-only, or insufficient
                       evidence; not usable.

This is purely a presentation rename of the existing classification
buckets — the underlying thresholds (27 / 36) DO NOT MOVE. The
`ADJACENT_RELEVANT` tier is the existing `WEAKLY_RELEVANT` band; the
naming change makes its market-entry semantics explicit (use with
reduced weight, not "almost relevant").
"""
from __future__ import annotations

import enum

from assembly.pipeline.persona_relevance.rubric import (
    CLASSIFICATION_THRESHOLDS,
    RelevanceClassification,
)


class InclusionTier(str, enum.Enum):
    CORE_RELEVANT = "core_relevant"
    ADJACENT_RELEVANT = "adjacent_relevant"
    EXCLUDED = "excluded"


def classify_inclusion_tier(
    relevance: RelevanceClassification,
) -> InclusionTier:
    """Map a `RelevanceClassification` to an `InclusionTier`."""
    if relevance in (
        RelevanceClassification.RELEVANT,
        RelevanceClassification.HIGHLY_RELEVANT,
    ):
        return InclusionTier.CORE_RELEVANT
    if relevance == RelevanceClassification.WEAKLY_RELEVANT:
        return InclusionTier.ADJACENT_RELEVANT
    return InclusionTier.EXCLUDED


def classify_inclusion_tier_from_score(score: int) -> InclusionTier:
    """Map a raw integer total_score to an `InclusionTier`. The
    underlying thresholds (18 / 27) are unchanged from Phase 8.2J."""
    if score >= CLASSIFICATION_THRESHOLDS[RelevanceClassification.RELEVANT]:
        return InclusionTier.CORE_RELEVANT
    if score >= CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.WEAKLY_RELEVANT
    ]:
        return InclusionTier.ADJACENT_RELEVANT
    return InclusionTier.EXCLUDED


__all__ = [
    "InclusionTier",
    "classify_inclusion_tier",
    "classify_inclusion_tier_from_score",
]
