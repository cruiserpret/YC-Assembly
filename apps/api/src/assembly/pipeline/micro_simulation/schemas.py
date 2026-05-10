"""Phase 8.2K — micro-simulation schemas.

CLOSED enums + Pydantic models with `extra='forbid'`. Names
deliberately carry the `Micro` prefix so future code cannot mistake
a micro-test result for a real simulation output.

CRITICAL framing:
  * `MicroSimulationResult` is **NOT** the same shape as
    `SimulationOutput`. The micro harness never writes to
    `simulation_outputs` / `simulation_rounds` / `population_*`
    tables.
  * Every `MicroSimulationResult` carries a `caveats` list that MUST
    include both the sample-size caveat AND the coverage-thinness
    caveat (validated by post-construction logic in the audit module).
"""
from __future__ import annotations

import enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MicroStance(str, enum.Enum):
    STRONGLY_INTERESTED = "strongly_interested"
    MILDLY_INTERESTED = "mildly_interested"
    CURIOUS_HESITANT = "curious_hesitant"
    CONFUSED = "confused"
    SKEPTICAL = "skeptical"
    RESISTANT = "resistant"


# Phase 8.4B — purchase-intent-shaped closed enum for market-entry
# micro-simulations. Used as the final-stance value in Stage 4 of
# Triton-style runs (and any future unlaunched-product market-entry
# tests). 5 values: a buyer's commitment scale, not the broader 6-
# value "interest" scale of MicroStance.
#
# Mapping from internal MicroStance → MarketEntryFinalStance is
# deterministic (see `map_micro_stance_to_market_entry`):
#   RESISTANT          → reject
#   SKEPTICAL          → skeptical
#   CONFUSED           → curious_but_unconvinced
#   CURIOUS_HESITANT   → curious_but_unconvinced
#   MILDLY_INTERESTED  → willing_to_try_once
#   STRONGLY_INTERESTED → likely_repeat_buyer
class MarketEntryFinalStance(str, enum.Enum):
    REJECT = "reject"
    SKEPTICAL = "skeptical"
    CURIOUS_BUT_UNCONVINCED = "curious_but_unconvinced"
    WILLING_TO_TRY_ONCE = "willing_to_try_once"
    LIKELY_REPEAT_BUYER = "likely_repeat_buyer"


def map_micro_stance_to_market_entry(
    s: MicroStance,
) -> MarketEntryFinalStance:
    """Map the internal 6-value MicroStance to the 5-value
    market-entry purchase-intent stance for reporting."""
    if s == MicroStance.RESISTANT:
        return MarketEntryFinalStance.REJECT
    if s == MicroStance.SKEPTICAL:
        return MarketEntryFinalStance.SKEPTICAL
    if s in (MicroStance.CONFUSED, MicroStance.CURIOUS_HESITANT):
        return MarketEntryFinalStance.CURIOUS_BUT_UNCONVINCED
    if s == MicroStance.MILDLY_INTERESTED:
        return MarketEntryFinalStance.WILLING_TO_TRY_ONCE
    if s == MicroStance.STRONGLY_INTERESTED:
        return MarketEntryFinalStance.LIKELY_REPEAT_BUYER
    # Defensive — unreachable since MicroStance is closed.
    raise ValueError(f"Unmapped MicroStance: {s!r}")  # pragma: no cover


class MicroRoundKind(str, enum.Enum):
    BASELINE = "baseline"
    FIRST_EXPOSURE = "first_exposure"
    OBJECTION = "objection"
    FINAL_STANCE = "final_stance"


class MicroRelevanceLabel(str, enum.Enum):
    RELEVANT = "RELEVANT"
    HIGHLY_RELEVANT = "HIGHLY_RELEVANT"
    WEAKLY_RELEVANT = "WEAKLY_RELEVANT"


class MicroPersonaState(BaseModel):
    """Evidence-bound state for ONE persona at one point in time.

    Constructed from a `PersonaMatch` (Phase 8.2H) plus the underlying
    PersonaRecord / PersonaTrait / PersonaEvidenceLink / SourceRecord
    rows. Carries only DIRECT or INFERRED traits — `unknown` traits are
    excluded so the harness never derives behavior from absent
    evidence.

    `current_stance` is updated round-to-round; `initial_stance` is
    fixed after the deterministic baseline round.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    display_name: str
    relevance_label: MicroRelevanceLabel
    matched_category_key: str
    relevance_score: int = Field(ge=18, le=45)

    # Source-bound traits that exist on the persona. Keys are
    # Phase-8.2A field names; values are the trait's value string.
    supported_traits: dict[str, str]
    # Mapping field_name -> short verbatim excerpt from a bound source
    # record. Lets every per-persona claim cite real evidence.
    evidence_excerpts: dict[str, str] = Field(default_factory=dict)

    initial_stance: MicroStance = MicroStance.CURIOUS_HESITANT
    current_stance: MicroStance = MicroStance.CURIOUS_HESITANT

    caveats: list[str] = Field(default_factory=list)


class MicroRoundResult(BaseModel):
    """One round's output for ONE persona.

    `triggered_by_evidence_excerpt` is required when stance changes —
    every shift must cite a real source-record excerpt the persona
    already carries. No invented quotes.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    round_kind: MicroRoundKind
    stance_before: MicroStance
    stance_after: MicroStance
    reasoning: str = Field(min_length=1, max_length=2000)
    objections: list[str] = Field(default_factory=list)
    evidence_citations: list[str] = Field(default_factory=list)
    triggered_by_evidence_excerpt: str | None = None
    llm_call_was_used: bool
    output_audit_passed: bool
    output_audit_notes: list[str] = Field(default_factory=list)


class MicroDebateTurn(BaseModel):
    """One pairwise debate turn. Optional in the harness.

    Phase 8.2K.1 hardening: `output_audit_notes` surfaces the cause of
    any audit failure (stance-enum repair exhaustion, JSON parse
    failure, forbidden-language hit). Without it, debate failures were
    visible only as a flag without explanation.
    """

    model_config = ConfigDict(extra="forbid")

    speaker_persona_id: str
    target_persona_id: str
    argument: str = Field(min_length=1, max_length=2000)
    cited_evidence_excerpt: str | None = None
    target_stance_before: MicroStance
    target_stance_after: MicroStance
    output_audit_passed: bool
    output_audit_notes: list[str] = Field(default_factory=list)


class MicroTrace(BaseModel):
    """Full in-memory record of the harness run."""

    model_config = ConfigDict(extra="forbid")

    rounds: list[MicroRoundResult] = Field(default_factory=list)
    debate_turns: list[MicroDebateTurn] = Field(default_factory=list)


class MicroSimulationOutputAudit(BaseModel):
    """Forbidden-claim scanner result + caveat tracking."""

    model_config = ConfigDict(extra="forbid")

    forbidden_claims_found: list[str] = Field(default_factory=list)
    rounds_failing_audit: list[str] = Field(default_factory=list)
    caveats_emitted: list[str] = Field(default_factory=list)
    sample_size_caveat_present: bool
    coverage_thinness_caveat_present: bool
    micro_test_label_present: bool


class MicroSimulationResult(BaseModel):
    """Top-level result. NOT a `SimulationOutput`. Operator-only.

    Note the explicit `is_micro_test=True` and the required caveats —
    these are structural guards against any future code path that
    might confuse a micro result with a population-level simulation.
    """

    model_config = ConfigDict(extra="forbid")

    is_micro_test: Literal[True] = True
    brief_label: str
    # Phase 8.4C raised these from le=10 to le=30 to support the
    # 21-person expanded micro-test on the full Phase 8.4A.4
    # production-retrieved included audience. The structural guards
    # against being mistaken for a society-scale run are the mandatory
    # caveats (sample-size, not-a-forecast, coverage-thinness) and the
    # `is_micro_test=True` literal — not this numeric cap.
    persona_count: int = Field(ge=1, le=30)
    relevant_count: int = Field(ge=0, le=30)
    weakly_relevant_count: int = Field(ge=0, le=30)
    mixed_relevance_pool: bool

    persona_states_initial: list[MicroPersonaState]
    persona_states_final: list[MicroPersonaState]

    trace: MicroTrace
    output_audit: MicroSimulationOutputAudit

    dry_run: bool
    llm_call_count: int = Field(ge=0)
    cost_actual_usd: float = Field(ge=0.0)
    cost_cap_usd: float = Field(gt=0.0)

    caveats: list[str] = Field(min_length=2)  # at least sample-size + coverage
    summary_text: str = Field(min_length=1)
