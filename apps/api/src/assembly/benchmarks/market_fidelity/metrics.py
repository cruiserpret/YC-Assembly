"""Phase 17B — pure scoring metric functions (scaffolding).

Deterministic, side-effect-free metric helpers for the benchmark. NO outcomes are
embedded here — callers pass observed values (only test fixtures do so in 17B; real
scoring happens in a later phase). Strictly-proper rules (Brier) are provided for
the headline ranking; MAE/TVD/RMSE are descriptive distances. Pure stdlib + math.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from assembly.benchmarks.market_fidelity.schema import BUCKET_KEYS


def _as_vec(d: dict[str, float]) -> list[float]:
    return [float(d[k]) for k in BUCKET_KEYS]


def _to_fractions(d: dict[str, float]) -> list[float]:
    v = _as_vec(d)
    total = sum(v) or 1.0
    return [x / total for x in v]


def bucket_mae(pred: dict[str, float], obs: dict[str, float]) -> float:
    """Mean absolute error across the four buckets, in percentage points."""
    p, o = _as_vec(pred), _as_vec(obs)
    return sum(abs(a - b) for a, b in zip(p, o, strict=True)) / len(BUCKET_KEYS)


def rmse(pred: dict[str, float], obs: dict[str, float]) -> float:
    """Root-mean-squared error across buckets, in percentage points."""
    p, o = _as_vec(pred), _as_vec(obs)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p, o, strict=True)) / len(BUCKET_KEYS))


def tvd(pred: dict[str, float], obs: dict[str, float]) -> float:
    """Total Variation Distance on the normalized distributions, in [0, 1]."""
    p, o = _to_fractions(pred), _to_fractions(obs)
    return 0.5 * sum(abs(a - b) for a, b in zip(p, o, strict=True))


def brier_multiclass(pred: dict[str, float], obs: dict[str, float]) -> float:
    """Multiclass quadratic (Brier) score on normalized distributions, in [0, 2].
    A strictly-proper rule — lower is better."""
    p, o = _to_fractions(pred), _to_fractions(obs)
    return sum((a - b) ** 2 for a, b in zip(p, o, strict=True))


def brier_binary(p_event: float, event: bool) -> float:
    """Brier score for a single binary event (e.g. 'material buyer/action occurred'):
    (p - y)^2, in [0, 1]. Strictly proper. ``p_event`` is a probability in [0, 1]."""
    if not (0.0 <= float(p_event) <= 1.0):
        raise ValueError("p_event must be a probability in [0, 1]")
    y = 1.0 if event else 0.0
    return (float(p_event) - y) ** 2


def directional_hit(
    buyer_pred_pp: float, material_action: bool, *, threshold_pp: float = 10.0
) -> str:
    """Directional buyer-anchor verdict. The prediction 'expects material buyer
    action' iff buyer_pred_pp >= threshold_pp. Returns 'hit' | 'miss'.
    A locked ~0% buyer vs a campaign that DID convert is a 'miss' (the Hollowed
    Oath failure mode)."""
    predicted_action = float(buyer_pred_pp) >= float(threshold_pp)
    return "hit" if predicted_action == bool(material_action) else "miss"


def schema_failure_accounting(schema_failures: Sequence[bool]) -> dict[str, float]:
    """Count + rate of schema failures across a set of predictions."""
    n = len(schema_failures)
    failed = sum(1 for x in schema_failures if x)
    return {
        "n": n,
        "schema_failures": failed,
        "schema_failure_rate": (failed / n) if n else 0.0,
    }
