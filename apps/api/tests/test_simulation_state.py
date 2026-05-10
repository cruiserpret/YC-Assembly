"""Tests for Phase 6 inter-round state types.

Schema-only and pure-Python tests. No LLM calls."""
from __future__ import annotations

from uuid import UUID

import pytest

from assembly.pipeline.simulation.state import (
    ROUND_NUMBERS_TO_TYPE,
    BuyerStateSnapshot,
)
from assembly.schemas.round import (
    AgentRoundResponse,
    Objection,
    PersuasionDriver,
    StanceShift,
)
from assembly.schemas.society import GeneratedAgent


# ---------------------------------------------------------------------------
# Reuse the Phase 5 fixtures to build a real GeneratedAgent
# ---------------------------------------------------------------------------


@pytest.fixture
def generated_agent(evidence_ids: dict[str, UUID]):
    """Reuse the test_society_builder helper to make a valid agent
    (with the full six-layer trait block)."""
    from tests.test_society_builder import (
        _generated_from_draft,
        _make_agent_draft,
    )

    eid = evidence_ids["user_description"]
    return _generated_from_draft(_make_agent_draft(eid=eid))


def test_round_numbers_map_to_seven_types() -> None:
    assert len(ROUND_NUMBERS_TO_TYPE) == 7
    assert ROUND_NUMBERS_TO_TYPE[1] == "baseline"
    assert ROUND_NUMBERS_TO_TYPE[7] == "final_stance"


def test_initial_snapshot_uses_curious_hesitant_anchor(generated_agent: GeneratedAgent) -> None:
    snap = BuyerStateSnapshot.initial(generated_agent)
    assert str(snap.current_stance) == "curious_hesitant"
    assert snap.accumulated_objections == []
    assert snap.accumulated_persuasion_drivers == []
    assert snap.last_reasoning is None
    assert snap.shift_history == []


def test_initial_snapshot_pulls_state_from_agent(generated_agent: GeneratedAgent) -> None:
    snap = BuyerStateSnapshot.initial(generated_agent)
    s = snap.state_after
    assert s.budget == generated_agent.budget_level.value
    assert s.fear == generated_agent.fear.value
    assert s.desire == generated_agent.desire.value
    assert s.influence_score == generated_agent.influence_score


def test_updated_for_response_accumulates_objections_and_drivers(
    generated_agent: GeneratedAgent,
) -> None:
    snap = BuyerStateSnapshot.initial(generated_agent)
    response = AgentRoundResponse(
        agent_id=generated_agent.agent_id,
        stance="skeptical",  # type: ignore[arg-type]
        reasoning="My initial reaction is skeptical because brand control feels at risk.",
        objections=[
            Objection(text="Worry about brand voice dilution", severity="strong", category="brand_control"),
            Objection(text="Pricing unclear at scale", severity="moderate", category="pricing"),
        ],
        persuasion_drivers=[
            PersuasionDriver(text="Reduces freelancer overhead", strength="moderate", category="cost"),
        ],
        shift_from_previous=StanceShift(
            from_stance="curious_hesitant",  # type: ignore[arg-type]
            to_stance="skeptical",  # type: ignore[arg-type]
            reason="Brand-control fear surfaced on first read.",
            triggered_by="first_exposure_brand_control_concern",
        ),
        state_after=snap.state_after,
    )
    next_snap = snap.updated_for_response(response)
    assert str(next_snap.current_stance) == "skeptical"
    assert len(next_snap.accumulated_objections) == 2
    assert len(next_snap.accumulated_persuasion_drivers) == 1
    assert next_snap.last_reasoning is not None
    assert len(next_snap.shift_history) == 1
    # Pure: original snap unchanged.
    assert snap.accumulated_objections == []
    assert snap.shift_history == []


def test_repeated_updates_keep_growing_accumulators(
    generated_agent: GeneratedAgent,
) -> None:
    snap = BuyerStateSnapshot.initial(generated_agent)
    for i in range(3):
        r = AgentRoundResponse(
            agent_id=generated_agent.agent_id,
            stance="skeptical",  # type: ignore[arg-type]
            reasoning=f"reason {i}",
            objections=[
                Objection(text=f"obj {i}", severity="moderate", category="x"),
            ],
            persuasion_drivers=[],
            shift_from_previous=None,
            state_after=snap.state_after,
        )
        snap = snap.updated_for_response(r)
    assert len(snap.accumulated_objections) == 3
