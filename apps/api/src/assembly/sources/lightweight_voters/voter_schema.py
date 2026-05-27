"""Phase 12C — Pydantic models for the 100-voter synthetic market graph.

Voters are NOT silent survey rows. Each voter exists as a graph node,
forms an initial intent via the rule cascade, receives + gives
influence on a typed social graph, optionally updates intent under
bounded movement, and lands in one of the 4 calibration buckets.

CRITICAL invariants (enforced elsewhere; documented here):
  - Zero LLM calls in the lightweight_voters package
  - Voters are DETERMINISTIC given (cohorts, simulation_seed)
  - Movement is bounded ±1 step on a fixed INTENT_ORDER
  - 100-voter distribution does NOT replace 24-rich raw distribution
  - calibrated_distribution NEVER promotes a prediction to 'validated'
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# Ordered most-skeptical → most-buyer. Movement in Round 2 is bounded
# to ±1 step on this scale. Kept here (not in intent_layer) because
# the voter-influence semantics are 12C-specific; the intent_layer
# cascade vocabulary remains the source of truth for intent labels.
INTENT_ORDER: tuple[str, ...] = (
    "would_reject",
    "loyal_to_current_alternative",
    "wait_and_see",
    "would_consider_if_proven",
    "would_share_with_friend",
    "would_compare_to_current_brand",
    "would_join_waitlist",
    "would_try_once",
    "would_buy_now",
)

# Phase 12C.1 — intents that map to the `skeptical` bucket.
# Voters with these initial_intents are `hard_resistant` and have
# movement constraints applied in the influence loop.
HARD_RESISTANT_INTENTS: frozenset[str] = frozenset({
    "would_reject",
    "would_block",
    "loyal_to_current_alternative",
})

# Roles that imply hard resistance independent of intent. A voter with
# one of these roles is hard_resistant even if the cascade assigned a
# soft intent — they need explicit proof satisfaction to move.
HARD_RESISTANT_ROLE_PATTERNS: tuple[str, ...] = (
    "competitor_builder",
    "anti_ai_skeptic",
    "anti_automation_skeptic",
    "privacy_security_blocked",
    "workflow_blocked",
)

MarketBucket = Literal["buyer", "receptive", "uncertain", "skeptical"]
VoteConfidence = Literal["high", "medium", "low"]

EdgeType = Literal[
    "segment_similarity",
    "role_similarity",
    "current_alt_similarity",
    "trust",
    "influencer",
    "skeptic_influence",
    "early_adopter_influence",
    "cross_segment_exposure",
]

RoundType = Literal["init", "receive", "update", "finalize"]


class InfluenceSignal(BaseModel):
    """A single incoming or outgoing influence record. Kept compact
    so the per-voter audit log doesn't explode."""

    model_config = ConfigDict(extra="forbid")

    peer_voter_id: str          # str-serialized UUID for JSON
    edge_type: EdgeType
    edge_weight: float = Field(ge=0.0, le=1.0)
    peer_intent: str | None = None
    peer_segment: str | None = None


class LightweightVoter(BaseModel):
    """One of the 100 voters in the synthetic market graph.

    Mutable across rounds (Pydantic v2 default). Fields populated in
    waves: identity + psy + objection at sampling, initial_intent at
    Round 0, influence_received at Round 1, final_intent + bucket at
    Round 2/3."""

    model_config = ConfigDict(extra="forbid")

    # ---- Identity / provenance ----
    voter_id: UUID = Field(default_factory=uuid4)
    run_scope_id: str
    linked_rich_persona_id: UUID | None = None
    cohort_id: UUID
    sampling_seed: str

    # ---- Centroid-derived identity ----
    segment: str                                 # cohort_label
    role: str                                    # sampled from cohort.role_distribution
    current_alternative: str | None = None
    population_weight: float = Field(ge=0.0, le=10.0, default=1.0)

    # ---- Psychology (sampled around centroid + bounded jitter) ----
    trust_threshold: float = Field(ge=0.0, le=1.0)
    novelty_seeking: float = Field(ge=0.0, le=1.0)
    price_sensitivity: float = Field(ge=0.0, le=1.0)
    category_expertise: float = Field(ge=0.0, le=1.0)
    social_influence_weight: float = Field(ge=0.0, le=1.0)
    switching_resistance: float = Field(ge=0.0, le=1.0)

    # ---- Centroid-sampled context ----
    primary_objection: str | None = None
    proof_need: str | None = None

    # ---- Intent state (filled across rounds 0-3) ----
    initial_intent: str = ""
    initial_bucket: MarketBucket | None = None
    final_intent: str = ""
    final_bucket: MarketBucket | None = None
    vote_confidence: VoteConfidence = "low"

    # Phase 12C.1 — hard-resistant classification.
    # `hard_resistant` voters require explicit proof satisfaction to
    # cross from `skeptical` to a non-skeptical bucket in one round.
    hard_resistant: bool = False
    hard_resistant_reason: str | None = None

    # ---- Influence audit trail ----
    influence_received: list[InfluenceSignal] = Field(default_factory=list)
    influence_given: list[InfluenceSignal] = Field(default_factory=list)

    # ---- Audit ----
    evidence_basis: str = ""
    generated_for_phase: str = "12c"


class SocialEdge(BaseModel):
    """One directed edge in the social graph. Symmetric edges are
    represented as two SocialEdge rows."""

    model_config = ConfigDict(extra="forbid")

    edge_id: UUID = Field(default_factory=uuid4)
    source_voter_id: UUID
    target_voter_id: UUID
    edge_type: EdgeType
    weight: float = Field(ge=0.0, le=1.0)
    evidence_basis: str = ""


class InfluenceRound(BaseModel):
    """Audit record for one round of the 4-round influence loop."""

    model_config = ConfigDict(extra="forbid")

    round_idx: int = Field(ge=0, le=3)
    round_type: RoundType
    voters_affected: int = 0
    intent_changes: int = 0
    bucket_changes: int = 0
    per_voter_log: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None
    # Phase 12C.1 — per-round bucket snapshot + skeptical-bucket
    # movement breakdown. Rounds 0 and 1 produce no movement; rounds
    # 2 and 3 record where skeptics ended up at the END of that round.
    bucket_distribution: dict[str, int] = Field(default_factory=dict)
    skeptic_transitions: dict[str, int] = Field(default_factory=dict)


class VoterBucketDistribution(BaseModel):
    """The aggregate over 100 voters → 4-bucket distribution, in %."""

    model_config = ConfigDict(extra="forbid")

    buyer: float = Field(ge=0.0, le=100.0)
    receptive: float = Field(ge=0.0, le=100.0)
    uncertain: float = Field(ge=0.0, le=100.0)
    skeptical: float = Field(ge=0.0, le=100.0)
    total_population_weight: float
    n_voters: int


class CalibratedDistribution(BaseModel):
    """Conservative blend of the 24-rich + 100-voter distributions.

    CRITICAL semantic note: a `CalibratedDistribution` does NOT make
    a prediction 'validated.' Calibration-status (the
    unvalidated/validated_promising/validated_strong enum) lives on
    the run row separately. This object only describes how we blend
    the two input distributions and how wide our confidence band is."""

    model_config = ConfigDict(extra="forbid")

    distribution_percent: dict[MarketBucket, float]
    confidence_band_pp: float
    used_prior_correction: bool = False
    blend_weights: dict[Literal["rich_24", "voter_100"], float]
    calibration_warnings: list[str] = Field(default_factory=list)


class DiversityHealth(BaseModel):
    """Per-run dashboard. Gates against the locked thresholds (see
    diversity_health.py for the threshold logic). `warnings` lists
    every gate that failed; the operator inspects them post-run."""

    model_config = ConfigDict(extra="forbid")

    # Voter-population structure
    n_voters: int
    n_cohorts_represented: int
    n_segments_represented: int
    n_roles_represented: int
    max_role_concentration: float
    competitor_user_share: float

    # Graph structure
    n_edges: int
    avg_edges_per_voter: float
    edges_per_voter_min: int
    edges_per_voter_max: int
    edge_type_distribution: dict[str, int]

    # Intent dynamics
    intent_diversity_per_round: dict[int, int]
    intent_changes_count: int
    bucket_changes_count: int

    # Phase 12C.1 — transition audit + resistance realism
    initial_intent_distribution: dict[str, int] = Field(default_factory=dict)
    final_intent_distribution: dict[str, int] = Field(default_factory=dict)
    initial_bucket_distribution: dict[str, int] = Field(default_factory=dict)
    final_bucket_distribution: dict[str, int] = Field(default_factory=dict)
    transition_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    hard_resistant_count: int = 0
    skeptic_retention_rate: float | None = None
    hard_reject_retention_rate: float | None = None
    competitor_loyal_retention_rate: float | None = None
    skeptic_to_uncertain_rate: float | None = None
    skeptic_to_receptive_rate: float | None = None
    skeptic_to_buyer_rate: float | None = None
    # Phase 12C.1 (extended) — softening guard metrics
    hard_resistant_to_uncertain_rate: float | None = None
    hard_resistant_to_receptive_rate: float | None = None
    hard_resistant_to_buyer_rate: float | None = None
    hard_resistant_retention_rate: float | None = None
    per_round_bucket_distribution: dict[int, dict[str, int]] = Field(
        default_factory=dict,
    )
    per_round_skeptic_transitions: dict[int, dict[str, int]] = Field(
        default_factory=dict,
    )
    # Phase 12C.1 (Option A) — bucket-level vs exact-intent
    # diagnostic split. Bucket-level metrics are the load-bearing
    # realism gates; exact-intent metrics are advisory (surface
    # within-skeptical micro-shifts without gating on them).
    hard_reject_bucket_retention_rate: float | None = None
    hard_reject_exact_intent_retention_rate: float | None = None
    hard_resistant_bucket_retention_rate: float | None = None
    hard_resistant_exact_intent_retention_rate: float | None = None
    within_skeptical_intent_shift_count: int = 0
    within_skeptical_intent_shift_examples: list[dict[str, str]] = Field(
        default_factory=list,
    )

    # Voter uniqueness
    voter_id_uniqueness_pct: float
    segment_role_pair_distinct_count: int

    # Inherited rich-persona quality (read from existing artifacts)
    persona_voice_diversity_score: float | None = None
    repeated_objection_count: int | None = None
    near_duplicate_turn_count: int | None = None
    ballots_with_unique_reasoning_pct: float | None = None

    # Gate output
    warnings: list[str] = Field(default_factory=list)
    all_gates_passed: bool
