"""Calibration metrics over the 4-bucket market vocabulary.

Every metric here:
  * accepts either fractions (sum=1.0) or percents (sum=100.0)
  * REQUIRES both inputs to be in the SAME mode — mixing fraction +
    percent raises a clear error rather than silently producing
    garbage
  * works after both inputs are passed through
    ``normalize_distribution`` so partial / extra keys are handled
    consistently

All bucket-level errors are reported in the SAME units as the input
(so percent in → percent-point error out). MAE / max / TVD are
likewise reported in the input's units.

False-confidence detection is rule-based, not statistical: the
calibration corpus is too small in Phase 12A.1 to fit confidence
intervals. The rule is simply that Assembly over-predicts a bucket
by an absolute amount above a configurable threshold. The default
threshold (15 percentage points) is calibrated against the working
intuition that >15pp gap on any market-level bucket would be a
material commercial mistake.
"""
from __future__ import annotations

from typing import Literal

from assembly.calibration.market_buckets import (
    BUCKET_NAMES,
    MarketBucket,
    normalize_distribution,
    validate_bucket_distribution,
)


def _coerce(
    d: dict[str, float],
    *,
    mode: Literal["percent", "fraction"],
) -> dict[MarketBucket, float]:
    """Normalize and return a dict over the closed bucket set."""
    return normalize_distribution(d, out_mode=mode)


def _mode_label(mode: Literal["percent", "fraction"]) -> str:
    return "pp" if mode == "percent" else "fraction"


def bucket_absolute_errors(
    predicted: dict[str, float],
    observed: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "percent",
) -> dict[MarketBucket, float]:
    """Per-bucket absolute error |predicted − observed|.

    In ``mode='percent'`` (default), errors are in percentage points.
    """
    p = _coerce(predicted, mode=mode)
    o = _coerce(observed, mode=mode)
    return {b: abs(p[b] - o[b]) for b in BUCKET_NAMES}


def mean_absolute_bucket_error(
    predicted: dict[str, float],
    observed: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "percent",
) -> float:
    """MAE across the 4 buckets. Same units as input mode."""
    errs = bucket_absolute_errors(predicted, observed, mode=mode)
    return sum(errs.values()) / len(BUCKET_NAMES)


def max_bucket_error(
    predicted: dict[str, float],
    observed: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "percent",
) -> float:
    """Largest absolute per-bucket error. Same units as input mode."""
    errs = bucket_absolute_errors(predicted, observed, mode=mode)
    return max(errs.values())


def total_variation_distance(
    predicted: dict[str, float],
    observed: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "fraction",
) -> float:
    """Total variation distance: ``0.5 * sum |p_i − q_i|``.

    Always returned in ``fraction`` units (0..1), regardless of input
    mode, because TVD is a probability metric and its meaning is
    well-defined only on the [0, 1] interval. If callers pass
    percents, we convert internally.
    """
    p = _coerce(predicted, mode="fraction")
    o = _coerce(observed, mode="fraction")
    return 0.5 * sum(abs(p[b] - o[b]) for b in BUCKET_NAMES)


def false_confidence_warning(
    predicted: dict[str, float],
    observed: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "percent",
    overprediction_threshold_pp: float = 15.0,
    underprediction_threshold_pp: float = 15.0,
) -> list[str]:
    """Rule-based detection of Assembly over- or under-confidence.

    Returns a list of human-readable warnings (empty if Assembly's
    prediction stayed within ``±threshold_pp`` on every bucket).

    Buyer over-prediction and skeptical under-prediction get extra
    weight — they are the two failure modes most likely to translate
    into a bad commercial decision.
    """
    # Work in percent-point space regardless of input mode so the
    # threshold is interpretable as "percentage points off."
    p = _coerce(predicted, mode="percent")
    o = _coerce(observed, mode="percent")
    warnings: list[str] = []
    for b in BUCKET_NAMES:
        diff = p[b] - o[b]
        if diff > overprediction_threshold_pp:
            tag = "over_predicted"
            if b == "buyer":
                tag = "over_predicted_buyer_critical"
            warnings.append(
                f"{tag}: predicted {p[b]:.1f}% but observed {o[b]:.1f}% "
                f"(+{diff:.1f}pp) for bucket={b!r}"
            )
        elif diff < -underprediction_threshold_pp:
            tag = "under_predicted"
            if b == "skeptical":
                tag = "under_predicted_skepticism_critical"
            warnings.append(
                f"{tag}: predicted {p[b]:.1f}% but observed {o[b]:.1f}% "
                f"({diff:.1f}pp) for bucket={b!r}"
            )
    return warnings


def calibration_summary(
    predicted: dict[str, float],
    observed: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "percent",
    objections_predicted: list[str] | None = None,
    objections_observed: list[str] | None = None,
) -> dict:
    """One-call rollup. Returns:

      - predicted_distribution (normalized to ``mode``)
      - observed_distribution  (normalized to ``mode``)
      - bucket_errors_pp       (or fractional if mode='fraction')
      - mean_absolute_bucket_error
      - max_bucket_error
      - total_variation_distance (always fraction)
      - false_confidence_warnings
      - validity (both inputs validate)
      - objection_recall (if both lists provided)
      - units (so downstream readers know what 'mae' means)

    ``objections_predicted`` / ``objections_observed`` accept any
    lower-cased free-text strings; recall is computed as
    ``|predicted ∩ observed| / |observed|``. Case-insensitive,
    whitespace-stripped. Skipped if either list is None.
    """
    p_norm = _coerce(predicted, mode=mode)
    o_norm = _coerce(observed, mode=mode)
    p_ok, p_err = validate_bucket_distribution(p_norm, mode=mode)
    o_ok, o_err = validate_bucket_distribution(o_norm, mode=mode)
    bucket_errs = bucket_absolute_errors(p_norm, o_norm, mode=mode)
    out: dict = {
        "predicted_distribution": p_norm,
        "observed_distribution": o_norm,
        "bucket_errors": bucket_errs,
        "mean_absolute_bucket_error": (
            mean_absolute_bucket_error(p_norm, o_norm, mode=mode)
        ),
        "max_bucket_error": max_bucket_error(p_norm, o_norm, mode=mode),
        "total_variation_distance": (
            total_variation_distance(p_norm, o_norm, mode="fraction")
        ),
        "false_confidence_warnings": (
            false_confidence_warning(p_norm, o_norm, mode=mode)
        ),
        "validity": {
            "predicted_ok": p_ok, "predicted_errors": p_err,
            "observed_ok": o_ok, "observed_errors": o_err,
        },
        "units": {
            "bucket_errors": _mode_label(mode),
            "mae": _mode_label(mode),
            "max_error": _mode_label(mode),
            "tvd": "fraction",
        },
    }
    if objections_predicted is not None and objections_observed is not None:
        out["objection_recall"] = _objection_recall(
            objections_predicted, objections_observed,
        )
    return out


def _objection_recall(
    predicted: list[str], observed: list[str],
) -> dict:
    """Compute |predicted ∩ observed| / |observed|. Case- and
    whitespace-insensitive."""
    norm_obs = {(o or "").strip().lower() for o in observed if o}
    norm_pred = {(p or "").strip().lower() for p in predicted if p}
    if not norm_obs:
        return {
            "predicted_count": len(norm_pred),
            "observed_count": 0,
            "recall": None,
            "matched": [],
            "missed": [],
        }
    matched = sorted(norm_obs & norm_pred)
    missed = sorted(norm_obs - norm_pred)
    return {
        "predicted_count": len(norm_pred),
        "observed_count": len(norm_obs),
        "recall": len(matched) / len(norm_obs),
        "matched": matched,
        "missed": missed,
    }
