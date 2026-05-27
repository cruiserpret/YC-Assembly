"""Phase 9E — Pydantic schemas for the intent layer.

`extra="forbid"`. Closed-set Literals.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


IntentLabel = Literal[
    "would_buy_now",
    "would_try_once",
    "would_join_waitlist",
    "would_consider_if_proven",
    "would_share_with_friend",
    "would_compare_to_current_brand",
    "loyal_to_current_alternative",
    "would_reject",
    "would_block",
    # Phase 12A.10 — new label routing ambiguous/wait-and-see
    # personas to the calibration `uncertain` bucket. Pre-12A.10
    # these cases were forced into `would_consider_if_proven`
    # (→ receptive), which Phase 12A.9 measurement showed
    # over-predicted receptive by +18pp and under-predicted
    # uncertain by ~18pp on the Opslane Show-HN blind case.
    "wait_and_see",
]
IntentStrength = Literal["low", "medium", "high"]
SwitchingStatus = Literal[
    "no_current_alternative",
    "weakly_attached_to_alternative",
    "actively_comparing",
    "loyal_to_current_alternative",
    "refuses_switching",
]
StanceLabel = Literal[
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
]
Confidence = Literal["high", "medium", "low"]

# Phase 12A.10D — explicit intent signal layered on top of the older
# stance/intent vocabulary. Derived from existing ballot fields
# (private_stance + private_reasoning text + top_objection +
# top_proof_need + persona psy + role); ZERO new LLM calls.
#
# The cascade currently collapses ambiguous/curious/proof-seeking
# personas into `would_consider_if_proven` (-> receptive) because the
# 5-element stance enum can't distinguish "I'd consider it if proven"
# from "I'm asking how it works". This enum makes the distinction
# explicit so the bucket map can finally route uncertain mass away
# from receptive without losing real positive interest.
IntentSignal = Literal[
    # Buyer-bucket signals (real adoption intent)
    "explicit_buy_or_use_now",
    "explicit_try_once",
    "explicit_waitlist_or_signup",
    # Receptive-bucket signals (positive, but not yet committed)
    "positive_interest_if_proven",
    "would_compare_to_current_tool",
    # Uncertain-bucket signals (informational / proof-seeking /
    # mixed — NOT positive adoption intent)
    "curious_but_unconvinced",
    "needs_more_information",
    "neutral_information_seeking",
    "mixed_or_ambiguous",
    # Skeptical-bucket signals (real resistance)
    "trust_blocked",
    "price_blocked",
    "competitor_loyal",
    "explicit_rejection",
    "not_target_customer",
    # Noise
    "off_topic_or_noise",
]
ArgumentType = Literal[
    "objection", "proof_need", "persuasion_lever",
    "switching_trigger", "loyalist_resistance",
    "price_value", "trust_safety",
]
ArgumentOrigin = Literal[
    "cohort", "persona", "discussion_turn", "ballot",
]
ResponseType = Literal[
    "adopted", "resisted", "modified", "ignored", "intensified",
]
IntentEffect = Literal[
    "increased_intent", "decreased_intent",
    "no_change", "increased_uncertainty",
]


class SimulatedIntentDraft(BaseModel):
    """Pre-persistence shape of a simulated intent record.

    The DB CHECK constraints enforce non-empty `evidence_basis` and
    `caveat`; the Pydantic-side check mirrors these so the orchestrator
    can refuse early.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    cohort_id: str | None = None
    stance_label: StanceLabel
    simulated_intent: IntentLabel
    intent_strength: IntentStrength
    switching_status: SwitchingStatus
    current_alternative: str | None = None
    conditions_to_buy: list[str] = Field(default_factory=list)
    reason_for_rejection: str | None = None
    proof_needed: list[str] = Field(default_factory=list)
    evidence_basis: str = Field(min_length=1)
    discussion_turn_ids: list[str] = Field(default_factory=list)
    ballot_ids: list[str] = Field(default_factory=list)
    memory_atom_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    caveat: str = Field(min_length=1)
    # Phase 12A.10D — derived intent signal. Optional / nullable for
    # backward compatibility with legacy artifacts that pre-date this
    # field. When present, downstream bucket mapping uses
    # `map_intent_signal_to_market_bucket` instead of the legacy
    # `map_assembly_intent_to_market_bucket` (gated by config flag).
    intent_signal: IntentSignal | None = None
    intent_signal_basis: str | None = None


class ArgumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_type: ArgumentOrigin
    origin_ref_id: str = Field(min_length=1)  # UUID string
    argument_text: str = Field(min_length=1)
    argument_type: ArgumentType
    source_cohort_id: str | None = None
    supporting_turn_ids: list[str] = Field(default_factory=list)
    supporting_memory_atom_ids: list[str] = Field(default_factory=list)


class PropagationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argument_id: str  # set by orchestrator after argument insert
    target_cohort_id: str
    representative_persona_id: str | None = None
    response_type: ResponseType
    response_text: str = Field(min_length=1)
    effect_on_intent: IntentEffect
    evidence_basis: str = Field(min_length=1)
