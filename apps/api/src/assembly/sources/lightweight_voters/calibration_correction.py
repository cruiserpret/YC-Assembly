"""Phase 12C — conservative calibration correction layer.

This is the LEAST-tested layer in 12C. The user spec is explicit:
calibrated_distribution is EXPERIMENTAL/INTERNAL, not proven truth.

Default behavior:
  - 50/50 blend between 24-rich and 100-voter distributions
  - Wide ±15pp confidence band
  - "calibration_support_weak" warning fires when we have <3 prior
    scored blind cases for the brief's category
  - Prior-error correction stays DISABLED until enough data exists
"""
from __future__ import annotations

from typing import Literal

from assembly.sources.lightweight_voters.voter_schema import (
    CalibratedDistribution,
    VoterBucketDistribution,
)


BUCKETS: tuple[Literal["buyer", "receptive", "uncertain", "skeptical"], ...] = (
    "buyer", "receptive", "uncertain", "skeptical",
)


def calibrated_distribution(
    raw_24_distribution_percent: dict[str, float],
    voter_100_distribution: VoterBucketDistribution,
    *,
    category: str | None = None,
    evidence_quality: float = 1.0,
    prior_errors_by_category: dict[str, list[dict[str, float]]] | None = None,
    min_prior_cases_for_correction: int = 3,
) -> CalibratedDistribution:
    """Blend the rich-24 and voter-100 distributions conservatively.

    Args:
      raw_24_distribution_percent: {bucket: %} from the 24-rich pipeline
      voter_100_distribution: VoterBucketDistribution from the 100-voter overlay
      category: brief.category_hint or similar; used to look up prior errors
      evidence_quality: 0-1, from evidence_quality.json (lower → lean rich)
      prior_errors_by_category: optional historical [{bucket: signed_err_pp}]
        per category. Only used when ≥`min_prior_cases_for_correction` entries
      min_prior_cases_for_correction: refuse to correct with < this many cases
    """
    warnings: list[str] = []

    # Blend weights default 50/50; shift toward rich when evidence quality
    # is low (voter pool is built on weaker centroids).
    blend_w = {"rich_24": 0.5, "voter_100": 0.5}
    if evidence_quality < 0.6:
        blend_w = {"rich_24": 0.7, "voter_100": 0.3}
        warnings.append(
            f"low_evidence_quality:{evidence_quality:.2f}"
            f"_leaning_rich"
        )

    voter_dict = {
        "buyer": voter_100_distribution.buyer,
        "receptive": voter_100_distribution.receptive,
        "uncertain": voter_100_distribution.uncertain,
        "skeptical": voter_100_distribution.skeptical,
    }
    blended: dict[str, float] = {}
    for b in BUCKETS:
        blended[b] = (
            blend_w["rich_24"] * float(raw_24_distribution_percent.get(b, 0))
            + blend_w["voter_100"] * voter_dict[b]
        )

    # Prior-error correction is DISABLED until we have enough cases.
    used_prior = False
    if category and prior_errors_by_category:
        cases = prior_errors_by_category.get(category, [])
        if len(cases) >= min_prior_cases_for_correction:
            for b in BUCKETS:
                avg_err = sum(c.get(b, 0) for c in cases) / len(cases)
                # Cautious 50% correction; never invert.
                blended[b] = max(0.0, blended[b] - 0.5 * avg_err)
            used_prior = True
        else:
            warnings.append(
                f"calibration_support_weak:"
                f"{len(cases)}_prior_cases_for_{category}"
            )
    else:
        warnings.append(
            "calibration_support_weak:no_prior_cases_for_category"
        )

    # Re-normalize: sum may drift slightly off 100.
    total = sum(blended.values())
    if total > 0:
        blended = {b: 100.0 * v / total for b, v in blended.items()}

    return CalibratedDistribution(
        distribution_percent=blended,
        confidence_band_pp=15.0,   # wide default; tightened later
        used_prior_correction=used_prior,
        blend_weights=blend_w,
        calibration_warnings=warnings,
    )
