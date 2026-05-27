"""Phase 12C — typed social graph over the 100 lightweight voters.

3-8 edges per voter. Deterministic given (voters, simulation_seed).
Edge types capture which similarity dimension drives the connection.

Goals:
  - within-segment edges dominate (60-80%)
  - some cross-segment exposure (~10-15%)
  - high-social-influence voters get extra out-edges
  - high-trust-threshold voters get skeptic_influence edges (dampening)
  - low-trust + high-novelty voters get early_adopter_influence edges
"""
from __future__ import annotations

import hashlib
import random

from assembly.sources.lightweight_voters.voter_schema import (
    LightweightVoter,
    SocialEdge,
)


def _stable_seed(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def build_social_graph(
    voters: list[LightweightVoter],
    *,
    simulation_seed: int,
    min_edges_per_voter: int = 3,
    max_edges_per_voter: int = 8,
) -> tuple[list[SocialEdge], list[str]]:
    """Build a typed social graph over the voter population.

    Returns (edges, warnings).

    Per-voter edge count target: [min_edges_per_voter, max_edges_per_voter].
    Within those bounds, edges are sampled along multiple similarity
    dimensions (segment, role, current_alternative, etc.).
    """
    warnings: list[str] = []
    if not voters:
        return [], ["empty_voter_population"]

    rng_root = random.Random(_stable_seed(str(simulation_seed), "graph"))

    edges: list[SocialEdge] = []
    out_edges_count: dict[str, int] = {
        str(v.voter_id): 0 for v in voters
    }

    def _add_edge(
        src: LightweightVoter,
        tgt: LightweightVoter,
        edge_type: str,
        weight: float,
        reason: str,
    ) -> None:
        if str(src.voter_id) == str(tgt.voter_id):
            return  # no self-loops
        edges.append(SocialEdge(
            source_voter_id=src.voter_id,
            target_voter_id=tgt.voter_id,
            edge_type=edge_type,
            weight=max(0.0, min(1.0, weight)),
            evidence_basis=reason,
        ))
        out_edges_count[str(src.voter_id)] = (
            out_edges_count.get(str(src.voter_id), 0) + 1
        )

    # Indexes for cheap lookup
    by_segment: dict[str, list[LightweightVoter]] = {}
    by_role: dict[str, list[LightweightVoter]] = {}
    by_alt: dict[str, list[LightweightVoter]] = {}
    for v in voters:
        by_segment.setdefault(v.segment, []).append(v)
        by_role.setdefault(v.role, []).append(v)
        if v.current_alternative:
            by_alt.setdefault(v.current_alternative, []).append(v)

    for v in voters:
        rng = random.Random(_stable_seed(
            str(simulation_seed), "graph", str(v.voter_id),
        ))

        # 1. SAME-SEGMENT EDGES (target 2-4)
        same_seg = [u for u in by_segment.get(v.segment, []) if u.voter_id != v.voter_id]
        rng.shuffle(same_seg)
        k_same = min(4, max(2, len(same_seg) // 3))
        for u in same_seg[:k_same]:
            # Higher weight when psy is similar (close trust thresholds)
            psy_dist = abs(v.trust_threshold - u.trust_threshold)
            weight = 0.5 + 0.3 * (1.0 - psy_dist)
            _add_edge(v, u, "segment_similarity", weight,
                      f"shared_segment={v.segment}")

        # 2. ROLE-SIMILARITY (cross-segment) — 0-1
        cross_role = [
            u for u in by_role.get(v.role, [])
            if u.voter_id != v.voter_id and u.segment != v.segment
        ]
        if cross_role:
            u = rng.choice(cross_role)
            _add_edge(v, u, "role_similarity", 0.4,
                      f"shared_role={v.role}")

        # 3. CURRENT-ALTERNATIVE SIMILARITY (0-2)
        if v.current_alternative:
            same_alt = [
                u for u in by_alt.get(v.current_alternative, [])
                if u.voter_id != v.voter_id
            ]
            rng.shuffle(same_alt)
            for u in same_alt[:2]:
                _add_edge(v, u, "current_alt_similarity", 0.5,
                          f"shared_current_alt="
                          f"{v.current_alternative}")

        # 4. INFLUENCER (from high-social-influence voters; cross-segment)
        if v.social_influence_weight > 0.7:
            cross_seg = [
                u for u in voters
                if u.voter_id != v.voter_id and u.segment != v.segment
            ]
            if cross_seg:
                u = rng.choice(cross_seg)
                _add_edge(v, u, "influencer", 0.4,
                          f"social_influence={v.social_influence_weight:.2f}")

        # 5. SKEPTIC INFLUENCE (high trust_threshold → dampens peers)
        if v.trust_threshold > 0.7:
            peers = [u for u in by_segment.get(v.segment, []) if u.voter_id != v.voter_id]
            if peers:
                u = rng.choice(peers)
                _add_edge(v, u, "skeptic_influence", 0.3,
                          f"trust_threshold={v.trust_threshold:.2f}")

        # 6. EARLY ADOPTER (low trust + high novelty → pulls peers receptive)
        if v.trust_threshold < 0.4 and v.novelty_seeking > 0.6:
            peers = [u for u in by_segment.get(v.segment, []) if u.voter_id != v.voter_id]
            if peers:
                u = rng.choice(peers)
                _add_edge(v, u, "early_adopter_influence", 0.35,
                          f"trust={v.trust_threshold:.2f}_novelty={v.novelty_seeking:.2f}")

        # 7. CROSS-SEGMENT EXPOSURE (random ~10% chance)
        if rng.random() < 0.10:
            cross_seg = [
                u for u in voters
                if u.voter_id != v.voter_id and u.segment != v.segment
            ]
            if cross_seg:
                u = rng.choice(cross_seg)
                _add_edge(v, u, "cross_segment_exposure", 0.2,
                          "random_cross_segment_exposure")

    # Enforce per-voter min/max bounds.
    # Trim voters with too many out-edges (drop the lowest-weight ones).
    if max_edges_per_voter:
        per_voter_edges: dict[str, list[SocialEdge]] = {}
        for e in edges:
            per_voter_edges.setdefault(
                str(e.source_voter_id), []
            ).append(e)
        kept: list[SocialEdge] = []
        for src_id, src_edges in per_voter_edges.items():
            if len(src_edges) > max_edges_per_voter:
                src_edges.sort(key=lambda e: e.weight, reverse=True)
                kept.extend(src_edges[:max_edges_per_voter])
                # rebuild out_edges_count
                out_edges_count[src_id] = max_edges_per_voter
            else:
                kept.extend(src_edges)
        edges = kept

    # Boost voters below min by adding more same-segment edges
    for v in voters:
        cur = out_edges_count.get(str(v.voter_id), 0)
        if cur >= min_edges_per_voter:
            continue
        rng = random.Random(_stable_seed(
            str(simulation_seed), "graph_boost", str(v.voter_id),
        ))
        candidates = [
            u for u in by_segment.get(v.segment, [])
            if u.voter_id != v.voter_id
            and not any(
                e.source_voter_id == v.voter_id
                and e.target_voter_id == u.voter_id
                for e in edges
            )
        ]
        rng.shuffle(candidates)
        need = min_edges_per_voter - cur
        for u in candidates[:need]:
            edges.append(SocialEdge(
                source_voter_id=v.voter_id,
                target_voter_id=u.voter_id,
                edge_type="segment_similarity",
                weight=0.4,
                evidence_basis=f"min_edge_boost_segment={v.segment}",
            ))
            out_edges_count[str(v.voter_id)] = cur + 1
            cur += 1
            if cur >= min_edges_per_voter:
                break
        if out_edges_count.get(str(v.voter_id), 0) < min_edges_per_voter:
            warnings.append(
                f"voter_below_min_edges:{v.voter_id}="
                f"{out_edges_count.get(str(v.voter_id), 0)}"
            )

    # Populate the influence_given on each voter (audit trail).
    by_vid = {str(v.voter_id): v for v in voters}
    for e in edges:
        src = by_vid.get(str(e.source_voter_id))
        if src is None:
            continue
        from assembly.sources.lightweight_voters.voter_schema import (
            InfluenceSignal,
        )
        src.influence_given.append(InfluenceSignal(
            peer_voter_id=str(e.target_voter_id),
            edge_type=e.edge_type,
            edge_weight=e.weight,
        ))

    return edges, warnings
