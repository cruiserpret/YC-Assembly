"""Tests for the deterministic peer-pair sampler used by round 6."""
from __future__ import annotations

from uuid import UUID

from assembly.pipeline.simulation.peer_sampling import sample_peer_pairs
from assembly.schemas.society import GeneratedAgent, InfluenceEdge


def _agent(uid: str, segment: str, *, influence: float, susceptibility: float):
    """Quick agent builder for sampler tests — uses the Phase-5 fixture
    machinery to produce a minimum-valid GeneratedAgent."""
    from tests.test_society_builder import (
        _generated_from_draft,
        _make_agent_draft,
    )

    eid = UUID("22222222-2222-2222-2222-222222222222")
    draft = _make_agent_draft(eid=eid, influence_score=influence)
    ga = _generated_from_draft(draft)
    return ga.model_copy(update={
        "agent_id": UUID(uid),
        "segment": segment,
        "influence_score": influence,
        "susceptibility_to_peer_shift": susceptibility,
    })


def test_sampler_is_deterministic_with_seed() -> None:
    a = _agent("00000000-0000-0000-0000-000000000001", "merchant", influence=0.5, susceptibility=0.5)
    b = _agent("00000000-0000-0000-0000-000000000002", "merchant", influence=0.7, susceptibility=0.4)
    c = _agent("00000000-0000-0000-0000-000000000003", "premium", influence=0.3, susceptibility=0.6)
    edges = [
        InfluenceEdge(source_agent_id=b.agent_id, target_agent_id=a.agent_id, influence_strength=0.6),
        InfluenceEdge(source_agent_id=c.agent_id, target_agent_id=a.agent_id, influence_strength=0.5),
        InfluenceEdge(source_agent_id=a.agent_id, target_agent_id=b.agent_id, influence_strength=0.6),
        InfluenceEdge(source_agent_id=c.agent_id, target_agent_id=b.agent_id, influence_strength=0.4),
        InfluenceEdge(source_agent_id=a.agent_id, target_agent_id=c.agent_id, influence_strength=0.5),
        InfluenceEdge(source_agent_id=b.agent_id, target_agent_id=c.agent_id, influence_strength=0.4),
    ]
    pairs1 = sample_peer_pairs(agents=[a, b, c], edges=edges, seed=42, k_per_agent=1)
    pairs2 = sample_peer_pairs(agents=[a, b, c], edges=edges, seed=42, k_per_agent=1)
    assert pairs1 == pairs2  # deterministic across runs


def test_sampler_no_self_loops() -> None:
    a = _agent("00000000-0000-0000-0000-0000000000a1", "x", influence=0.5, susceptibility=0.5)
    edges = [
        # Bogus self-edge — sampler must ignore.
        InfluenceEdge(source_agent_id=a.agent_id, target_agent_id=a.agent_id, influence_strength=1.0),
    ]
    pairs = sample_peer_pairs(agents=[a], edges=edges, seed=7, k_per_agent=2)
    assert pairs == []


def test_sampler_weights_high_influence_higher() -> None:
    """Agent A has two incoming edges of equal strength, but peer B has
    `influence_score=1.0` and peer C has `influence_score=0.05`. Across many
    seeds, B should be picked more often as A's debate peer."""
    a = _agent("00000000-0000-0000-0000-0000000000aa", "x", influence=0.5, susceptibility=0.5)
    b = _agent("00000000-0000-0000-0000-0000000000bb", "x", influence=1.0, susceptibility=0.5)
    c = _agent("00000000-0000-0000-0000-0000000000cc", "x", influence=0.05, susceptibility=0.5)
    edges = [
        InfluenceEdge(source_agent_id=b.agent_id, target_agent_id=a.agent_id, influence_strength=0.5),
        InfluenceEdge(source_agent_id=c.agent_id, target_agent_id=a.agent_id, influence_strength=0.5),
    ]
    b_picks = 0
    c_picks = 0
    for seed in range(100):
        pairs = sample_peer_pairs(
            agents=[a, b, c], edges=edges, seed=seed, k_per_agent=1
        )
        for p in pairs:
            if p.subject_agent_id != a.agent_id:
                continue
            if p.peer_agent_id == b.agent_id:
                b_picks += 1
            elif p.peer_agent_id == c.agent_id:
                c_picks += 1
    # B has 20× the influence_score so should be picked far more often.
    assert b_picks > c_picks * 5, f"B picked {b_picks}, C picked {c_picks}"
