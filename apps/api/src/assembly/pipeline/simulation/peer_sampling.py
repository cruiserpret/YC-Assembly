"""Phase 6 — Weighted peer-pair selection for the social_influence round.

Deterministic given a seed: same society + same edges + same seed →
same pair set. Lets tests assert reproducibility.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from assembly.schemas.society import GeneratedAgent, InfluenceEdge


@dataclass(frozen=True)
class PeerPair:
    """One (subject, peer) pair for the social_influence round. The
    subject is the agent whose stance might shift; the peer is the
    influencer."""

    subject_agent_id: UUID
    peer_agent_id: UUID
    influence_strength: float
    cluster_label: str | None


def sample_peer_pairs(
    *,
    agents: list[GeneratedAgent],
    edges: list[InfluenceEdge],
    seed: int,
    k_per_agent: int = 2,
) -> list[PeerPair]:
    """For each agent, pick `k_per_agent` peers from incoming edges weighted
    by `influence_strength × peer.influence_score × (1 + subject.susceptibility_to_peer_shift)`.

    Returns a flat list of pairs. No self-loops, no duplicate (subject, peer)
    pairs (within the same agent's k picks).
    """
    by_id: dict[UUID, GeneratedAgent] = {a.agent_id: a for a in agents}
    rng = random.Random(seed)

    # Build incoming-edge index: for each subject, a list of (peer_agent, base_weight)
    incoming: dict[UUID, list[tuple[UUID, float, str | None]]] = {
        a.agent_id: [] for a in agents
    }
    for e in edges:
        if e.source_agent_id == e.target_agent_id:
            continue  # defensive
        # In our convention: edge `source -> target` means source influences
        # target. So for a subject = `target`, the peer is `source`.
        if e.target_agent_id in incoming:
            incoming[e.target_agent_id].append(
                (e.source_agent_id, e.influence_strength, e.cluster_label)
            )

    pairs: list[PeerPair] = []
    for subject in agents:
        candidates = incoming.get(subject.agent_id, [])
        if not candidates:
            continue
        weights = []
        for peer_id, edge_strength, _cluster in candidates:
            peer = by_id.get(peer_id)
            if peer is None:
                weights.append(0.0)
                continue
            weight = (
                edge_strength
                * peer.influence_score
                * (1.0 + subject.susceptibility_to_peer_shift)
            )
            weights.append(weight)

        if sum(weights) <= 0.0:
            continue

        # Weighted sampling without replacement of up to k peers.
        picks: list[tuple[UUID, float, str | None]] = []
        pool = list(zip(candidates, weights, strict=True))
        for _ in range(min(k_per_agent, len(pool))):
            total = sum(w for _, w in pool)
            if total <= 0.0:
                break
            r = rng.random() * total
            acc = 0.0
            chosen_idx = 0
            for idx, (_, w) in enumerate(pool):
                acc += w
                if r <= acc:
                    chosen_idx = idx
                    break
            (chosen_cand, _w) = pool.pop(chosen_idx)
            picks.append(chosen_cand)

        for peer_id, edge_strength, cluster in picks:
            pairs.append(
                PeerPair(
                    subject_agent_id=subject.agent_id,
                    peer_agent_id=peer_id,
                    influence_strength=edge_strength,
                    cluster_label=cluster,
                )
            )

    return pairs


__all__ = ["PeerPair", "sample_peer_pairs"]
