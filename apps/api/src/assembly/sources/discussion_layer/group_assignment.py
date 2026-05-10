"""Phase 9A.4 — stratified group assignment for the discussion layer.

Splits N personas into K groups of M = N // K each, balancing across
six dimensions:

  1. normalized_primary_role
  2. prior 9A.2 final stance
  3. extraversion (high/low)
  4. agreeableness (high/low)
  5. social_influence_susceptibility (high/low)
  6. trust_proof_threshold (high/low)
  7. source provider family

Universal — no LumaLoop hardcoding. Deterministic with a seed so the
audit can be reproduced.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any


def _seeded_order(items: list[Any], seed: str) -> list[Any]:
    """Stable shuffle keyed off a string seed."""
    return sorted(
        items,
        key=lambda x: hashlib.sha256(
            f"{seed}|{x.get('persona_id', x)}".encode("utf-8"),
        ).hexdigest(),
    )


def _bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.4:
        return "low"
    if value > 0.6:
        return "high"
    return "medium"


def assign_groups_stratified(
    *,
    personas: list[dict[str, Any]],
    group_count: int,
    group_size: int | None = None,
    seed: str = "9A.4",
) -> list[list[str]]:
    """Return a list of groups; each group is a list of persona_id strings.

    Required dict keys per persona:
      persona_id, normalized_primary_role, prior_simulation_final_stance,
      extraversion, agreeableness, social_influence_susceptibility,
      trust_proof_threshold, source_provider_family.

    Algorithm:
      1. Sort personas into a deterministic order (sha256(seed | id)).
      2. Initialize K empty buckets.
      3. For each persona in order, place it into the bucket whose
         current "diversity gap" is largest — i.e. the bucket that most
         needs this persona's profile vector. Diversity gap is the
         negative count of (role, stance, ext_bucket, agr_bucket,
         sis_bucket, tpt_bucket, provider) attributes already in the
         bucket; the bucket with the lowest sum of overlaps wins ties.

    The result is balanced (equal sizes ± 1) and stratified.
    """
    if group_count < 1:
        raise ValueError("group_count must be >= 1")
    n = len(personas)
    if n == 0:
        return [[] for _ in range(group_count)]
    target_size = group_size or (n // group_count)
    if target_size * group_count > n:
        target_size = n // group_count

    ordered = _seeded_order(personas, seed)
    groups: list[list[dict[str, Any]]] = [[] for _ in range(group_count)]

    def overlap_score(group: list[dict[str, Any]], cand: dict[str, Any]) -> int:
        if not group:
            return 0
        score = 0
        roles = Counter(g.get("normalized_primary_role") for g in group)
        score += roles.get(cand.get("normalized_primary_role"), 0) * 5
        stances = Counter(
            g.get("prior_simulation_final_stance") for g in group
        )
        score += stances.get(cand.get("prior_simulation_final_stance"), 0) * 3
        for trait in (
            "extraversion", "agreeableness",
            "social_influence_susceptibility", "trust_proof_threshold",
        ):
            buckets = Counter(_bucket(g.get(trait)) for g in group)
            score += buckets.get(_bucket(cand.get(trait)), 0) * 2
        providers = Counter(
            g.get("source_provider_family") for g in group
        )
        score += providers.get(cand.get("source_provider_family"), 0) * 2
        return score

    for cand in ordered:
        # capacity-respecting + minimum-overlap placement
        candidate_groups = [
            (i, g) for i, g in enumerate(groups) if len(g) < target_size
        ]
        if not candidate_groups:
            # everyone over capacity — overflow into smallest group
            candidate_groups = [
                (i, g) for i, g in enumerate(groups)
            ]
        target = min(
            candidate_groups,
            key=lambda ig: (
                overlap_score(ig[1], cand),
                len(ig[1]),
                ig[0],
            ),
        )
        groups[target[0]].append(cand)

    return [
        [p["persona_id"] for p in g] for g in groups
    ]


def diversity_audit(
    groups: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compute a diversity report across the assigned groups."""
    out: dict[str, Any] = {
        "group_count": len(groups),
        "group_sizes": [len(g) for g in groups],
        "per_group_summary": [],
    }
    for i, g in enumerate(groups):
        summary = {
            "group_index": i,
            "size": len(g),
            "roles": dict(Counter(p.get("normalized_primary_role") for p in g)),
            "prior_stances": dict(
                Counter(p.get("prior_simulation_final_stance") for p in g)
            ),
            "extraversion_buckets": dict(
                Counter(_bucket(p.get("extraversion")) for p in g)
            ),
            "agreeableness_buckets": dict(
                Counter(_bucket(p.get("agreeableness")) for p in g)
            ),
            "social_influence_susceptibility_buckets": dict(
                Counter(
                    _bucket(p.get("social_influence_susceptibility"))
                    for p in g
                )
            ),
            "trust_proof_threshold_buckets": dict(
                Counter(
                    _bucket(p.get("trust_proof_threshold")) for p in g
                )
            ),
            "providers": dict(
                Counter(p.get("source_provider_family") for p in g)
            ),
        }
        out["per_group_summary"].append(summary)
    return out
