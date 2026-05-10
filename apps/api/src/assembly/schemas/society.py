"""Phase 5 — Society schema.

Each agent is a buyer-state model, NOT a persona. Every field carries a
`basis` so we can distinguish:

  - `direct_evidence`     — anchored to a kind=direct, non-user_input
                             evidence_item (fetched competitor page, pricing,
                             public review).
  - `user_input`           — anchored to a kind=direct, source_type=user_input
                             evidence_item.
  - `analogical_evidence`  — anchored to a kind=analogical evidence_item
                             (e.g., extracted category language).
  - `assumption`           — explicitly labeled assumption with a one-sentence
                             rationale. Paired with kind=missing in the
                             evidence ledger.

Pydantic enforces structural consistency. Substance — i.e. that an
`evidence_anchor` UUID actually exists in the simulation's evidence ledger,
and that the `value` text doesn't contain forbidden language — is checked
post-Pydantic in `pipeline.society_builder.validate_society`.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BasisKind(str, Enum):
    DIRECT_EVIDENCE = "direct_evidence"
    USER_INPUT = "user_input"
    ANALOGICAL_EVIDENCE = "analogical_evidence"
    ASSUMPTION = "assumption"


_EVIDENCE_BASIS = (
    BasisKind.DIRECT_EVIDENCE,
    BasisKind.USER_INPUT,
    BasisKind.ANALOGICAL_EVIDENCE,
)


class AgentField(BaseModel):
    """A single buyer-state field with explicit basis. The string `value` is
    deliberately free-form (e.g., '$10k–$80k MRR', 'fewer plugins, more
    control', 'overwhelmed and skeptical'). The basis tag plus
    `evidence_anchors` are what make the field non-fictional."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(min_length=1)
    basis: BasisKind

    # Required when basis ∈ {direct_evidence, user_input, analogical_evidence}.
    # IDs reference rows in the simulation's evidence ledger.
    evidence_anchors: list[UUID] = Field(default_factory=list)

    # Required when basis == assumption.
    assumption_rationale: str | None = None

    # Optional — for assumption only — the evidence_item id of a kind=missing
    # row that this assumption "fills". Lets the report show *which* gap an
    # assumption is compensating for.
    missing_evidence_link: UUID | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "AgentField":
        if self.basis in _EVIDENCE_BASIS:
            if not self.evidence_anchors:
                raise ValueError(
                    f"basis={self.basis.value} requires at least one evidence_anchor"
                )
            if self.assumption_rationale is not None:
                raise ValueError(
                    f"assumption_rationale must be null for basis={self.basis.value}"
                )
            if self.missing_evidence_link is not None:
                raise ValueError(
                    f"missing_evidence_link must be null for basis={self.basis.value}"
                )
        elif self.basis == BasisKind.ASSUMPTION:
            if not self.assumption_rationale:
                raise ValueError("basis=assumption requires assumption_rationale")
            if self.evidence_anchors:
                raise ValueError(
                    "evidence_anchors must be empty for basis=assumption"
                )
        return self


# ---------------------------------------------------------------------------
# LLM-facing draft shapes (no UUIDs; assigned in code post-parse)
# ---------------------------------------------------------------------------


class LLMAgentDraft(BaseModel):
    """Shape we ask the LLM to produce, one entry per agent in the society.

    The agent's identity (UUID) is assigned in code; the LLM only sees its
    position in the list and references other agents by index in `edges`.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    segment: str = Field(min_length=2)
    role: str = Field(min_length=2)
    cluster: str | None = None
    weight: float = Field(gt=0.0, le=1.0, default=0.0625)

    # One-sentence buyer-state summary. Min 75 chars enforces substance.
    summary: str = Field(min_length=75)

    # 9 buyer-state fields — every required, every with basis.
    current_alternatives: AgentField
    budget_level: AgentField
    trust_threshold: AgentField
    switching_trigger: AgentField
    fear: AgentField
    desire: AgentField
    price_sensitivity: AgentField
    objection_pattern: AgentField
    emotional_state: AgentField

    # Social
    influence_score: float = Field(ge=0.0, le=1.0)
    susceptibility_to_peer_shift: float = Field(ge=0.0, le=1.0)

    # Awareness — explicit list of assumptions and missing-evidence notes
    # that influenced this agent's caution / certainty.
    assumptions: list[str] = Field(default_factory=list)
    missing_evidence_awareness: list[str] = Field(default_factory=list)

    # Phase 5.5 — explicit six-layer trait architecture. Persisted to
    # `agents.traits` JSONB. AgentTraits is defined later in this file;
    # forward-referenced by string here to avoid a definition-order issue.
    traits: "AgentTraits"


class LLMEdgeDraft(BaseModel):
    """Influence edge between two agents, by their list-index in the society.

    `influence_strength` is the weight the social-influence simulation round
    will use to sample debate pairs and propagate stance shifts."""

    model_config = ConfigDict(extra="forbid")

    source_index: int = Field(ge=0)
    target_index: int = Field(ge=0)
    influence_strength: float = Field(ge=0.0, le=1.0)
    cluster_label: str | None = None

    @model_validator(mode="after")
    def _no_self_edges(self) -> "LLMEdgeDraft":
        if self.source_index == self.target_index:
            raise ValueError("influence edges may not be self-loops")
        return self


class LLMSocietyDraft(BaseModel):
    """Full society payload returned by the LLM."""

    model_config = ConfigDict(extra="forbid")

    agents: list[LLMAgentDraft] = Field(min_length=1)
    edges: list[LLMEdgeDraft] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final post-assignment shapes (with UUIDs)
# ---------------------------------------------------------------------------


class GeneratedAgent(BaseModel):
    """Final agent shape, persisted into `agents.buyer_state` JSONB."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: UUID = Field(default_factory=uuid4)
    segment: str
    role: str
    cluster: str | None = None
    weight: float = Field(gt=0.0, le=1.0)
    summary: str

    current_alternatives: AgentField
    budget_level: AgentField
    trust_threshold: AgentField
    switching_trigger: AgentField
    fear: AgentField
    desire: AgentField
    price_sensitivity: AgentField
    objection_pattern: AgentField
    emotional_state: AgentField

    influence_score: float = Field(ge=0.0, le=1.0)
    susceptibility_to_peer_shift: float = Field(ge=0.0, le=1.0)

    assumptions: list[str] = Field(default_factory=list)
    missing_evidence_awareness: list[str] = Field(default_factory=list)

    # Phase 5.5 — explicit six-layer trait architecture. Persisted to
    # `agents.traits` JSONB.
    traits: "AgentTraits"

    # ---- helpers ------------------------------------------------------

    def all_evidence_anchors(self) -> list[UUID]:
        """Return the union of every per-field anchor across the
        GeneratedAgent fields AND the six trait layers. Used at persistence
        time to populate the ORM column `agents.evidence_anchors`."""
        seen: set[UUID] = set()
        for f in (
            self.current_alternatives,
            self.budget_level,
            self.trust_threshold,
            self.switching_trigger,
            self.fear,
            self.desire,
            self.price_sensitivity,
            self.objection_pattern,
            self.emotional_state,
        ):
            for a in f.evidence_anchors:
                seen.add(a)
        for a in self.traits.all_evidence_anchors():
            seen.add(a)
        return sorted(seen, key=str)

    def fields_iter(self) -> list[tuple[str, AgentField]]:
        """Return [(field_name, AgentField), ...] for the 9 buyer-state
        fields, in canonical order. Used by the validator and persistence."""
        return [
            ("current_alternatives", self.current_alternatives),
            ("budget_level", self.budget_level),
            ("trust_threshold", self.trust_threshold),
            ("switching_trigger", self.switching_trigger),
            ("fear", self.fear),
            ("desire", self.desire),
            ("price_sensitivity", self.price_sensitivity),
            ("objection_pattern", self.objection_pattern),
            ("emotional_state", self.emotional_state),
        ]


class InfluenceEdge(BaseModel):
    """Final edge shape, one row per `agent_edges` table entry."""

    model_config = ConfigDict(extra="forbid")

    source_agent_id: UUID
    target_agent_id: UUID
    influence_strength: float = Field(ge=0.0, le=1.0)
    cluster_label: str | None = None


class SocietyValidationError(BaseModel):
    """One failure in `validate_society`. Suitable for echoing to the LLM
    in the repair loop."""

    model_config = ConfigDict(extra="forbid")

    agent_index: int | None = None
    agent_id: UUID | None = None
    field_path: str
    rule: str
    message: str


class SocietyBuildResult(BaseModel):
    """Top-level output of `pipeline.society_builder.build_society`."""

    model_config = ConfigDict(extra="forbid")

    agents: list[GeneratedAgent]
    edges: list[InfluenceEdge]
    segments: list[str] = Field(default_factory=list)
    repair_attempts_used: int = 0
    raw_response_text: str = ""
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 5.5 — Six-Layer Trait Model
# ---------------------------------------------------------------------------
#
# `AgentTraits` is the explicit six-layer trait architecture, persisted into
# `agents.traits` JSONB (separate from `buyer_state` which holds the
# GeneratedAgent dump). Every field carries its own provenance via either an
# `AgentField` (free-text + basis) or a `CategoricalTrait` (low/moderate/high
# + rationale + basis).
#
# IMPORTANT: many trait fields will be `basis=assumption` in V0 because the
# user brief rarely supplies psychological detail. Assumption is the honest
# label — never invent confident facts about agent psychology.


TraitLevel = Literal["low", "moderate", "high"]


class CategoricalTrait(BaseModel):
    """A bounded trait (low/moderate/high) with a one-line rationale and the
    same provenance discipline as AgentField. Used for OCEAN traits, risk
    sensitivity, skepticism level, status sensitivity, etc."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level: TraitLevel
    rationale: str = Field(min_length=10)
    basis: BasisKind
    evidence_anchors: list[UUID] = Field(default_factory=list)
    assumption_rationale: str | None = None
    missing_evidence_link: UUID | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "CategoricalTrait":
        if self.basis in _EVIDENCE_BASIS:
            if not self.evidence_anchors:
                raise ValueError(
                    f"basis={self.basis.value} requires at least one evidence_anchor"
                )
            if self.assumption_rationale is not None:
                raise ValueError(
                    f"assumption_rationale must be null for basis={self.basis.value}"
                )
            if self.missing_evidence_link is not None:
                raise ValueError(
                    f"missing_evidence_link must be null for basis={self.basis.value}"
                )
        elif self.basis == BasisKind.ASSUMPTION:
            if not self.assumption_rationale:
                raise ValueError("basis=assumption requires assumption_rationale")
            if self.evidence_anchors:
                raise ValueError(
                    "evidence_anchors must be empty for basis=assumption"
                )
        return self


# Layer 1 — buyer-state additions (the rest is on GeneratedAgent already)
class BuyerStateLayer(BaseModel):
    """Layer 1. Extra buyer-state fields not already on `GeneratedAgent`.

    The 5 fields already on `GeneratedAgent` (current_alternatives,
    switching_trigger, fear, desire, objection_pattern, emotional_state) are
    intentionally NOT duplicated here to keep persistence non-redundant."""

    model_config = ConfigDict(extra="forbid")

    current_workflow: AgentField
    current_pain: AgentField
    category_familiarity: CategoricalTrait


# Layer 2 — OCEAN-like
class OCEANLayer(BaseModel):
    """Layer 2. Five categorical traits. All assumption-basis is acceptable
    — these are inferences, not facts about the user."""

    model_config = ConfigDict(extra="forbid")

    openness: CategoricalTrait
    conscientiousness: CategoricalTrait
    extraversion: CategoricalTrait
    agreeableness: CategoricalTrait
    neuroticism_or_risk_sensitivity: CategoricalTrait


# Layer 3 — Economic
class EconomicLayer(BaseModel):
    """Layer 3. Free-text where amounts vary by buyer; categorical where it
    cleanly does (none of this layer is categorical in V0)."""

    model_config = ConfigDict(extra="forbid")

    willingness_to_pay: AgentField
    roi_expectation: AgentField
    cost_of_current_alternative: AgentField
    purchase_authority: AgentField
    time_to_value_expectation: AgentField


# Layer 4 — Trust / Proof / Risk
class TrustProofRiskLayer(BaseModel):
    """Layer 4. Mix of free-text and categorical."""

    model_config = ConfigDict(extra="forbid")

    proof_requirement: AgentField
    skepticism_level: CategoricalTrait
    risk_tolerance: CategoricalTrait
    brand_control_sensitivity: CategoricalTrait
    required_credibility_signal: AgentField
    fear_of_downside: AgentField


# Layer 5 — Social Influence
class SocialInfluenceLayer(BaseModel):
    """Layer 5. The numeric influence_score / susceptibility_to_peer_shift
    live on GeneratedAgent already. This layer adds tendencies + a placeholder
    for the simulation engine to populate trust edges."""

    model_config = ConfigDict(extra="forbid")

    status_sensitivity: CategoricalTrait
    word_of_mouth_likelihood: CategoricalTrait
    # Phase 6 will populate this with agent_ids the agent trusts. V0 leaves
    # it empty; the field exists so the schema doesn't change between phases.
    trust_edges_placeholder: list[UUID] = Field(default_factory=list)


# Layer 6 — Emotional / JTBD
class EmotionalJTBDLayer(BaseModel):
    """Layer 6. switch_trigger overlaps with `GeneratedAgent.switching_trigger`
    — kept on GeneratedAgent. emotional_state_toward_category overlaps with
    `GeneratedAgent.emotional_state` — also kept on GeneratedAgent."""

    model_config = ConfigDict(extra="forbid")

    push_pain: AgentField
    pull_attraction: AgentField
    anxiety: AgentField
    habit: AgentField
    desired_transformation: AgentField


class AgentTraits(BaseModel):
    """The full six-layer trait architecture. Persisted into
    `agents.traits` JSONB. Every field's value carries its own basis +
    optional evidence_anchors + assumption_rationale.

    Non-overlapping with `GeneratedAgent`: fields that already live on
    GeneratedAgent (current_alternatives, fear, desire, switching_trigger,
    emotional_state, objection_pattern, trust_threshold, budget_level,
    price_sensitivity, influence_score, susceptibility_to_peer_shift,
    cluster) are intentionally NOT repeated here. The full agent state is
    GeneratedAgent + AgentTraits, persisted as buyer_state JSONB +
    traits JSONB respectively."""

    model_config = ConfigDict(extra="forbid")

    buyer_state: BuyerStateLayer
    ocean: OCEANLayer
    economic: EconomicLayer
    trust_proof_risk: TrustProofRiskLayer
    social_influence: SocialInfluenceLayer
    emotional_jtbd: EmotionalJTBDLayer

    # ---- helpers ------------------------------------------------------

    def all_categorical_fields(self) -> list[tuple[str, "CategoricalTrait"]]:
        """Walk every CategoricalTrait across the six layers. Used by the
        validator to apply categorical-trait checks uniformly."""
        out: list[tuple[str, CategoricalTrait]] = []
        out.append(("buyer_state.category_familiarity", self.buyer_state.category_familiarity))
        for name in ("openness", "conscientiousness", "extraversion",
                     "agreeableness", "neuroticism_or_risk_sensitivity"):
            out.append((f"ocean.{name}", getattr(self.ocean, name)))
        out.append(("trust_proof_risk.skepticism_level", self.trust_proof_risk.skepticism_level))
        out.append(("trust_proof_risk.risk_tolerance", self.trust_proof_risk.risk_tolerance))
        out.append(("trust_proof_risk.brand_control_sensitivity",
                    self.trust_proof_risk.brand_control_sensitivity))
        out.append(("social_influence.status_sensitivity", self.social_influence.status_sensitivity))
        out.append(("social_influence.word_of_mouth_likelihood",
                    self.social_influence.word_of_mouth_likelihood))
        return out

    def all_agent_field_paths(self) -> list[tuple[str, "AgentField"]]:
        """Walk every AgentField across the six layers."""
        out: list[tuple[str, AgentField]] = []
        out.append(("buyer_state.current_workflow", self.buyer_state.current_workflow))
        out.append(("buyer_state.current_pain", self.buyer_state.current_pain))

        for name in ("willingness_to_pay", "roi_expectation",
                     "cost_of_current_alternative", "purchase_authority",
                     "time_to_value_expectation"):
            out.append((f"economic.{name}", getattr(self.economic, name)))

        for name in ("proof_requirement", "required_credibility_signal", "fear_of_downside"):
            out.append((f"trust_proof_risk.{name}", getattr(self.trust_proof_risk, name)))

        for name in ("push_pain", "pull_attraction", "anxiety", "habit", "desired_transformation"):
            out.append((f"emotional_jtbd.{name}", getattr(self.emotional_jtbd, name)))
        return out

    def all_evidence_anchors(self) -> list[UUID]:
        """Return the union of every per-field anchor across all six layers,
        for the rolled-up `agents.evidence_anchors` column."""
        seen: set[UUID] = set()
        for _, ct in self.all_categorical_fields():
            for a in ct.evidence_anchors:
                seen.add(a)
        for _, af in self.all_agent_field_paths():
            for a in af.evidence_anchors:
                seen.add(a)
        return sorted(seen, key=str)


# Resolve the forward references on LLMAgentDraft and GeneratedAgent now
# that AgentTraits has been declared. Without this, Pydantic raises
# "is not fully defined" when these classes are first instantiated.
LLMAgentDraft.model_rebuild()
GeneratedAgent.model_rebuild()


__all__ = [
    "AgentField",
    "AgentTraits",
    "BasisKind",
    "BuyerStateLayer",
    "CategoricalTrait",
    "EconomicLayer",
    "EmotionalJTBDLayer",
    "GeneratedAgent",
    "InfluenceEdge",
    "LLMAgentDraft",
    "LLMEdgeDraft",
    "LLMSocietyDraft",
    "OCEANLayer",
    "SocialInfluenceLayer",
    "SocietyBuildResult",
    "SocietyValidationError",
    "TraitLevel",
    "TrustProofRiskLayer",
]
