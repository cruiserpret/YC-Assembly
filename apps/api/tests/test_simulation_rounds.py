"""Tests for individual Phase 6 round modules.

Each test patches `with_cost_guard` to a pass-through, runs ONE round
end-to-end with a MockProvider, and asserts the per-round contract.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from assembly.llm.mock import MockProvider
from assembly.pipeline.simulation import call_llm as call_llm_mod
from assembly.pipeline.simulation.rounds import (
    baseline,
    competitor_comparison,
    final_stance,
    first_exposure,
    objection_formation,
    proof_exposure,
    social_influence,
)
from assembly.pipeline.simulation.state import BuyerStateSnapshot, RoundContext
from assembly.schemas.round import AgentRoundResponse, DebateTurnOut


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_cost_guard(monkeypatch: pytest.MonkeyPatch):
    """Phase 6.6: bypass `cost_guarded_chat` (no row lock, no cap, no log
    write). Tests that don't have a real Postgres simulation row use this
    so they can drive the full pipeline against `MockProvider` alone."""
    from assembly.llm import guarded_chat as guarded_chat_mod
    from assembly.llm.provider import LLMCallContext

    async def fake_cost_guarded_chat(
        *,
        sessionmaker,
        simulation_id,
        stage,
        messages,
        provider,
        model=None,
        hard_cap_usd=None,
        max_tokens=2048,
        temperature=0.4,
        capture_prompt_snapshot=True,
        estimated_prompt_tokens=4000,
        estimated_completion_tokens=1000,
    ):
        ctx = LLMCallContext(
            stage=stage,
            model=model or "test-model",
            simulation_id=simulation_id,
            max_tokens=max_tokens,
            temperature=temperature,
            capture_prompt_snapshot=capture_prompt_snapshot,
        )
        return await provider.chat(list(messages), ctx)

    # Patch in BOTH places: the canonical definition + every module that
    # imported the symbol by name (call_llm rebinds it at module level).
    monkeypatch.setattr(
        guarded_chat_mod, "cost_guarded_chat", fake_cost_guarded_chat
    )
    monkeypatch.setattr(
        call_llm_mod, "cost_guarded_chat", fake_cost_guarded_chat
    )


@pytest.fixture
def small_society(evidence_ids: dict[str, UUID]):
    """Build a 4-agent society with traits — reuse Phase 5 fixture machinery."""
    from tests.test_society_builder import (
        _generated_from_draft,
        _make_agent_draft,
    )

    eid = evidence_ids["user_description"]
    return [_generated_from_draft(_make_agent_draft(eid=eid)) for _ in range(4)]


@pytest.fixture
def round_ctx(
    basic_brief,
    valid_pio,
    evidence_build_result,
    small_society,
):
    """Build a RoundContext for round 1 (baseline). Tests that need other
    rounds override `round_number`/`round_type` and `snapshots` directly."""
    return RoundContext(
        simulation_id=uuid4(),
        round_number=1,
        round_type="baseline",
        society=list(small_society),
        edges=[],
        pio=valid_pio,
        evidence=list(evidence_build_result.items),
        brief=basic_brief,
        snapshots={},
        seed=42,
    )


def _make_agent_response_json(
    agent_id: UUID,
    *,
    stance: str = "curious_hesitant",
    state_after: dict | None = None,
    objections: list | None = None,
    persuasion_drivers: list | None = None,
    shift_from_previous: dict | None = None,
) -> str:
    """Helper: build a valid AgentRoundResponse JSON for the mock provider
    to return. Uses subjective language so the validator passes."""
    state = state_after or {
        "current_alternatives": ["Shopify apps", "freelancers"],
        "budget": "$10k-$80k MRR",
        "trust_threshold": "needs proof of brand control",
        "switching_trigger": "live merchant case studies",
        "fear": "losing brand identity",
        "desire": "fewer plugins, more brand control",
        "influence_score": 0.45,
        "price_sensitivity": "moderate; mid-tier acceptable",
        "current_behavior": "operates Shopify store with apps and freelancers",
        "objection_pattern": "AI sounds unproven for brand-sensitive ops",
        "emotional_state": "overwhelmed and cautious",
    }
    return json.dumps({
        "agent_id": str(agent_id),
        "stance": stance,
        "reasoning": (
            "The agent appeared cautious but engaged. The product framing "
            "seemed to address part of the buyer's pain, though brand-control "
            "questions seemed to surface."
        ),
        "objections": objections or [],
        "persuasion_drivers": persuasion_drivers or [],
        "shift_from_previous": shift_from_previous,
        "state_after": state,
    })


# ---------------------------------------------------------------------------
# Round 1 — baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_round_runs_and_writes_per_agent_response(
    patched_cost_guard, round_ctx
) -> None:
    p = MockProvider()
    # MockProvider needs N responses (one per agent). Add a default.
    p.add_default(
        _make_agent_response_json(
            agent_id=round_ctx.society[0].agent_id, stance="curious_hesitant"
        )
    )

    result = await baseline.run_round(
        round_ctx, provider=p, sessionmaker=None
    )

    assert result.round_number == 1
    assert result.round_type == "baseline"
    assert len(result.agent_responses) == len(round_ctx.society)
    assert result.debate_turns == []
    # Every baseline stance is curious_hesitant (the helper enforces this).
    assert all(str(r.stance) == "curious_hesitant" for r in result.agent_responses)
    # No shifts in baseline.
    assert all(r.shift_from_previous is None for r in result.agent_responses)
    # Snapshots populated for every agent.
    assert set(result.new_snapshots.keys()) == {a.agent_id for a in round_ctx.society}


@pytest.mark.asyncio
async def test_baseline_round_normalizes_stance_to_curious_hesitant(
    patched_cost_guard, round_ctx
) -> None:
    """Even if the LLM returns a non-curious_hesitant stance for baseline,
    the round normalizes it. (Defensive — protects against prompt drift.)"""
    p = MockProvider()
    # MockProvider returns a stance that the round MUST overwrite.
    p.add_default(
        _make_agent_response_json(
            agent_id=round_ctx.society[0].agent_id, stance="skeptical"
        )
    )
    result = await baseline.run_round(
        round_ctx, provider=p, sessionmaker=None
    )
    assert all(str(r.stance) == "curious_hesitant" for r in result.agent_responses)


# ---------------------------------------------------------------------------
# Round 2 — first_exposure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_exposure_round_records_shifts(
    patched_cost_guard, round_ctx
) -> None:
    """Round 2 starts from baseline snapshots. If the LLM emits a
    non-curious_hesitant stance + shift, the round records it."""
    # Build round-1 snapshots first (would normally come from baseline).
    snapshots: dict[UUID, BuyerStateSnapshot] = {
        a.agent_id: BuyerStateSnapshot.initial(a) for a in round_ctx.society
    }
    ctx2 = RoundContext(
        simulation_id=round_ctx.simulation_id,
        round_number=2,
        round_type="first_exposure",
        society=round_ctx.society,
        edges=round_ctx.edges,
        pio=round_ctx.pio,
        evidence=round_ctx.evidence,
        brief=round_ctx.brief,
        snapshots=snapshots,
        seed=round_ctx.seed,
    )

    p = MockProvider()
    a0 = round_ctx.society[0].agent_id
    p.add_default(
        _make_agent_response_json(
            agent_id=a0,
            stance="skeptical",
            shift_from_previous={
                "from_stance": "curious_hesitant",
                "to_stance": "skeptical",
                "reason": "Brand-control fear surfaced on first read.",
                "triggered_by": "first_exposure_brand_control_concern",
            },
        )
    )

    result = await first_exposure.run_round(
        ctx2, provider=p, sessionmaker=None
    )
    assert len(result.agent_responses) == len(ctx2.society)
    shifted = [r for r in result.agent_responses if r.shift_from_previous is not None]
    assert len(shifted) >= 1


# ---------------------------------------------------------------------------
# Round 3-5, 7 — generic per-agent rounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module,round_type,round_number",
    [
        (objection_formation, "objection_formation", 3),
        (competitor_comparison, "competitor_comparison", 4),
        (proof_exposure, "proof_exposure", 5),
        (final_stance, "final_stance", 7),
    ],
)
@pytest.mark.asyncio
async def test_generic_per_agent_rounds_run_cleanly(
    patched_cost_guard, round_ctx, module, round_type, round_number
) -> None:
    """Each generic round produces N agent responses + 0 debate turns."""
    snapshots: dict[UUID, BuyerStateSnapshot] = {
        a.agent_id: BuyerStateSnapshot.initial(a) for a in round_ctx.society
    }
    ctx = RoundContext(
        simulation_id=round_ctx.simulation_id,
        round_number=round_number,
        round_type=round_type,
        society=round_ctx.society,
        edges=round_ctx.edges,
        pio=round_ctx.pio,
        evidence=round_ctx.evidence,
        brief=round_ctx.brief,
        snapshots=snapshots,
        seed=round_ctx.seed,
    )

    p = MockProvider()
    p.add_default(
        _make_agent_response_json(
            agent_id=round_ctx.society[0].agent_id, stance="curious_hesitant"
        )
    )

    result = await module.run_round(ctx, provider=p, sessionmaker=None)
    assert len(result.agent_responses) == len(ctx.society)
    assert result.debate_turns == []
    assert result.round_type == round_type
    assert result.round_number == round_number


# ---------------------------------------------------------------------------
# Round 6 — social_influence (debate)
# ---------------------------------------------------------------------------


def _debate_turn_json(speaker_id: UUID, target_id: UUID, *, with_shift: bool = True) -> str:
    payload = {
        "speaker_agent_id": str(speaker_id),
        "target_agent_id": str(target_id),
        "responding_to_turn_id": None,
        "argument": (
            "the peer pointed out their freelancer overhead would shrink "
            "and the brand-control safeguards seemed sufficient"
        ),
        "caused_shifts": (
            [
                {
                    "from_stance": "skeptical",
                    "to_stance": "curious_hesitant",
                    "reason": "the peer's framing eased my fear of losing brand control",
                    "triggered_by": "peer_argument_brand_control_safeguards",
                }
            ]
            if with_shift
            else []
        ),
    }
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_social_influence_round_produces_debate_turns(
    patched_cost_guard,
    basic_brief,
    valid_pio,
    evidence_build_result,
    evidence_ids: dict[str, UUID],
) -> None:
    """Round 6 must produce debate_turns (one per pair) and may produce
    caused_shifts that re-stance subjects."""
    from assembly.schemas.society import InfluenceEdge
    from tests.test_society_builder import (
        _generated_from_draft,
        _make_agent_draft,
    )

    eid = evidence_ids["user_description"]
    a = _generated_from_draft(_make_agent_draft(eid=eid))
    b = _generated_from_draft(_make_agent_draft(eid=eid))
    c = _generated_from_draft(_make_agent_draft(eid=eid))
    society = [a, b, c]
    edges = [
        InfluenceEdge(source_agent_id=b.agent_id, target_agent_id=a.agent_id, influence_strength=0.6),
        InfluenceEdge(source_agent_id=c.agent_id, target_agent_id=a.agent_id, influence_strength=0.5),
        InfluenceEdge(source_agent_id=a.agent_id, target_agent_id=b.agent_id, influence_strength=0.6),
        InfluenceEdge(source_agent_id=c.agent_id, target_agent_id=b.agent_id, influence_strength=0.5),
        InfluenceEdge(source_agent_id=a.agent_id, target_agent_id=c.agent_id, influence_strength=0.6),
        InfluenceEdge(source_agent_id=b.agent_id, target_agent_id=c.agent_id, influence_strength=0.5),
    ]
    snapshots = {x.agent_id: BuyerStateSnapshot.initial(x) for x in society}

    ctx = RoundContext(
        simulation_id=uuid4(),
        round_number=6,
        round_type="social_influence",
        society=society,
        edges=edges,
        pio=valid_pio,
        evidence=list(evidence_build_result.items),
        brief=basic_brief,
        snapshots=snapshots,
        seed=42,
    )

    p = MockProvider()
    # Default debate-turn response — will be served for every pair.
    p.add_default(_debate_turn_json(speaker_id=b.agent_id, target_id=a.agent_id))

    result = await social_influence.run_round(
        ctx, provider=p, sessionmaker=None
    )

    # k=2 per agent × 3 agents = up to 6 pairs (less if not all incoming edges exist)
    assert len(result.debate_turns) >= 3
    assert len(result.agent_responses) == 3
    # At least one subject's snapshot should have an updated stance.
    shifted = [r for r in result.agent_responses if r.shift_from_previous is not None]
    assert len(shifted) >= 1


@pytest.mark.asyncio
async def test_social_influence_no_shifts_when_mock_returns_no_shifts(
    patched_cost_guard,
    basic_brief,
    valid_pio,
    evidence_build_result,
    evidence_ids: dict[str, UUID],
) -> None:
    """If every debate turn returns caused_shifts=[], all subjects keep
    their round-5 stance."""
    from assembly.schemas.society import InfluenceEdge
    from tests.test_society_builder import (
        _generated_from_draft,
        _make_agent_draft,
    )

    eid = evidence_ids["user_description"]
    a = _generated_from_draft(_make_agent_draft(eid=eid))
    b = _generated_from_draft(_make_agent_draft(eid=eid))
    society = [a, b]
    edges = [
        InfluenceEdge(source_agent_id=b.agent_id, target_agent_id=a.agent_id, influence_strength=0.6),
        InfluenceEdge(source_agent_id=a.agent_id, target_agent_id=b.agent_id, influence_strength=0.6),
    ]
    snapshots = {x.agent_id: BuyerStateSnapshot.initial(x) for x in society}

    ctx = RoundContext(
        simulation_id=uuid4(),
        round_number=6,
        round_type="social_influence",
        society=society,
        edges=edges,
        pio=valid_pio,
        evidence=list(evidence_build_result.items),
        brief=basic_brief,
        snapshots=snapshots,
        seed=42,
    )

    p = MockProvider()
    p.add_default(_debate_turn_json(speaker_id=a.agent_id, target_id=b.agent_id, with_shift=False))

    result = await social_influence.run_round(
        ctx, provider=p, sessionmaker=None
    )
    assert all(r.shift_from_previous is None for r in result.agent_responses)


# ---------------------------------------------------------------------------
# Regression: response-shape footer + AgentRoundResponse extra-key rejection
# ---------------------------------------------------------------------------
# A live demo at society size 6 failed in round_competitor_comparison after
# 3 repair attempts. Root cause: the model echoed the `prior_round_state`
# block's `accumulated_objections` key into its response and added a
# `new_objections` key for the delta. `AgentRoundResponse` has
# `extra="forbid"`, so Pydantic rejected. Repair attempts kept producing
# the same shape because the system prompt was ambiguous about response
# keys. Fix: `_base.build_messages` now appends `_RESPONSE_SHAPE_FOOTER`
# to the system prompt for per-agent rounds (1-5, 7) — round 6 builds its
# own messages with `DebateTurnOut` and is intentionally not covered.

from pydantic import ValidationError as _PydValidationError

from assembly.pipeline.simulation.rounds import _base as _base_mod


@pytest.mark.parametrize(
    "round_type",
    [
        "baseline",
        "first_exposure",
        "objection_formation",
        "competitor_comparison",
        "proof_exposure",
        "final_stance",
    ],
)
def test_per_agent_round_messages_include_response_shape_footer(
    round_type, round_ctx, small_society
) -> None:
    """The system prompt for every per-agent round must end with the
    `_RESPONSE_SHAPE_FOOTER` so the model cannot echo the prior_round_state
    keys (`accumulated_objections`, etc.) into its response."""
    messages = _base_mod.build_messages(
        round_type=round_type,
        agent=small_society[0],
        snapshot=None,
        ctx=round_ctx,
    )
    assert len(messages) == 2
    assert messages[0].role == "system"
    sys_text = messages[0].content
    assert _base_mod._RESPONSE_SHAPE_FOOTER in sys_text
    # Footer must explicitly forbid each shape the model previously emitted.
    for forbidden_key in (
        "new_objections",
        "accumulated_objections",
        "new_persuasion_drivers",
        "accumulated_persuasion_drivers",
    ):
        assert forbidden_key in sys_text, (
            f"footer must explicitly name forbidden key {forbidden_key!r} "
            f"so the LLM cannot reintroduce it; round_type={round_type}"
        )


def test_round_6_social_influence_does_not_use_build_messages_helper() -> None:
    """Round 6 has its own message-construction path (`DebateTurnOut`
    schema, not `AgentRoundResponse`). The response-shape footer is
    AgentRoundResponse-specific and must NOT leak into the debate prompt.
    """
    src = (
        Path(__file__).resolve().parent.parent
        / "src/assembly/pipeline/simulation/rounds/social_influence.py"
    ).read_text(encoding="utf-8")
    assert "build_messages(" not in src, (
        "round 6 must not call _base.build_messages — the AgentRoundResponse "
        "footer would mislead the DebateTurnOut prompt"
    )


def test_agent_round_response_rejects_forbidden_extra_keys() -> None:
    """`AgentRoundResponse` is the contract pinned by the response-shape
    footer. If `extra="forbid"` ever loosens, the prompt fix becomes
    decorative — this test fails first."""
    base = {
        "agent_id": str(uuid4()),
        "stance": "curious_hesitant",
        "reasoning": "the agent seemed cautious",
        "objections": [],
        "persuasion_drivers": [],
        "shift_from_previous": None,
        "state_after": {
            "current_alternatives": ["x"],
            "budget": "moderate",
            "trust_threshold": "moderate",
            "switching_trigger": "proof",
            "fear": "brand damage",
            "desire": "less plugin sprawl",
            "influence_score": 0.4,
            "price_sensitivity": "moderate",
            "current_behavior": "uses apps",
            "objection_pattern": "skeptical of automation",
            "emotional_state": "cautious",
        },
    }
    for extra_key in (
        "new_objections",
        "accumulated_objections",
        "new_persuasion_drivers",
        "accumulated_persuasion_drivers",
    ):
        bad = dict(base)
        bad[extra_key] = []
        with pytest.raises(_PydValidationError) as excinfo:
            AgentRoundResponse.model_validate(bad)
        # Pydantic surfaces the offending key — confirm it does, so a
        # repair-loop user could read which key was wrong.
        assert extra_key in str(excinfo.value)
