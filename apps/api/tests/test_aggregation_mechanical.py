"""Phase 7 — unit tests for the deterministic / mechanical aggregation
sections. No DB, no LLM. Constructs `ReportInputBundle` from in-memory
duck-typed objects so the math + structure assertions stay isolated."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from assembly.pipeline.aggregation.mechanical import (
    build_confidence_section,
    build_debate_shift_markers,
    build_evidence_ledger_section,
    collect_top_objections,
    collect_top_persuasion_drivers,
)
from assembly.pipeline.aggregation.reader import ReportInputBundle
from assembly.pipeline.evidence_graph.retriever import RankedEvidence, RetrievalResult
from assembly.pipeline.evidence_graph.service import (
    ClaimTraceability,
    EvidenceBundle,
    MissingEvidenceSummary,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _round(round_id, number, round_type="baseline"):
    return SimpleNamespace(
        id=round_id, simulation_id=uuid4(), round_number=number,
        round_type=round_type, summary={}, started_at=None, completed_at=None,
    )


def _agent_response(round_id, stance, *, shift=None, objections=None, drivers=None):
    return SimpleNamespace(
        id=uuid4(),
        round_id=round_id,
        agent_id=uuid4(),
        stance=stance,
        reasoning="agents portraying X seemed to feel Y",
        objections=objections or [],
        persuasion_drivers=drivers or [],
        shift_from_previous=shift,
        state_after={},
        raw_output={},
    )


def _bundle(*, rounds=None, agent_responses=None, debate_turns=None,
            competitor=None, pricing=None, trust=None, positioning=None,
            market=None, missing=None, claims=None):
    empty_bundle = EvidenceBundle(ranked=[], missing=[])
    empty_missing = MissingEvidenceSummary(by_node_class={}, total=0)
    return ReportInputBundle(
        simulation=SimpleNamespace(id=uuid4(), evidence_cutoff_date=None),
        brief=SimpleNamespace(),
        pio=SimpleNamespace(),
        society=[],
        edges=[],
        rounds=rounds or [],
        agent_responses=agent_responses or [],
        debate_turns=debate_turns or [],
        competitor_evidence=competitor or empty_bundle,
        pricing_evidence=pricing or empty_bundle,
        trust_barrier_evidence=trust or empty_bundle,
        positioning_evidence=positioning or empty_bundle,
        market_acceptance_evidence=market or empty_bundle,
        missing_evidence=missing or empty_missing,
        claim_traceability=claims or [],
        cutoff_date=None,
    )


# ---------------------------------------------------------------------------
# Section 8 — debate shift markers
# ---------------------------------------------------------------------------


def test_debate_shift_markers_no_shifts_returns_empty_summary() -> None:
    r1 = _round(uuid4(), 1)
    bundle = _bundle(
        rounds=[r1],
        agent_responses=[_agent_response(r1.id, "curious_hesitant")],
    )
    section = build_debate_shift_markers(bundle)
    assert section.markers == []
    assert "no" in section.summary.lower() or "0" in section.summary


def test_debate_shift_markers_groups_by_round_and_stance_pair() -> None:
    r2 = _round(uuid4(), 2, "first_exposure")
    bundle = _bundle(
        rounds=[r2],
        agent_responses=[
            _agent_response(r2.id, "skeptical", shift={
                "from_stance": "curious_hesitant", "to_stance": "skeptical",
                "triggered_by": "brand_control",
            }),
            _agent_response(r2.id, "skeptical", shift={
                "from_stance": "curious_hesitant", "to_stance": "skeptical",
                "triggered_by": "brand_control",
            }),
            _agent_response(r2.id, "mildly_interested", shift={
                "from_stance": "curious_hesitant", "to_stance": "mildly_interested",
                "triggered_by": "consolidation_value",
            }),
        ],
    )
    section = build_debate_shift_markers(bundle)
    by_pair = {(m.from_stance, m.to_stance, m.triggered_by): m.count for m in section.markers}
    assert by_pair[("curious_hesitant", "skeptical", "brand_control")] == 2
    assert by_pair[("curious_hesitant", "mildly_interested", "consolidation_value")] == 1
    assert section.rounds_with_shifts == [2]


def test_debate_shift_markers_resolves_round_6_debate_turn_id() -> None:
    r6 = _round(uuid4(), 6, "social_influence")
    turn = SimpleNamespace(
        id=uuid4(), round_id=r6.id,
        speaker_agent_id=uuid4(), target_agent_id=uuid4(),
        responding_to_turn_id=None,
        argument="The peer argued that consolidation was worth the trust risk.",
        caused_shifts=[
            {"from_stance": "skeptical", "to_stance": "curious_hesitant"}
        ],
    )
    bundle = _bundle(
        rounds=[r6], debate_turns=[turn],
        agent_responses=[
            _agent_response(r6.id, "curious_hesitant", shift={
                "from_stance": "skeptical",
                "to_stance": "curious_hesitant",
                "triggered_by": str(turn.id),
            }),
        ],
    )
    section = build_debate_shift_markers(bundle)
    assert any(m.debate_turn_id == turn.id for m in section.markers)
    speaker_match = next(m for m in section.markers if m.debate_turn_id == turn.id)
    assert speaker_match.speaker_agent_id == turn.speaker_agent_id
    assert speaker_match.example_argument
    assert speaker_match.example_argument.startswith("The peer")


# ---------------------------------------------------------------------------
# Section 9a — confidence
# ---------------------------------------------------------------------------


def test_confidence_section_narrow_when_one_stance_dominates() -> None:
    r1 = _round(uuid4(), 1)
    r7 = _round(uuid4(), 7, "final_stance")
    bundle = _bundle(
        rounds=[r1, r7],
        agent_responses=[
            _agent_response(r1.id, "curious_hesitant") for _ in range(6)
        ] + [
            _agent_response(r7.id, "skeptical") for _ in range(5)
        ] + [
            _agent_response(r7.id, "mildly_interested"),
        ],
    )
    section = build_confidence_section(bundle)
    assert section.split_confidence.largest_bucket_stance == "skeptical"
    assert section.split_confidence.largest_bucket_count == 5
    assert section.split_confidence.interpretation in ("narrow", "split")
    # Round 1 entropy < round 7 entropy NOT necessarily true; just check both populated.
    assert isinstance(section.split_confidence.entropy_round_1, float)
    assert isinstance(section.split_confidence.entropy_round_7, float)


def test_confidence_section_per_round_distribution_ordered_by_count() -> None:
    r7 = _round(uuid4(), 7)
    bundle = _bundle(
        rounds=[r7],
        agent_responses=[
            _agent_response(r7.id, "skeptical") for _ in range(3)
        ] + [
            _agent_response(r7.id, "curious_hesitant"),
        ],
    )
    section = build_confidence_section(bundle)
    last = section.stance_distribution_by_round[-1]
    assert last[0].stance == "skeptical"
    assert last[0].count == 3


# ---------------------------------------------------------------------------
# Section 9b — evidence ledger
# ---------------------------------------------------------------------------


def test_evidence_ledger_counts_and_missing_separated() -> None:
    direct_item = SimpleNamespace(
        id=uuid4(), kind="direct", node_class="competitor", content="x")
    analogical_item = SimpleNamespace(
        id=uuid4(), kind="analogical", node_class="analogical_market", content="y")
    missing_item = SimpleNamespace(
        id=uuid4(), kind="missing", node_class="pricing", content="missing pricing")
    competitor = EvidenceBundle(
        ranked=[
            RankedEvidence(item=direct_item, score=0.5),
            RankedEvidence(item=analogical_item, score=0.3),
        ],
        missing=[],
    )
    missing_summary = MissingEvidenceSummary(
        by_node_class={"pricing": [missing_item]}, total=1,
    )
    bundle = _bundle(competitor=competitor, missing=missing_summary)

    section = build_evidence_ledger_section(bundle)
    assert section.counts.direct_count == 1
    assert section.counts.analogical_count == 1
    assert section.counts.missing_count == 1
    assert len(section.missing) == 1
    assert section.missing[0].node_class == "pricing"


# ---------------------------------------------------------------------------
# Roll-ups (input data for Calls A/B/C)
# ---------------------------------------------------------------------------


def test_collect_top_objections_groups_by_text_and_counts() -> None:
    r1 = _round(uuid4(), 1)
    bundle = _bundle(
        rounds=[r1],
        agent_responses=[
            _agent_response(r1.id, "skeptical", objections=[
                {"text": "brand control", "category": "brand_control"}
            ]),
            _agent_response(r1.id, "skeptical", objections=[
                {"text": "brand control", "category": "brand_control"}
            ]),
            _agent_response(r1.id, "mildly_interested", objections=[
                {"text": "lock-in", "category": "lock_in"}
            ]),
        ],
    )
    rolled = collect_top_objections(bundle)
    by_text = {x["text"]: x["count"] for x in rolled}
    assert by_text["brand control"] == 2
    assert by_text["lock-in"] == 1


def test_collect_top_persuasion_drivers_preserves_evidence_anchors() -> None:
    r1 = _round(uuid4(), 1)
    eid = uuid4()
    bundle = _bundle(
        rounds=[r1],
        agent_responses=[
            _agent_response(r1.id, "mildly_interested", drivers=[
                {"text": "consolidation", "evidence_anchors": [str(eid)]}
            ]),
        ],
    )
    rolled = collect_top_persuasion_drivers(bundle)
    assert rolled[0]["text"] == "consolidation"
    assert str(eid) in rolled[0]["evidence_anchors"]
