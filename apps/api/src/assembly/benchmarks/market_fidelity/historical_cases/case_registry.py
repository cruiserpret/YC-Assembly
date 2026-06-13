"""Phase 17D — candidate diversity guard (anti-cherry-pick).

A historical case-pack LIST that is only famous winners is worthless for a fair
benchmark. This checks the spread of candidate metadata (outcome class, category,
platform, fame) and warns when the set looks cherry-picked. Pure; advisory.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from assembly.benchmarks.market_fidelity.historical_cases.case_pack_schema import CandidateMetadata


def check_diversity(candidates: Sequence[CandidateMetadata]) -> dict:
    """Summarize the candidate spread + flag cherry-picking. ``balanced`` is True only
    when there are successes AND failures, ≥1 middling/uncertain case, ≥2 categories,
    and not an all-famous set."""
    n = len(candidates)
    by_outcome = Counter(c.expected_outcome_class for c in candidates)
    by_category = Counter(c.category for c in candidates)
    by_platform = Counter(c.platform for c in candidates)
    by_fame = Counter(c.fame_level for c in candidates)

    warnings: list[str] = []
    if n == 0:
        return {"n": 0, "balanced": False, "warnings": ["no candidates"],
                "by_outcome_class": {}, "by_category": {}, "by_platform": {}, "by_fame": {}}

    has_success = by_outcome.get("success", 0) > 0
    has_failure = by_outcome.get("failure", 0) > 0
    has_middling = by_outcome.get("middling", 0) + by_outcome.get("uncertain", 0) > 0
    famous = by_fame.get("famous", 0)

    if not has_success or not has_failure:
        warnings.append("set lacks BOTH successes and failures (cherry-pick risk)")
    if not has_middling:
        warnings.append("set has no middling/uncertain cases")
    if len(by_category) < 2:
        warnings.append("set spans fewer than 2 categories")
    if n >= 3 and famous == n:
        warnings.append("every candidate is 'famous' — include obscure/niche cases too")
    if n >= 3 and famous >= max(1, n - 1):
        warnings.append("set is dominated by famous cases (memorization-risk skew)")

    balanced = has_success and has_failure and has_middling and len(by_category) >= 2 and famous != n

    return {
        "n": n,
        "balanced": balanced,
        "warnings": warnings,
        "by_outcome_class": dict(by_outcome),
        "by_category": dict(by_category),
        "by_platform": dict(by_platform),
        "by_fame": dict(by_fame),
    }
