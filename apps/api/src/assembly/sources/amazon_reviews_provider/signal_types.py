"""Phase 11A — closed enums for sentiment + signal_type.

Kept in lockstep with the matching DB CHECK constraints on
`amazon_review_signal` (see
`assembly/models/amazon_review_signal.py`). A drift between this
file and the model/migration would surface as a CHECK violation the
first time a Phase 11B ingestion run tried to write.
"""
from __future__ import annotations

from typing import Literal

SentimentBucket = Literal["positive", "negative", "mixed"]

SignalType = Literal[
    "objection",
    "praise",
    "proof_need",
    "switch_reason",
    "return_reason",
    "durability",
    "price",
    "trust",
    "safety",
    "setup",
    "support",
    "use_case",
]

SENTIMENT_BUCKETS: tuple[SentimentBucket, ...] = (
    "positive", "negative", "mixed",
)

SIGNAL_TYPES: tuple[SignalType, ...] = (
    "objection",
    "praise",
    "proof_need",
    "switch_reason",
    "return_reason",
    "durability",
    "price",
    "trust",
    "safety",
    "setup",
    "support",
    "use_case",
)
