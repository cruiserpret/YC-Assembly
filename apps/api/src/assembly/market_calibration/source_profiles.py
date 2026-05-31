"""Phase 15D0 — source-bias DIAGNOSTICS (measurement only, no live correction).

For each ``source_type`` in the validation ledger, estimate how Assembly's
locked predictions deviated from observed outcomes — the repeated, per-source
error pattern (e.g. Hacker News over-predicting receptive). This is a
*diagnostic* layer: it reads observed outcomes to MEASURE error and produces
**no forecast and no live correction**. Nothing here is applied to Assembly's
output.

Anti-overfit discipline (enforced here + by tests):
  - only ``validation_status == "scored"`` cases are used,
  - fitting uses ``used_for_training`` cases ONLY — holdout cases are never
    used to fit,
  - with 0 holdout cases, every profile is explicitly ``validated=False``
    ("not validated"),
  - small N is surfaced as a warning; no generalizable claim is made.

No LLM, no network, no DB, no randomness. Reads the ledger (observed outcomes)
purely to score predictions that were locked before the outcome was known.
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from assembly.validation_ledger import compute_case_metrics
from assembly.validation_ledger import metrics as ledger_metrics
from assembly.validation_ledger.schema import ValidationCase

_BUCKETS = ledger_metrics.BUCKET_KEYS
_SIGNED_ERROR_THRESHOLD_PP = 2.0  # |signed avg error| above this is "biased"
# Observed denominators that are real revealed-action (Tier-1) ground truth.
_ACTION_GRADE_DENOMINATORS = frozenset({"backers"})
_LEVELS = ("insufficient", "weak", "moderate", "strong")


class SourceBucketBias(BaseModel):
    """Average SIGNED bucket error (predicted minus observed) for a source.

    Positive = the model OVER-predicts this bucket for this source; negative =
    UNDER-predicts.
    """

    model_config = ConfigDict(extra="forbid")

    buyer_action_positive: float
    receptive: float
    uncertain_proof_needed: float
    skeptical_resistant: float


class SourceBiasProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    case_count: int
    avg_mae_pp: float | None = None
    avg_tvd: float | None = None
    avg_max_bucket_error_pp: float | None = None
    avg_signed_bucket_error: SourceBucketBias | None = None
    overpredicted_buckets: list[str] = []
    underpredicted_buckets: list[str] = []
    confidence_level: str = "insufficient"
    validated: bool = False  # True only if scored against HOLDOUT (none yet)
    warning: str | None = None


def _scorable_training_cases(
    cases: Sequence[ValidationCase], *, use_training_only: bool = True
) -> list[ValidationCase]:
    """Scored + scorable cases used for FITTING. Holdout cases are excluded so
    they can never leak into a fitted profile."""
    out: list[ValidationCase] = []
    for c in cases:
        if c.metadata.validation_status != "scored" or not c.is_scorable():
            continue
        if c.anti_overfit.used_for_holdout:
            continue  # never fit on holdout
        if use_training_only and not c.anti_overfit.used_for_training:
            continue
        out.append(c)
    return out


def _count_level(n: int) -> str:
    if n < 2:
        return "insufficient"
    if n < 4:
        return "weak"
    if n < 8:
        return "moderate"
    return "strong"


def _evidence_cap(cases: Sequence[ValidationCase]) -> str:
    """Confidence is capped at 'weak' unless the observed ground truth includes
    real revealed-action (Tier-1) data — comment/voice-derived observed cannot
    support a 'moderate'/'strong' claim."""
    for c in cases:
        if c.observed and c.observed.denominator_type in _ACTION_GRADE_DENOMINATORS:
            return "strong"
    return "weak"


def _confidence_level(cases: Sequence[ValidationCase]) -> str:
    by_count = _count_level(len(cases))
    cap = _evidence_cap(cases)
    return min(by_count, cap, key=_LEVELS.index)


def minimum_case_warning(profile: SourceBiasProfile) -> str | None:
    """A human-readable caution for a thin/unvalidated profile."""
    parts: list[str] = []
    if profile.case_count < 4:
        parts.append(
            f"only {profile.case_count} case(s) for source "
            f"'{profile.source_type}' — diagnostic only, not generalizable"
        )
    if not profile.validated:
        parts.append("not validated (no holdout cases)")
    return "; ".join(parts) if parts else None


def estimate_source_profiles(
    cases: Sequence[ValidationCase], *, use_training_only: bool = True
) -> dict[str, SourceBiasProfile]:
    """Estimate one SourceBiasProfile per source_type. Diagnostic only."""
    fit_cases = _scorable_training_cases(cases, use_training_only=use_training_only)
    holdout_exists = any(c.anti_overfit.used_for_holdout for c in cases)

    by_source: dict[str, list[ValidationCase]] = {}
    for c in fit_cases:
        by_source.setdefault(c.metadata.source_type, []).append(c)

    profiles: dict[str, SourceBiasProfile] = {}
    for source, group in sorted(by_source.items()):
        maes: list[float] = []
        tvds: list[float] = []
        maxes: list[float] = []
        signed: dict[str, list[float]] = {b: [] for b in _BUCKETS}
        for c in group:
            m = compute_case_metrics(c)
            if m is None or c.predicted is None or c.observed is None:
                continue
            if m.mae_pp is not None:
                maes.append(m.mae_pp)
            if m.tvd is not None:
                tvds.append(m.tvd)
            if m.max_bucket_error_pp is not None:
                maxes.append(m.max_bucket_error_pp)
            errs = ledger_metrics.bucket_errors(
                c.predicted.to_buckets(), c.observed.to_buckets()
            )
            for b in _BUCKETS:
                signed[b].append(errs[b])

        avg_signed = {
            b: round(sum(v) / len(v), 4) for b, v in signed.items() if v
        }
        over = [b for b in _BUCKETS if avg_signed.get(b, 0.0) > _SIGNED_ERROR_THRESHOLD_PP]
        under = [b for b in _BUCKETS if avg_signed.get(b, 0.0) < -_SIGNED_ERROR_THRESHOLD_PP]

        profile = SourceBiasProfile(
            source_type=source,
            case_count=len(group),
            avg_mae_pp=round(sum(maes) / len(maes), 4) if maes else None,
            avg_tvd=round(sum(tvds) / len(tvds), 4) if tvds else None,
            avg_max_bucket_error_pp=round(sum(maxes) / len(maxes), 4) if maxes else None,
            avg_signed_bucket_error=(
                SourceBucketBias(**avg_signed) if len(avg_signed) == len(_BUCKETS) else None
            ),
            overpredicted_buckets=over,
            underpredicted_buckets=under,
            confidence_level=_confidence_level(group),
            # No source profile is validated until it is scored on a holdout
            # split. With 0 holdout cases, this is always False.
            validated=False if not holdout_exists else False,
        )
        profile.warning = minimum_case_warning(profile)
        profiles[source] = profile
    return profiles


def summarize_source_bias(
    cases: Sequence[ValidationCase], *, use_training_only: bool = True
) -> dict[str, dict[str, object]]:
    """Flat per-source summary (source -> key stats). Diagnostic only."""
    profiles = estimate_source_profiles(cases, use_training_only=use_training_only)
    return {src: source_profile_to_dict(p) for src, p in profiles.items()}


def source_profile_to_dict(profile: SourceBiasProfile) -> dict[str, object]:
    return profile.model_dump()
