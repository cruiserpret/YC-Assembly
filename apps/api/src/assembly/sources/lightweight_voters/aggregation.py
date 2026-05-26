"""Phase 12C — aggregate the 100 voters into a 4-bucket distribution.

Pure Python. Each voter contributes its `population_weight` to its
final_bucket. Normalized to 100%.
"""
from __future__ import annotations

from assembly.sources.lightweight_voters.voter_schema import (
    LightweightVoter,
    VoterBucketDistribution,
)


def aggregate_voter_distribution(
    voters: list[LightweightVoter],
) -> VoterBucketDistribution:
    """Aggregate the 100 voters → weighted 4-bucket distribution (%)."""
    weighted: dict[str, float] = {
        "buyer": 0.0, "receptive": 0.0,
        "uncertain": 0.0, "skeptical": 0.0,
    }
    total_weight = 0.0
    for v in voters:
        if v.final_bucket is None:
            # Voters without a final_bucket are excluded from the
            # distribution but counted in total population weight
            # — they're a sign of a broken pipeline run.
            continue
        weighted[v.final_bucket] += v.population_weight
        total_weight += v.population_weight
    if total_weight <= 0:
        # Degenerate: no voters had a bucket. Return zero dist.
        return VoterBucketDistribution(
            buyer=0.0, receptive=0.0, uncertain=0.0, skeptical=0.0,
            total_population_weight=0.0, n_voters=len(voters),
        )
    return VoterBucketDistribution(
        buyer=100.0 * weighted["buyer"] / total_weight,
        receptive=100.0 * weighted["receptive"] / total_weight,
        uncertain=100.0 * weighted["uncertain"] / total_weight,
        skeptical=100.0 * weighted["skeptical"] / total_weight,
        total_population_weight=total_weight,
        n_voters=len(voters),
    )
