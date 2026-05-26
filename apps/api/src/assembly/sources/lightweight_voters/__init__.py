"""Phase 12C — 100-person synthetic market graph overlay.

A read-only overlay on top of the existing 24-rich-persona simulation.
Voters are sampled from SocietyCohort centroids, connected via a
typed social graph, and run through a 4-round influence loop. Zero
LLM calls.
"""
from __future__ import annotations

from assembly.sources.lightweight_voters.aggregation import (
    aggregate_voter_distribution,
)
from assembly.sources.lightweight_voters.calibration_correction import (
    calibrated_distribution,
)
from assembly.sources.lightweight_voters.diversity_health import (
    compute_diversity_health,
)
from assembly.sources.lightweight_voters.influence_loop import (
    run_influence_rounds,
)
from assembly.sources.lightweight_voters.social_graph import (
    build_social_graph,
)
from assembly.sources.lightweight_voters.voter_sampling import (
    allocate_voters_per_cohort,
    generate_voters_from_cohorts,
)
from assembly.sources.lightweight_voters.voter_schema import (
    HARD_RESISTANT_INTENTS,
    HARD_RESISTANT_ROLE_PATTERNS,
    INTENT_ORDER,
    CalibratedDistribution,
    DiversityHealth,
    EdgeType,
    InfluenceRound,
    InfluenceSignal,
    LightweightVoter,
    MarketBucket,
    RoundType,
    SocialEdge,
    VoteConfidence,
    VoterBucketDistribution,
)

__all__ = [
    # schema
    "HARD_RESISTANT_INTENTS",
    "HARD_RESISTANT_ROLE_PATTERNS",
    "INTENT_ORDER",
    "CalibratedDistribution",
    "DiversityHealth",
    "EdgeType",
    "InfluenceRound",
    "InfluenceSignal",
    "LightweightVoter",
    "MarketBucket",
    "RoundType",
    "SocialEdge",
    "VoteConfidence",
    "VoterBucketDistribution",
    # sampling
    "allocate_voters_per_cohort",
    "generate_voters_from_cohorts",
    # graph
    "build_social_graph",
    # influence
    "run_influence_rounds",
    # aggregation + calibration + diversity
    "aggregate_voter_distribution",
    "calibrated_distribution",
    "compute_diversity_health",
]
