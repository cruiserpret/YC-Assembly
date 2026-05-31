"""Phase 15C — deterministic action-signal weighting + aggregation.

Pure functions over ActionSignal lists. They summarize WHAT EVIDENCE EXISTS
and HOW STRONG it is — they do NOT produce a market-proportion forecast and do
NOT touch any live output. The strength weights are heuristic, theory-ordered
DEFAULTS (revealed action > semi-action > opinion ≈ synthetic), with a few
generic, domain-knowledge source/category adjustments. They are NOT tuned to
the validation cases and NEVER read observed outcomes — that calibration is
Phase 15D/15E, after a holdout set exists.

No LLM, no network, no DB, no randomness.
"""
from __future__ import annotations

from collections.abc import Sequence

from assembly.market_calibration.action_signals import (
    SIGNAL_TIERS,
    ActionSignal,
    classify_action_signal,
)

# Base strength by tier (predictiveness of real proportions). Heuristic,
# theory-ordered; NOT calibrated. Tier 4 (synthetic) ≈ Tier 3 (opinion): it is
# the thing being validated, not independent evidence.
_TIER_BASE_STRENGTH: dict[int, float] = {1: 1.0, 2: 0.6, 3: 0.3, 4: 0.3}
_UNKNOWN_STRENGTH = 0.2  # conservative: an unclassified signal is weak by default

# Generic domain-knowledge categories for the GitHub adjustment only.
_DEV_CATEGORIES = frozenset({"developer_tools", "open_source_software"})
_NON_DEV_CATEGORIES = frozenset({"consumer_apps", "crowdfunding_hardware"})
_GITHUB_SIGNALS = frozenset({"github_star", "github_fork"})


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def default_signal_strength(
    signal_type: str,
    source_type: str = "unknown",
    product_category: str | None = None,
) -> float:
    """Heuristic default strength in [0, 1] for one signal type.

    Tier base strength, adjusted by a few generic source/category factors.
    NOT a calibrated weight — a representation default that Phase 15D may
    later replace with weights learned on a holdout set.
    """
    tier = SIGNAL_TIERS.get(signal_type)
    if tier is None:
        return _UNKNOWN_STRENGTH
    base = _TIER_BASE_STRENGTH[tier]

    adj = 1.0
    # GitHub signals are meaningful for dev/OSS products, weak elsewhere.
    if signal_type in _GITHUB_SIGNALS:
        if product_category in _DEV_CATEGORIES:
            adj = 1.0
        elif product_category in _NON_DEV_CATEGORIES:
            adj = 0.5
        else:
            adj = 0.75  # unknown category — neutral discount
    # Product Hunt upvotes carry novelty/social noise.
    elif signal_type == "product_hunt_upvote":
        adj = 0.85
    # Aggregate traffic / search interest is a noisy proxy.
    elif signal_type in {"traffic", "search_interest"}:
        adj = 0.9

    return round(_clamp01(base * adj), 4)


def evidence_tier_summary(signals: Sequence[ActionSignal]) -> dict[int, int]:
    """Count of signals per tier (1-4). Unclassified signals are omitted."""
    out: dict[int, int] = {}
    for s in signals:
        t = classify_action_signal(s)
        if t is not None:
            out[t] = out.get(t, 0) + 1
    return out


def has_tier1_action_evidence(signals: Sequence[ActionSignal]) -> bool:
    """True iff any signal is a Tier-1 revealed action."""
    return any(classify_action_signal(s) == 1 for s in signals)


def action_signal_confidence(signals: Sequence[ActionSignal]) -> str:
    """Overall evidence confidence: 'high' | 'medium' | 'low'.

    - high   : a Tier-1 action that is quantified (has a count or denominator)
    - medium : a Tier-1 action without quantity, OR a quantified Tier-2 signal
    - low    : only opinion/synthetic/unquantified, or no signals
    """
    if not signals:
        return "low"
    tier1 = [s for s in signals if classify_action_signal(s) == 1]
    tier2 = [s for s in signals if classify_action_signal(s) == 2]
    quantified_t1 = any(
        s.count is not None or s.denominator is not None for s in tier1
    )
    if tier1 and quantified_t1:
        return "high"
    if tier1 or any(s.count is not None for s in tier2):
        return "medium"
    return "low"


def aggregate_action_signals(
    signals: Sequence[ActionSignal],
    product_category: str | None = None,
) -> dict[str, object]:
    """Summarize a set of action signals into an EVIDENCE profile.

    Returns counts/strength by tier, the strongest tier present, a
    strength-weighted dominant direction, and an overall confidence. This is an
    evidence summary, NOT a market-proportion forecast — it intentionally emits
    no buyer/receptive/uncertain/skeptical distribution.
    """
    by_tier: dict[int, dict[str, float]] = {}
    total_strength = 0.0
    pos = 0.0
    neg = 0.0
    for s in signals:
        t = classify_action_signal(s)
        strength = default_signal_strength(
            s.signal_type, s.source_type, product_category
        )
        total_strength += strength
        if t is not None:
            slot = by_tier.setdefault(t, {"count": 0.0, "total_strength": 0.0})
            slot["count"] += 1
            slot["total_strength"] += strength
        if s.direction == "positive":
            pos += strength
        elif s.direction == "negative":
            neg += strength

    net = pos - neg
    if pos == 0.0 and neg == 0.0:
        dominant = "unknown"
    elif net > 1e-9:
        dominant = "positive"
    elif net < -1e-9:
        dominant = "negative"
    else:
        dominant = "mixed"

    present = [t for t in by_tier]
    return {
        "n_signals": len(signals),
        "total_strength": round(total_strength, 4),
        "by_tier": {
            t: {
                "count": int(by_tier[t]["count"]),
                "total_strength": round(by_tier[t]["total_strength"], 4),
            }
            for t in sorted(by_tier)
        },
        "highest_tier_present": min(present) if present else None,
        "dominant_direction": dominant,
        "net_direction_strength": round(net, 4),
        "has_tier1": has_tier1_action_evidence(signals),
        "confidence": action_signal_confidence(signals),
    }
