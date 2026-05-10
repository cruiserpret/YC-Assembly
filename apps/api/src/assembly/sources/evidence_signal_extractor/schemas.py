"""Phase 9A.1 — schemas for the atomic evidence signal extractor.

`extra="forbid"`. Closed-set Literal for SignalType.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SignalType = Literal[
    "competitor_usage_signal",
    "substitute_usage_signal",
    "use_case_signal",
    "objection_signal",
    "price_value_signal",
    "trust_proof_signal",
    "format_preference_signal",
    "safety_visibility_signal",
    "convenience_signal",
    "performance_signal",
]


class EvidenceSignal(BaseModel):
    """One atomic signal extracted from one evidence item."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1)
    source_record_synthetic_id: str = Field(min_length=1)
    provider: str
    source_url: str | None = None
    domain: str | None = None
    signal_type: SignalType

    # Inferred role this signal contributes to. Universal — always a
    # `competitor_user_<X>` / `substitute_user_<X>` / lexicon-derived
    # role string (e.g. `price_skeptic`, `safety_visibility_focused_buyer`).
    inferred_role: str
    inferred_subsegment: str | None = None
    competitor_or_substitute_context: str | None = None
    use_case_context: str | None = None
    objection_pattern: str | None = None
    trust_or_proof_requirement: str | None = None
    price_or_value_signal: str | None = None
    behavior_context: str | None = None

    evidence_excerpt: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    reason_for_signal: str
