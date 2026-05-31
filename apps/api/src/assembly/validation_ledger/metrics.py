"""Phase 15B — pure, deterministic metric functions for the validation ledger.

These compare a *predicted* market-proportion distribution against an
*observed* one across the four canonical buckets:

    buyer_action_positive · receptive · uncertain_proof_needed · skeptical_resistant

All functions are pure: no LLM, no network, no DB, no randomness, no global
state. They operate on plain ``dict[str, float]`` (percentage points unless a
function says otherwise) so they stay decoupled from the pydantic schema.

IMPORTANT (anti-overfit): these are *measurement* utilities only. They never
feed observed data back into any model — they merely score predictions that
were locked before the outcome was known.
"""
from __future__ import annotations

from collections.abc import Mapping

# Canonical bucket order. Every distribution dict must carry exactly these keys.
BUCKET_KEYS: tuple[str, ...] = (
    "buyer_action_positive",
    "receptive",
    "uncertain_proof_needed",
    "skeptical_resistant",
)


def _vals(dist: Mapping[str, float]) -> list[float]:
    """Extract the four bucket values in canonical order.

    Raises KeyError if any bucket is missing (callers rely on this to reject
    malformed distributions).
    """
    try:
        return [float(dist[k]) for k in BUCKET_KEYS]
    except KeyError as exc:  # pragma: no cover - message clarity
        raise KeyError(
            f"distribution missing bucket {exc!s}; required keys: {BUCKET_KEYS}"
        ) from exc


def normalize_distribution(
    dist: Mapping[str, float], *, scale: float = 100.0
) -> dict[str, float]:
    """Rescale a distribution so its four buckets sum to ``scale`` (default 100).

    Raises ValueError if the distribution sums to <= 0 (cannot be normalized).
    """
    vals = _vals(dist)
    total = sum(vals)
    if total <= 0:
        raise ValueError("cannot normalize a distribution with a non-positive sum")
    return {k: (float(dist[k]) / total) * scale for k in BUCKET_KEYS}


def validate_distribution_sums(
    dist: Mapping[str, float], *, expected: float = 100.0, tol: float = 1.0
) -> bool:
    """True iff the four buckets sum to ``expected`` within ``tol``."""
    return abs(sum(_vals(dist)) - expected) <= tol


def bucket_errors(
    pred: Mapping[str, float], obs: Mapping[str, float]
) -> dict[str, float]:
    """Signed per-bucket error, predicted minus observed.

    Positive = the model OVER-predicted that bucket; negative = UNDER-predicted.
    """
    return {k: float(pred[k]) - float(obs[k]) for k in BUCKET_KEYS}


def mae_pp(pred: Mapping[str, float], obs: Mapping[str, float]) -> float:
    """Mean absolute error in percentage points across the four buckets."""
    errs = bucket_errors(pred, obs)
    return sum(abs(e) for e in errs.values()) / len(BUCKET_KEYS)


def max_bucket_error_pp(
    pred: Mapping[str, float], obs: Mapping[str, float]
) -> float:
    """Largest single-bucket absolute error in percentage points."""
    return max(abs(e) for e in bucket_errors(pred, obs).values())


def total_variation_distance(
    pred: Mapping[str, float], obs: Mapping[str, float]
) -> float:
    """Total variation distance in [0, 1].

    Both distributions are normalized to fractions first, so TVD is scale-
    invariant: 0.5 * sum |p_i - q_i|.
    """
    p = normalize_distribution(pred, scale=1.0)
    q = normalize_distribution(obs, scale=1.0)
    return 0.5 * sum(abs(p[k] - q[k]) for k in BUCKET_KEYS)


def direction_match(
    pred: Mapping[str, float], obs: Mapping[str, float]
) -> bool:
    """True iff predicted and observed agree on the DOMINANT (argmax) bucket.

    A coarse but robust check: did the forecast at least get the single
    largest market reaction right? Ties break by canonical bucket order
    (deterministic).
    """
    pk = max(BUCKET_KEYS, key=lambda k: float(pred[k]))
    ok = max(BUCKET_KEYS, key=lambda k: float(obs[k]))
    return pk == ok


def buyer_false_confidence(
    pred: Mapping[str, float],
    obs: Mapping[str, float],
    *,
    threshold_pp: float = 10.0,
) -> bool:
    """True iff the forecast OVER-states the buyer/action-positive bucket by at
    least ``threshold_pp`` points — the most damaging error for a founder
    (false confidence that people will buy). Pre-outcome models that hallucinate
    buyers are penalized here.
    """
    return (
        float(pred["buyer_action_positive"]) - float(obs["buyer_action_positive"])
    ) >= threshold_pp


def compute_all(
    pred: Mapping[str, float], obs: Mapping[str, float]
) -> dict[str, object]:
    """Compute every metric for a (predicted, observed) pair. Deterministic."""
    return {
        "mae_pp": round(mae_pp(pred, obs), 4),
        "tvd": round(total_variation_distance(pred, obs), 4),
        "max_bucket_error_pp": round(max_bucket_error_pp(pred, obs), 4),
        "bucket_errors": {k: round(v, 4) for k, v in bucket_errors(pred, obs).items()},
        "direction_match": direction_match(pred, obs),
        "buyer_false_confidence": buyer_false_confidence(pred, obs),
    }
