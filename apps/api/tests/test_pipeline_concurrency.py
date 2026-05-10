"""Phase 6.5: bounded concurrency in run_per_agent_round.

The semaphore caps the number of in-flight LLM calls per round. We assert
that the cap holds even with N agents > cap.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from assembly.config import Settings
from assembly.llm.mock import MockProvider
from assembly.pipeline.simulation import call_llm as call_llm_mod
from assembly.pipeline.simulation.rounds import first_exposure
from assembly.pipeline.simulation.state import BuyerStateSnapshot, RoundContext


@pytest.fixture
def patched_cost_guard(monkeypatch: pytest.MonkeyPatch):
    """Phase 6.6: bypass `cost_guarded_chat` (universal helper). The semaphore
    is on a separate code path from the cost guard, so this just ensures the
    LLM call goes through to the MockProvider without needing a real sim row."""
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

    monkeypatch.setattr(
        guarded_chat_mod, "cost_guarded_chat", fake_cost_guarded_chat
    )
    monkeypatch.setattr(
        call_llm_mod, "cost_guarded_chat", fake_cost_guarded_chat
    )


@pytest.mark.asyncio
async def test_concurrency_semaphore_caps_in_flight_calls(
    patched_cost_guard,
    monkeypatch: pytest.MonkeyPatch,
    basic_brief,
    valid_pio,
    evidence_build_result,
    evidence_ids: dict[str, UUID],
) -> None:
    """With ASSEMBLY_SIMULATION_MAX_CONCURRENCY=2 and 6 agents, never more
    than 2 LLM calls should be in flight at once."""
    from tests.test_society_builder import (
        _generated_from_draft,
        _make_agent_draft,
    )

    # Force max_concurrency=2 via patched settings.
    from assembly.pipeline.simulation.rounds import _base as rounds_base

    # Also need agents — build 6 from the fixture.
    eid = evidence_ids["user_description"]
    society = [_generated_from_draft(_make_agent_draft(eid=eid)) for _ in range(6)]
    snapshots = {a.agent_id: BuyerStateSnapshot.initial(a) for a in society}
    ctx = RoundContext(
        simulation_id=uuid4(),
        round_number=2,
        round_type="first_exposure",
        society=society,
        edges=[],
        pio=valid_pio,
        evidence=list(evidence_build_result.items),
        brief=basic_brief,
        snapshots=snapshots,
        seed=42,
    )

    # Force config to max_concurrency=2.
    fake_settings = Settings(simulation_max_concurrency=2)
    monkeypatch.setattr(rounds_base, "get_settings", lambda: fake_settings)

    in_flight = 0
    max_observed = 0
    lock = asyncio.Lock()

    a0 = society[0].agent_id
    canned = json.dumps({
        "agent_id": str(a0),
        "stance": "curious_hesitant",
        "reasoning": "the agent appeared cautious about the offer",
        "objections": [],
        "persuasion_drivers": [],
        "shift_from_previous": None,
        "state_after": {
            "current_alternatives": ["x"],
            "budget": "mid",
            "trust_threshold": "needs proof",
            "switching_trigger": "case studies",
            "fear": "control loss",
            "desire": "fewer plugins",
            "influence_score": 0.4,
            "price_sensitivity": "moderate",
            "current_behavior": "operates Shopify store",
            "objection_pattern": "AI sounds unproven",
            "emotional_state": "cautious",
        },
    })

    class _ConcurrencyTrackingProvider(MockProvider):
        async def chat(self, messages, ctx_):
            nonlocal in_flight, max_observed
            async with lock:
                in_flight += 1
                if in_flight > max_observed:
                    max_observed = in_flight
            try:
                # Yield once so other tasks can attempt to enter.
                await asyncio.sleep(0.01)
                return await super().chat(messages, ctx_)
            finally:
                async with lock:
                    in_flight -= 1

    p = _ConcurrencyTrackingProvider()
    p.add_default(canned)

    await first_exposure.run_round(ctx, provider=p, sessionmaker=None)

    # Cap honored — never more than 2 in flight at once.
    assert max_observed <= 2, f"observed up to {max_observed} in-flight calls (cap was 2)"
    # And we did saturate the cap (else the test isn't proving anything).
    assert max_observed >= 2, f"only {max_observed} in-flight; semaphore not actually exercised"
