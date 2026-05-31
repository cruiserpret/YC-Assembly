"""Phase 15C — market-calibration: action-signal representation layer.

Distinguishes what people SAY (public opinion) from what people DO (revealed
action). EVIDENCE REPRESENTATION ONLY — no forecast, no live-output change, no
calibration, no LLM/network/DB. Weights are heuristic theory-ordered defaults,
never tuned to validation outcomes. Phase 15D/15E will consume this layer to
learn source/category priors + a calibrated forecast on a held-out set.

See docs/PHASE_15C_ACTION_SIGNAL_WEIGHTING.md.
"""
from __future__ import annotations

from assembly.market_calibration.action_signals import (
    SIGNAL_TIERS,
    TIER1_SIGNALS,
    TIER2_SIGNALS,
    TIER3_SIGNALS,
    TIER4_SIGNALS,
    ActionSignal,
    classify_action_signal,
)
from assembly.market_calibration.signal_weights import (
    action_signal_confidence,
    aggregate_action_signals,
    default_signal_strength,
    evidence_tier_summary,
    has_tier1_action_evidence,
)

__all__ = [
    "ActionSignal",
    "classify_action_signal",
    "SIGNAL_TIERS",
    "TIER1_SIGNALS",
    "TIER2_SIGNALS",
    "TIER3_SIGNALS",
    "TIER4_SIGNALS",
    "default_signal_strength",
    "aggregate_action_signals",
    "evidence_tier_summary",
    "has_tier1_action_evidence",
    "action_signal_confidence",
]
