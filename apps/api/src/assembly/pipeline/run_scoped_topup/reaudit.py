"""Phase 8.2I — before/after re-audit comparison.

Pure function. Takes two `RunScopedAudienceRetrievalResult` instances
(one before top-up, one after) and produces a structured comparison.
"""
from __future__ import annotations

from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverageLabel,
    RunScopedAudienceRetrievalResult,
)
from assembly.pipeline.run_scoped_topup.schemas import (
    CategoryBeforeAfter,
    RunScopedReauditResult,
)


def compare_before_after(
    *,
    before: RunScopedAudienceRetrievalResult,
    after: RunScopedAudienceRetrievalResult,
) -> RunScopedReauditResult:
    """Per-category and per-mode delta. Pure function."""
    before_by_key = {c.category_key: c for c in before.category_coverage}
    after_by_key = {c.category_key: c for c in after.category_coverage}
    all_keys = sorted(set(before_by_key) | set(after_by_key))

    per_cat: list[CategoryBeforeAfter] = []
    for k in all_keys:
        b = before_by_key.get(k)
        a = after_by_key.get(k)
        b_total = b.matched_total if b is not None else 0
        a_total = a.matched_total if a is not None else 0
        per_cat.append(CategoryBeforeAfter(
            category_key=k,
            display_name=(a or b).display_name if (a or b) else k,
            before_matched=b_total,
            after_matched=a_total,
            delta=a_total - b_total,
            coverage_label_before=(
                b.coverage_label.value if b is not None
                else CategoryCoverageLabel.MISSING.value
            ),
            coverage_label_after=(
                a.coverage_label.value if a is not None
                else CategoryCoverageLabel.MISSING.value
            ),
        ))

    remaining_missing = [
        c.category_key
        for c in after.category_coverage
        if c.coverage_label in (
            CategoryCoverageLabel.MISSING, CategoryCoverageLabel.THIN,
        ) and c.priority == "high"
    ]

    new_caveats = [
        c for c in after.warnings_and_caveats
        if c not in set(before.warnings_and_caveats)
    ]

    before_n = len(before.matched_personas)
    after_n = len(after.matched_personas)

    return RunScopedReauditResult(
        before_matched_count=before_n,
        after_matched_count=after_n,
        matched_delta=after_n - before_n,
        before_tiny_ready=before.readiness_by_mode.tiny_ready,
        after_tiny_ready=after.readiness_by_mode.tiny_ready,
        before_small_ready=before.readiness_by_mode.small_ready,
        after_small_ready=after.readiness_by_mode.small_ready,
        before_serious_ready=before.readiness_by_mode.serious_ready,
        after_serious_ready=after.readiness_by_mode.serious_ready,
        per_category=per_cat,
        new_caveats=new_caveats,
        remaining_missing_categories=remaining_missing,
        next_step_recommendation_before=before.next_step_recommendation,
        next_step_recommendation_after=after.next_step_recommendation,
    )
