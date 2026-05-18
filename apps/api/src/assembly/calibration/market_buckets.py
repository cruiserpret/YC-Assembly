"""Controlled vocabulary for market-level calibration buckets.

Calibration compares two distributions on the same 4-bucket vocabulary:

  buyer       — real willingness to buy, pay, adopt, install, or use now
  receptive   — interested but not yet committed
  uncertain   — ambiguous, neutral, or needs more information
  skeptical   — rejecting, loyal to current alternative, or strongly doubtful

The mapping from Assembly's internal intent labels to these buckets
is INTENTIONALLY CONSERVATIVE:

  - Waitlist signups count as `receptive`, NOT `buyer`, unless payment
    intent is explicit. This prevents Assembly from inflating
    buyer-side proportions by treating a low-friction signal as a
    purchase commitment.
  - `loyal_to_current_alternative` counts as `skeptical`, NOT
    `uncertain`. A persona who explicitly anchors on their current
    tool is signaling rejection of the alternative, regardless of
    whether their reasoning is articulate.
  - `would_consider_if_proven` (and its `_high_trust` / `_unsure`
    variants) all map to `receptive`. These are positive-with-proof
    asks — not buyer commitments.
  - Anything unknown maps to `uncertain` and emits a warning so the
    extractor surface keeps growing without silent drift.

The bucket set is closed. Do not extend without revisiting the
calibration design: extra buckets break the metric invariants
(probabilities sum to 1) and silently change MAE / TVD comparisons.
"""
from __future__ import annotations

import logging
from typing import Literal, get_args

logger = logging.getLogger(__name__)

MarketBucket = Literal["buyer", "receptive", "uncertain", "skeptical"]
BUCKET_NAMES: tuple[MarketBucket, ...] = get_args(MarketBucket)


# ---------------------------------------------------------------------------
# Mapping table — Assembly intent label → market bucket
# ---------------------------------------------------------------------------
#
# Conservative mapping (see module docstring). Keys are lowercased,
# spaces normalized to underscores at lookup time.

ASSEMBLY_LABEL_TO_BUCKET: dict[str, MarketBucket] = {
    # ------- buyer: explicit purchase / adopt / install intent -------
    "would_buy_now": "buyer",
    "would_try_once": "buyer",           # committed trial when product exists
    "strong_purchase_intent": "buyer",
    "committed_trial": "buyer",

    # ------- receptive: positive but not committed -----------------
    "would_join_waitlist": "receptive",
    "would_consider_if_proven": "receptive",
    "would_consider_if_proven_high_trust": "receptive",
    "would_consider_if_proven_unsure": "receptive",
    "would_share_with_friend": "receptive",
    "positive_but_needs_proof": "receptive",
    "asks_serious_questions": "receptive",

    # ------- uncertain: ambiguous, neutral, or needs more info ----
    "unsure": "uncertain",
    "wait_and_see": "uncertain",
    "insufficient_information": "uncertain",
    "mixed": "uncertain",
    "neutral": "uncertain",
    "would_compare_to_current_brand": "uncertain",
    "would_block": "uncertain",           # discussion-flow only — not a market intent

    # ------- skeptical: reject / loyal / switching-cost-too-high --
    "would_reject": "skeptical",
    "loyal_to_current_alternative": "skeptical",
    "skeptical": "skeptical",
    "not_for_me": "skeptical",
    "switching_cost_too_high": "skeptical",
    "trust_not_cleared": "skeptical",
    "refuses_switching": "skeptical",
}


def _normalize_label(label: str) -> str:
    """Lowercase + replace spaces/hyphens with underscores for lookup."""
    return (
        (label or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def map_assembly_intent_to_market_bucket(
    label: str,
    *,
    payment_intent_explicit: bool = False,
) -> tuple[MarketBucket, str | None]:
    """Map a single Assembly intent label to a market bucket.

    Returns ``(bucket, warning_or_none)``. If the label is unknown,
    classifies as ``"uncertain"`` and returns a warning string so the
    caller can surface drift without silently mis-bucketing.

    ``payment_intent_explicit`` is a narrow override: when ``True``,
    a label that would normally map to ``receptive`` because it is
    only a soft commitment (e.g. ``"would_join_waitlist"``) is
    upgraded to ``"buyer"``. The default is ``False`` so the
    conservative mapping wins and Assembly cannot quietly inflate
    buyer proportions.
    """
    if label is None:
        return "uncertain", "label_was_none — defaulting to uncertain"
    key = _normalize_label(label)
    if not key:
        return "uncertain", "label_was_empty — defaulting to uncertain"
    bucket = ASSEMBLY_LABEL_TO_BUCKET.get(key)
    if bucket is None:
        warning = (
            f"unknown_intent_label={label!r} — defaulting to "
            "uncertain. Add an explicit mapping in market_buckets.py "
            "if this label is intended."
        )
        logger.warning(
            "calibration.unknown_intent_label label=%r", label,
        )
        return "uncertain", warning
    if payment_intent_explicit and bucket == "receptive":
        return "buyer", None
    return bucket, None


# ---------------------------------------------------------------------------
# Distribution normalization
# ---------------------------------------------------------------------------


def normalize_distribution(
    counts_or_percents: dict[str, float],
    *,
    out_mode: Literal["percent", "fraction"] = "fraction",
) -> dict[MarketBucket, float]:
    """Normalize a bucket map to a proper distribution.

    Accepts EITHER:
      - raw counts: {"buyer": 8, "receptive": 16, "uncertain": 0, "skeptical": 6}
      - percentages: {"buyer": 0.10, "receptive": 0.40, ...}
      - 0-100 percents: {"buyer": 10, "receptive": 40, ...}

    Always returns a 4-key dict over ``BUCKET_NAMES``. Missing keys are
    filled with ``0.0``. Out-of-vocabulary keys are dropped.

    ``out_mode="fraction"`` returns values summing to 1.0 (default —
    plays cleanly with TVD math).
    ``out_mode="percent"`` returns values summing to 100.0 (useful for
    human-readable summaries).

    Empty input (or all zeros) returns 0.25 on each bucket as a
    deliberately-flat prior, so the caller never sees NaN; a single
    distribution-validity warning is logged.
    """
    if not counts_or_percents:
        logger.warning("calibration.normalize_distribution empty input — returning flat prior")
        return _flat_prior(out_mode)
    cleaned: dict[MarketBucket, float] = {b: 0.0 for b in BUCKET_NAMES}
    for k, v in counts_or_percents.items():
        if k in BUCKET_NAMES:
            cleaned[k] += float(v or 0)
    total = sum(cleaned.values())
    if total <= 0:
        logger.warning("calibration.normalize_distribution non-positive total — returning flat prior")
        return _flat_prior(out_mode)
    scale = (100.0 if out_mode == "percent" else 1.0) / total
    return {b: cleaned[b] * scale for b in BUCKET_NAMES}


def _flat_prior(out_mode: Literal["percent", "fraction"]) -> dict[MarketBucket, float]:
    v = 25.0 if out_mode == "percent" else 0.25
    return {b: v for b in BUCKET_NAMES}


def validate_bucket_distribution(
    d: dict[str, float],
    *,
    mode: Literal["percent", "fraction"] = "fraction",
    tol: float = 1e-6,
) -> tuple[bool, list[str]]:
    """Return ``(ok, errors)``. Errors include:
       - missing buckets
       - extra (out-of-vocab) keys
       - negative values
       - sum not within tolerance of 1.0 (fraction) or 100.0 (percent)
    """
    errors: list[str] = []
    expected = set(BUCKET_NAMES)
    got = set(d.keys())
    for missing in expected - got:
        errors.append(f"missing_bucket={missing!r}")
    for extra in got - expected:
        errors.append(f"extra_bucket={extra!r}")
    for k, v in d.items():
        if k in expected and v is not None and float(v) < 0:
            errors.append(f"negative_value_for={k!r}: {v}")
    target = 100.0 if mode == "percent" else 1.0
    s = sum(float(v or 0) for k, v in d.items() if k in expected)
    if abs(s - target) > tol:
        errors.append(
            f"sum={s:.6f} expected={target:.6f} (mode={mode!r})"
        )
    return (len(errors) == 0, errors)
