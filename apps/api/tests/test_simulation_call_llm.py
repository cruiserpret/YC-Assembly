"""Tests for `call_llm_for_simulation`.

These tests do NOT hit Postgres. They patch `with_cost_guard` to a no-op
that just runs the closure, so we can assert the helper's *contract*:

  - Pydantic schema validation triggers a repair attempt.
  - Output-validator violations trigger a repair attempt.
  - Repair loop is bounded; LLMRepairExhausted raised on exhaustion.
  - prompt_snapshot capture is on by default.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.mock import MockProvider
from assembly.llm.provider import LLMMessage
from assembly.pipeline.simulation import call_llm as call_llm_mod


class _TinyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoning: str  # gets language-validated
    count: int


@pytest.fixture
def patched_cost_guard(monkeypatch: pytest.MonkeyPatch):
    """Phase 6.6: replace `cost_guarded_chat` with a thin pass-through so
    tests can drive `call_llm_for_simulation` without Postgres.

    Patches BOTH the canonical `assembly.llm.guarded_chat.cost_guarded_chat`
    AND the symbol re-bound at `call_llm` import time, so neither path
    leaks through to the real cost guard."""
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
async def test_call_llm_returns_parsed_schema_on_clean_response(patched_cost_guard) -> None:
    p = MockProvider()
    p.add_default(json.dumps({"reasoning": "the buyer seemed cautious", "count": 3}))

    parsed, response = await call_llm_mod.call_llm_for_simulation(
        sessionmaker=None,  # patched cost-guard ignores it
        simulation_id=uuid4(),
        stage="round_baseline",
        schema=_TinyResponse,
        messages=[LLMMessage(role="user", content="hi")],
        provider=p,
    )
    assert isinstance(parsed, _TinyResponse)
    assert parsed.reasoning == "the buyer seemed cautious"
    # snapshot captured
    assert response.prompt_snapshot is not None


@pytest.mark.asyncio
async def test_call_llm_repairs_invalid_json(patched_cost_guard) -> None:
    p = MockProvider()
    p.add_response(predicate=lambda *_: True, response="not json at all")
    p.add_default(json.dumps({"reasoning": "subjective", "count": 2}))

    parsed, _ = await call_llm_mod.call_llm_for_simulation(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="round_baseline",
        schema=_TinyResponse,
        messages=[LLMMessage(role="user", content="hi")],
        provider=p,
        max_repair_attempts=2,
    )
    assert parsed.reasoning == "subjective"


@pytest.mark.asyncio
async def test_call_llm_repairs_validator_violation(patched_cost_guard) -> None:
    """First response contains a forced verdict — validator fires repair.
    Second response is clean."""
    p = MockProvider()
    p.add_response(
        predicate=lambda *_: True,
        response=json.dumps({"reasoning": "we should kill this product", "count": 1}),
    )
    p.add_default(
        json.dumps({"reasoning": "the agent appeared cautious", "count": 1})
    )

    parsed, _ = await call_llm_mod.call_llm_for_simulation(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="round_baseline",
        schema=_TinyResponse,
        messages=[LLMMessage(role="user", content="hi")],
        provider=p,
        max_repair_attempts=2,
    )
    assert "kill" not in parsed.reasoning


@pytest.mark.asyncio
async def test_call_llm_repairs_objective_sentiment(patched_cost_guard) -> None:
    p = MockProvider()
    p.add_response(
        predicate=lambda *_: True,
        response=json.dumps({"reasoning": "the market is positive overall", "count": 1}),
    )
    p.add_default(
        json.dumps({"reasoning": "the market mood seemed cautiously open", "count": 1})
    )

    parsed, _ = await call_llm_mod.call_llm_for_simulation(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="round_baseline",
        schema=_TinyResponse,
        messages=[LLMMessage(role="user", content="hi")],
        provider=p,
        max_repair_attempts=2,
    )
    assert "the market is positive" not in parsed.reasoning


@pytest.mark.asyncio
async def test_call_llm_exhausts_when_never_clean(patched_cost_guard) -> None:
    """All attempts return verdict-laden responses → LLMRepairExhausted."""
    p = MockProvider()
    # "we should kill this product" matches verdict.imperative (forced verdict).
    p.add_default(
        json.dumps({"reasoning": "we should kill this product based on the analysis", "count": 1})
    )

    with pytest.raises(LLMRepairExhausted):
        await call_llm_mod.call_llm_for_simulation(
            sessionmaker=None,
            simulation_id=uuid4(),
            stage="round_baseline",
            schema=_TinyResponse,
            messages=[LLMMessage(role="user", content="hi")],
            provider=p,
            max_repair_attempts=1,
        )


@pytest.mark.asyncio
async def test_call_llm_allows_buyer_vocabulary(patched_cost_guard) -> None:
    """Buyer-state-friendly profile: $X MRR and ROI mentions in reasoning
    should NOT trigger a repair."""
    p = MockProvider()
    p.add_default(
        json.dumps({
            "reasoning": (
                "the agent operates at $40k MRR and demands clear ROI before "
                "switching from their current freelancer setup"
            ),
            "count": 1,
        })
    )
    parsed, _ = await call_llm_mod.call_llm_for_simulation(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="round_competitor_comparison",
        schema=_TinyResponse,
        messages=[LLMMessage(role="user", content="hi")],
        provider=p,
    )
    assert "MRR" in parsed.reasoning  # buyer vocab preserved
