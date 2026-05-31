"""Phase 15C — action-signal taxonomy + schema (EVIDENCE REPRESENTATION ONLY).

This module models the distinction between **what people SAY** (public opinion)
and **what people DO** (revealed action). It is a representation layer: it
classifies and describes evidence. It produces **no forecast**, changes **no
live output**, and applies **no calibration**. The strength weights here are
heuristic, theory-ordered DEFAULTS — not values tuned to any validation case.

Signal tiers (predictiveness of real market proportions, strongest first):
  Tier 1 — revealed action:    purchase, paid signup, backer pledge, trial
           conversion, demo request, install/download, GitHub fork (dev tools),
           retention/churn.
  Tier 2 — semi-action:        GitHub star, PH upvote/follow, waitlist signup,
           Discord join, bookmark/share, traffic, search interest.
  Tier 3 — public opinion:     comment sentiment, public praise/criticism,
           forum/social discussion, reviews.
  Tier 4 — synthetic:          deep-agent forecast, 100-voter forecast,
           behavioral-layer forecast (only if later validated).

No LLM, no network, no DB. Pure, deterministic, unit-tested.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Parallel to the ledger's SourceType, kept LOCAL here so this module has no
# dependency on the ledger (the dependency direction is ledger -> calibration).
SourceType = Literal[
    "hacker_news",
    "product_hunt",
    "kickstarter",
    "reddit",
    "github",
    "app_store",
    "b2b",
    "mixed",
    "unknown",
]

SignalDirection = Literal["positive", "negative", "mixed", "unknown"]
Confidence = Literal["low", "medium", "high"]
SignalTier = Literal[1, 2, 3, 4]

# ---------------------------------------------------------------------------
# Canonical signal_type -> tier taxonomy. Theory-ordered, not outcome-derived.
# ---------------------------------------------------------------------------
TIER1_SIGNALS: frozenset[str] = frozenset({
    "purchase",
    "paid_signup",
    "kickstarter_pledge",
    "backer_pledge",
    "trial_conversion",
    "demo_request",
    "install",
    "download",
    "github_fork",
    "retention",
    "churn",
})

TIER2_SIGNALS: frozenset[str] = frozenset({
    "github_star",
    "product_hunt_upvote",
    "follow",
    "waitlist_signup",
    "discord_join",
    "bookmark",
    "share",
    "traffic",
    "search_interest",
})

TIER3_SIGNALS: frozenset[str] = frozenset({
    "comment_sentiment",
    "public_praise",
    "public_criticism",
    "forum_discussion",
    "social_discussion",
    "review",
})

TIER4_SIGNALS: frozenset[str] = frozenset({
    "deep_agent_forecast",
    "voter_100_forecast",
    "behavioral_forecast",
})

SIGNAL_TIERS: dict[str, int] = {
    **{s: 1 for s in TIER1_SIGNALS},
    **{s: 2 for s in TIER2_SIGNALS},
    **{s: 3 for s in TIER3_SIGNALS},
    **{s: 4 for s in TIER4_SIGNALS},
}


class ActionSignal(BaseModel):
    """One piece of evidence about a market's reaction to a product.

    Describes a signal; it is NOT a forecast and carries no market-proportion
    distribution. ``tier`` auto-populates from the canonical taxonomy when the
    ``signal_type`` is known; an explicit ``tier`` may be supplied for custom
    signal types not in the taxonomy.
    """

    model_config = ConfigDict(extra="forbid")

    signal_type: str
    source_type: SourceType = "unknown"
    tier: SignalTier | None = None
    count: float | None = None  # count or value, if known
    denominator: float | None = None  # population the count is out of, if known
    direction: SignalDirection = "unknown"
    confidence: Confidence = "medium"
    notes: str = ""
    observed_at: str | None = None  # ISO date/datetime string, optional
    source_reference: str | None = None  # url / source ref, optional

    @model_validator(mode="after")
    def _autofill_tier(self) -> ActionSignal:
        # Known signal types are authoritatively classified by the taxonomy.
        # Unknown types keep whatever explicit tier was provided (may be None).
        canonical = SIGNAL_TIERS.get(self.signal_type)
        if canonical is not None:
            object.__setattr__(self, "tier", canonical)
        return self


def classify_action_signal(signal: ActionSignal) -> int | None:
    """Return the canonical tier (1-4) for a signal.

    Known ``signal_type`` -> taxonomy tier. Unknown type -> the signal's
    explicit ``tier`` if provided, else None (genuinely unclassified).
    """
    canonical = SIGNAL_TIERS.get(signal.signal_type)
    if canonical is not None:
        return canonical
    return signal.tier
