"""Phase 15D0 — calibration DIAGNOSTICS report (measurement only).

Loads the validation ledger and returns a structured diagnostic report:
dataset summary, train/holdout counts, action-tier coverage, per-source and
per-category error profiles, explicit warnings, and the data needed before any
calibration could be applied. It changes NO forecast and applies NO correction.

> Phase 15D0 does not change forecasts. It measures repeated error patterns only.

No LLM, no network, no DB, no randomness.
"""
from __future__ import annotations

from collections.abc import Sequence

from assembly.market_calibration.action_signals import classify_action_signal
from assembly.market_calibration.category_priors import summarize_category_prior
from assembly.market_calibration.source_profiles import summarize_source_bias
from assembly.validation_ledger import load_cases
from assembly.validation_ledger.schema import ValidationCase

# Observed denominators that are comment/opinion-derived (Tier-3-grade ground
# truth) rather than revealed action.
_COMMENT_DENOMINATORS = frozenset({"comments", "independent_voices"})


def _tier_case_counts(cases: Sequence[ValidationCase]) -> dict[str, int]:
    """How many cases carry at least one action signal at each tier.

    For the current seed (no action_signals attached) these are all 0 — which is
    itself a diagnostic: the ledger has no revealed-action evidence yet.
    """
    counts = {"tier1_case_count": 0, "tier2_case_count": 0, "tier3_case_count": 0}
    for c in cases:
        tiers = {classify_action_signal(s) for s in c.action_signals}
        if 1 in tiers:
            counts["tier1_case_count"] += 1
        if 2 in tiers:
            counts["tier2_case_count"] += 1
        if 3 in tiers:
            counts["tier3_case_count"] += 1
    return counts


def build_calibration_diagnostics_report(
    cases: Sequence[ValidationCase] | None = None,
) -> dict[str, object]:
    """Build the Phase 15D0 diagnostic report. Diagnostic only — no forecast."""
    if cases is None:
        cases = load_cases()

    scored = [c for c in cases if c.metadata.validation_status == "scored"]
    training = [c for c in cases if c.anti_overfit.used_for_training]
    holdout = [c for c in cases if c.anti_overfit.used_for_holdout]
    comment_derived = [
        c for c in scored
        if c.observed and c.observed.denominator_type in _COMMENT_DENOMINATORS
    ]
    sources = sorted({c.metadata.source_type for c in cases})
    categories = sorted({c.metadata.product_category for c in cases})

    dataset_summary = {
        "n_cases": len(cases),
        "n_scored": len(scored),
        "sources": sources,
        "categories": categories,
        "comment_derived_observed_count": len(comment_derived),
    }

    warnings: list[str] = []
    if len(holdout) == 0:
        warnings.append("0 holdout cases — cannot validate calibration")
    if len(cases) < 20:
        warnings.append(
            f"Only {len(cases)} cases — source profiles are diagnostic only"
        )
    if scored and len(comment_derived) > len(scored) / 2:
        warnings.append("Most observed outcomes are Tier-3/comment-derived")
    warnings.append("Do not apply these profiles to live forecasts yet")

    recommended_next_data_needs = [
        "Add 20+ BLIND validation cases, defaulting new ones to "
        "used_for_holdout=true, so calibration can be validated on unseen data",
        "Capture real Tier-1 action outcomes per case (purchases, backers, "
        "paid signups, trial conversions, installs/downloads, retention/churn)",
        "Diversify across sources (HN, Product Hunt, Kickstarter, GitHub, "
        "Reddit, App Store, B2B) and product categories",
        "Record per-case action_signals so action-tier coverage is non-zero",
    ]

    return {
        "phase": "15D0_source_bias_diagnostics",
        "is_diagnostic_only": True,
        "applies_calibration": False,
        "changes_live_forecast": False,
        "validated": len(holdout) > 0,
        "dataset_summary": dataset_summary,
        "training_case_count": len(training),
        "holdout_case_count": len(holdout),
        **_tier_case_counts(cases),
        "source_profiles": summarize_source_bias(cases),
        "category_profiles": summarize_category_prior(cases),
        "warnings": warnings,
        "recommended_next_data_needs": recommended_next_data_needs,
    }
