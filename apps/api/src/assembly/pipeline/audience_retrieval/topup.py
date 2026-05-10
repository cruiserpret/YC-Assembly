"""Phase 8.2H — top-up recommendation builder.

Pure. Takes the coverage audit + the TargetSocietyPlan, produces a
list of `TopUpRecommendation` items for categories that need more
data. **Does NOT execute any ingestion.**
"""
from __future__ import annotations

from collections.abc import Sequence

from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverage,
    CategoryCoverageLabel,
    TopUpRecommendation,
)
from assembly.pipeline.target_society.constants import (
    WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN,
    WARNING_SENSITIVE_TARGETING_CAVEAT,
)
from assembly.pipeline.target_society.schemas import (
    StakeholderCategory,
    TargetSocietyPlan,
)


_TOPUP_LABELS = (
    CategoryCoverageLabel.MISSING,
    CategoryCoverageLabel.THIN,
    CategoryCoverageLabel.ACCEPTABLE_FOR_TINY,
)


def build_topup_recommendations(
    *,
    plan: TargetSocietyPlan,
    coverage: Sequence[CategoryCoverage],
) -> list[TopUpRecommendation]:
    """One TopUpRecommendation per missing/thin/tiny-only category."""
    has_sensitive = _plan_has_sensitive_marker(plan)
    by_key = {c.category_key: c for c in plan.stakeholder_categories}
    qplan_by_key = {q.category_key: q for q in plan.source_query_plan}

    out: list[TopUpRecommendation] = []
    for cov in coverage:
        if cov.coverage_label not in _TOPUP_LABELS:
            continue
        cat = by_key.get(cov.category_key)
        if cat is None:
            continue
        qplan = qplan_by_key.get(cov.category_key)
        suggested_queries = (
            list(qplan.queries) if qplan is not None
            else cov.recommended_topup_queries
        )
        max_records = _suggested_max_records(cat, cov)
        yield_range = _expected_yield(cat, cov)

        # Compliance / sensitivity caveats.
        caveats: list[str] = []
        requires_extra_review = False
        if cat.sensitivity_or_compliance_notes:
            caveats.append(cat.sensitivity_or_compliance_notes)
            requires_extra_review = True
        if has_sensitive:
            caveats.append(
                "Brief carries sensitive-targeting caveats. Top-up "
                "ingestion for any category in this plan must keep "
                "queries broad and never solicit individual protected "
                "attributes."
            )
            requires_extra_review = True

        out.append(TopUpRecommendation(
            stakeholder_category_key=cat.category_key,
            reason_for_topup=_reason_text(cov),
            missing_signals=list(cov.missing_signals),
            suggested_queries=suggested_queries,
            preferred_source_types=(
                qplan.preferred_source_types if qplan is not None else []
            ),
            max_records_suggested=max_records,
            expected_persona_yield_range=yield_range,
            caveats=caveats,
            requires_extra_compliance_review=requires_extra_review,
        ))
    return out


def convert_target_society_queries_to_topup_plan(
    plan: TargetSocietyPlan,
) -> dict[str, list[str]]:
    """Helper: flatten the plan's source_query_plan into a
    {category_key: queries[]} map. Useful for tests + the operator
    script."""
    return {
        q.category_key: list(q.queries)
        for q in plan.source_query_plan
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan_has_sensitive_marker(plan: TargetSocietyPlan) -> bool:
    for w in plan.warnings_and_limitations:
        if w.code in (
            WARNING_SENSITIVE_TARGETING_CAVEAT,
            WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN,
        ):
            return True
    return False


def _suggested_max_records(
    cat: StakeholderCategory, cov: CategoryCoverage,
) -> int:
    """Heuristic: target at least the small_target * 4 (typical 1-in-4
    accepted rate) capped at 60 per category."""
    target = cat.minimum_persona_target_small
    return max(15, min(60, target * 4))


def _expected_yield(
    cat: StakeholderCategory, cov: CategoryCoverage,
) -> str:
    """A coarse human-readable yield range. Acknowledges the past
    pilot's measured rate of ~22% strong-signal among accepted
    Tavily snippets."""
    target = cat.minimum_persona_target_small
    return f"{max(1, target // 2)}–{target * 2} candidate persona shells"


def _reason_text(cov: CategoryCoverage) -> str:
    if cov.coverage_label is CategoryCoverageLabel.MISSING:
        return f"No existing personas match {cov.category_key}."
    if cov.coverage_label is CategoryCoverageLabel.THIN:
        return (
            f"Only {cov.matched_total} match(es) for {cov.category_key}; "
            f"below tiny target {cov.required_min_tiny}."
        )
    return (
        f"{cov.matched_total} match(es) reach tiny but not small for "
        f"{cov.category_key}."
    )
