"""Phase 11D.1 — closed enums for tech / startup market intelligence.

Kept in lockstep with the matching DB CHECK constraints on
`tech_market_signal` (see
`assembly/models/tech_market_signal.py`). A drift between this file
and the model/migration would surface as a CHECK violation the first
time Phase 11D.2 ingestion tried to write.
"""
from __future__ import annotations

from typing import Literal


SentimentBucket = Literal["positive", "negative", "mixed"]


# 14 signal types covering the buyer-language surface a tech-startup
# founder cares about — pain urgency on the buyer side, friction on
# the implementation side, procurement and developer trust on the
# enterprise side, and competitor comparison + WTP on the commercial
# side.
SignalType = Literal[
    "pain_urgency",
    "switching_objection",
    "pricing_objection",
    "trust_security_concern",
    "integration_friction",
    "onboarding_friction",
    "support_complaint",
    "competitor_comparison",
    "willingness_to_pay",
    "nice_to_have_risk",
    "feature_not_company_risk",
    "workflow_fit",
    "developer_skepticism",
    "procurement_friction",
]


# Who is voicing the signal. Distinct from "persona" — these are
# the source-side roles the distiller can attribute the snippet to,
# usually inferred from venue (HN comments → developer; G2 reviews →
# admin/buyer; consumer App Store → user; LinkedIn → buyer/founder).
BuyerType = Literal[
    "user",
    "buyer",
    "developer",
    "founder",
    "admin",
    "investor",
    "unknown",
]


# What kind of market is the source talking about. Different
# market contexts have different implicit assumptions about price,
# procurement, and integration depth.
MarketContext = Literal[
    "B2C",
    "B2B",
    "prosumer",
    "devtool",
    "marketplace",
    "AI_tool",
    "unknown",
]


SENTIMENT_BUCKETS: tuple[SentimentBucket, ...] = (
    "positive", "negative", "mixed",
)

SIGNAL_TYPES: tuple[SignalType, ...] = (
    "pain_urgency",
    "switching_objection",
    "pricing_objection",
    "trust_security_concern",
    "integration_friction",
    "onboarding_friction",
    "support_complaint",
    "competitor_comparison",
    "willingness_to_pay",
    "nice_to_have_risk",
    "feature_not_company_risk",
    "workflow_fit",
    "developer_skepticism",
    "procurement_friction",
)

BUYER_TYPES: tuple[BuyerType, ...] = (
    "user", "buyer", "developer", "founder",
    "admin", "investor", "unknown",
)

MARKET_CONTEXTS: tuple[MarketContext, ...] = (
    "B2C", "B2B", "prosumer", "devtool",
    "marketplace", "AI_tool", "unknown",
)


# Assembly-side product category labels — controlled vocabulary
# mapped from the upstream provider's `source_category` during
# distillation. Each fixture file in Phase 11D.1 covers one of
# these.
PRODUCT_CATEGORIES: tuple[str, ...] = (
    "ai_saas",
    "browser_extension",
    "devtool_api",
    "b2b_workflow_saas",
    "consumer_mobile_app",
    "marketplace",
    "unknown",
)
