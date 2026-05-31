"""Phase 15D0 — category-prior DIAGNOSTICS (measurement only, no live prior).

For each ``product_category`` in the validation ledger, summarize the average
observed vs predicted bucket distribution and the per-bucket error. This is the
diagnostic precursor to a learned category prior (Phase 15E) — but it applies
**no prior, no correction, and no forecast change**. Diagnostic only.

Anti-overfit: scored cases only; no product-name logic; no generalizable-prior
claim unless enough cases exist (surfaced as a warning). Reads observed
outcomes purely to MEASURE, never to alter output.

No LLM, no network, no DB, no randomness.
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from assembly.validation_ledger import compute_case_metrics
from assembly.validation_ledger import metrics as ledger_metrics
from assembly.validation_ledger.schema import ValidationCase

_BUCKETS = ledger_metrics.BUCKET_KEYS
_ACTION_GRADE_DENOMINATORS = frozenset({"backers"})
_LEVELS = ("insufficient", "weak", "moderate", "strong")


class CategoryPriorProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_category: str
    case_count: int
    observed_avg: dict[str, float] | None = None
    predicted_avg: dict[str, float] | None = None
    avg_signed_bucket_error: dict[str, float] | None = None
    avg_mae_pp: float | None = None
    confidence_level: str = "insufficient"
    warning: str | None = None


def _scored_cases(cases: Sequence[ValidationCase]) -> list[ValidationCase]:
    return [
        c for c in cases
        if c.metadata.validation_status == "scored" and c.is_scorable()
        and not c.anti_overfit.used_for_holdout
    ]


def _count_level(n: int) -> str:
    if n < 2:
        return "insufficient"
    if n < 4:
        return "weak"
    if n < 8:
        return "moderate"
    return "strong"


def _confidence_level(cases: Sequence[ValidationCase]) -> str:
    by_count = _count_level(len(cases))
    has_action = any(
        c.observed and c.observed.denominator_type in _ACTION_GRADE_DENOMINATORS
        for c in cases
    )
    cap = "strong" if has_action else "weak"
    return min(by_count, cap, key=_LEVELS.index)


def _category_warning(category: str, n: int) -> str | None:
    if n < 4:
        return (
            f"only {n} case(s) for category '{category}' — diagnostic only, "
            "not a generalizable prior; not validated (no holdout cases)"
        )
    return "not validated (no holdout cases)"


def estimate_category_profiles(
    cases: Sequence[ValidationCase],
) -> dict[str, CategoryPriorProfile]:
    """Estimate one CategoryPriorProfile per product_category. Diagnostic only."""
    by_cat: dict[str, list[ValidationCase]] = {}
    for c in _scored_cases(cases):
        by_cat.setdefault(c.metadata.product_category, []).append(c)

    out: dict[str, CategoryPriorProfile] = {}
    for cat, group in sorted(by_cat.items()):
        obs_sum = {b: 0.0 for b in _BUCKETS}
        pred_sum = {b: 0.0 for b in _BUCKETS}
        maes: list[float] = []
        n = 0
        for c in group:
            if c.predicted is None or c.observed is None:
                continue
            n += 1
            pb = c.predicted.to_buckets()
            ob = c.observed.to_buckets()
            for b in _BUCKETS:
                pred_sum[b] += pb[b]
                obs_sum[b] += ob[b]
            m = compute_case_metrics(c)
            if m and m.mae_pp is not None:
                maes.append(m.mae_pp)

        observed_avg = {b: round(obs_sum[b] / n, 4) for b in _BUCKETS} if n else None
        predicted_avg = {b: round(pred_sum[b] / n, 4) for b in _BUCKETS} if n else None
        signed = (
            {b: round(predicted_avg[b] - observed_avg[b], 4) for b in _BUCKETS}
            if observed_avg and predicted_avg else None
        )
        out[cat] = CategoryPriorProfile(
            product_category=cat,
            case_count=len(group),
            observed_avg=observed_avg,
            predicted_avg=predicted_avg,
            avg_signed_bucket_error=signed,
            avg_mae_pp=round(sum(maes) / len(maes), 4) if maes else None,
            confidence_level=_confidence_level(group),
            warning=_category_warning(cat, len(group)),
        )
    return out


def summarize_category_prior(
    cases: Sequence[ValidationCase],
) -> dict[str, dict[str, object]]:
    return {
        cat: category_profile_to_dict(p)
        for cat, p in estimate_category_profiles(cases).items()
    }


def category_profile_to_dict(profile: CategoryPriorProfile) -> dict[str, object]:
    return profile.model_dump()
