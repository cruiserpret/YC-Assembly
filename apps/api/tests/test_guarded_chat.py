"""Unit tests for the Phase 6.6 universal `cost_guarded_chat` helper.

These tests do NOT require Postgres. They patch `with_cost_guard` to a
controllable test double so we can assert:

  - cost_guarded_chat builds the LLMCallContext correctly
  - it delegates to with_cost_guard with the right arguments
  - it propagates the LLMResponse from the closure back to the caller
  - cap pre-check refusal (CostCapExceeded) bubbles through
  - provider failures bubble through (and with_cost_guard logs them)

The end-to-end logging proof (an actual `llm_call_log` row written to
Postgres for parser/evidence/society stages) lives as an integration test
in `test_phase_6_6_logging.py`.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from assembly.llm.errors import CostCapExceeded
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.mock import MockProvider
from assembly.llm.provider import LLMMessage, LLMResponse


def _spy_with_cost_guard(monkeypatch: pytest.MonkeyPatch, *, raise_on_call=None):
    """Replace `with_cost_guard` (in the guarded_chat module) with a spy that
    records its kwargs and runs the supplied `actual_call`.

    `raise_on_call` (Exception | None) lets a test simulate cost-cap refusal
    or provider failure.
    """
    from assembly.llm import guarded_chat as gc_mod

    captured: dict[str, Any] = {}

    async def fake_with_cost_guard(sessionmaker, **kwargs):
        captured.update(kwargs)
        captured["sessionmaker"] = sessionmaker
        if raise_on_call is not None:
            raise raise_on_call
        return await kwargs["actual_call"]()

    monkeypatch.setattr(gc_mod, "with_cost_guard", fake_with_cost_guard)
    return captured


@pytest.mark.asyncio
async def test_cost_guarded_chat_returns_response_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _spy_with_cost_guard(monkeypatch)

    p = MockProvider()
    p.add_default("hello world")
    sim_id = uuid4()

    response = await cost_guarded_chat(
        sessionmaker=None,
        simulation_id=sim_id,
        stage="intake_parser",
        messages=[LLMMessage(role="user", content="parse this")],
        provider=p,
    )

    assert isinstance(response, LLMResponse)
    assert response.text == "hello world"
    # The spy received the kwargs cost_guarded_chat built:
    assert captured["simulation_id"] == sim_id
    assert captured["stage"] == "intake_parser"
    assert captured["provider"] == "mock"
    assert callable(captured["actual_call"])


@pytest.mark.asyncio
async def test_cost_guarded_chat_resolves_model_via_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `model=None`, the helper resolves via `pick_model_for_stage`."""
    captured = _spy_with_cost_guard(monkeypatch)

    p = MockProvider()
    p.add_default("ok")
    await cost_guarded_chat(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="round_baseline",
        messages=[LLMMessage(role="user", content="x")],
        provider=p,
        model=None,
    )

    # Whatever `pick_model_for_stage('round_baseline')` returns must end up
    # both in the `with_cost_guard` model kwarg AND on the LLMResponse.
    from assembly.llm.router import pick_model_for_stage
    expected = pick_model_for_stage("round_baseline")
    assert captured["model"] == expected


@pytest.mark.asyncio
async def test_cost_guarded_chat_respects_explicit_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _spy_with_cost_guard(monkeypatch)

    p = MockProvider()
    p.add_default("ok")
    await cost_guarded_chat(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="society_builder",
        messages=[LLMMessage(role="user", content="x")],
        provider=p,
        model="claude-haiku-test-override",
    )
    assert captured["model"] == "claude-haiku-test-override"


@pytest.mark.asyncio
async def test_cost_guarded_chat_uses_default_cap_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `hard_cap_usd=None`, the helper uses settings.cost_hard_usd."""
    from assembly.config import get_settings

    captured = _spy_with_cost_guard(monkeypatch)

    p = MockProvider()
    p.add_default("ok")
    await cost_guarded_chat(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="intake_parser",
        messages=[LLMMessage(role="user", content="x")],
        provider=p,
    )
    assert captured["hard_cap_usd"] == Decimal(str(get_settings().cost_hard_usd))


@pytest.mark.asyncio
async def test_cost_guarded_chat_propagates_cost_cap_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If with_cost_guard refuses (CostCapExceeded), cost_guarded_chat must
    propagate the exception WITHOUT calling the provider."""
    refusal = CostCapExceeded(
        simulation_id="00000000-0000-0000-0000-000000000000",
        total_so_far=4.99,
        estimated_next=0.05,
        hard_cap=5.00,
    )
    _spy_with_cost_guard(monkeypatch, raise_on_call=refusal)

    p = MockProvider()
    # Note: no rules added — if the provider WERE called, MockProvider raises.
    with pytest.raises(CostCapExceeded):
        await cost_guarded_chat(
            sessionmaker=None,
            simulation_id=uuid4(),
            stage="round_baseline",
            messages=[LLMMessage(role="user", content="x")],
            provider=p,
        )
    # Provider must not have been called when the cap refused pre-emptively.
    assert len(p.calls) == 0


@pytest.mark.asyncio
async def test_cost_guarded_chat_propagates_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the provider itself raises, cost_guarded_chat must propagate (with
    the failure logged via with_cost_guard's success=False path — verified
    in the integration test, not here)."""
    _spy_with_cost_guard(monkeypatch)
    p = MockProvider()  # no rules → raises on first chat call

    with pytest.raises(AssertionError):
        await cost_guarded_chat(
            sessionmaker=None,
            simulation_id=uuid4(),
            stage="intake_parser",
            messages=[LLMMessage(role="user", content="x")],
            provider=p,
        )


@pytest.mark.asyncio
async def test_cost_guarded_chat_passes_token_estimates_to_cost_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-supplied `estimated_prompt_tokens` / `estimated_completion_tokens`
    must reach `with_cost_guard` so the cap pre-check is accurate."""
    captured = _spy_with_cost_guard(monkeypatch)

    p = MockProvider()
    p.add_default("ok")
    await cost_guarded_chat(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="society_builder",
        messages=[LLMMessage(role="user", content="x")],
        provider=p,
        estimated_prompt_tokens=12345,
        estimated_completion_tokens=6789,
    )
    assert captured["estimated_prompt_tokens"] == 12345
    assert captured["estimated_completion_tokens"] == 6789


@pytest.mark.asyncio
async def test_cost_guarded_chat_captures_prompt_snapshot_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit trail: by default, the resolved prompt is stored on the response."""
    _spy_with_cost_guard(monkeypatch)

    p = MockProvider()
    p.add_default("ok")
    response = await cost_guarded_chat(
        sessionmaker=None,
        simulation_id=uuid4(),
        stage="intake_parser",
        messages=[
            LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="usr"),
        ],
        provider=p,
    )
    assert response.prompt_snapshot is not None
    snap_messages = response.prompt_snapshot["messages"]
    assert {m["role"] for m in snap_messages} == {"system", "user"}
