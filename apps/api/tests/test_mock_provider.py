"""Tests for MockProvider."""
from __future__ import annotations

import pytest

from assembly.llm.mock import MockProvider
from assembly.llm.provider import LLMCallContext, LLMMessage


@pytest.mark.asyncio
async def test_mock_returns_default_response(llm_ctx: LLMCallContext) -> None:
    p = MockProvider()
    p.add_default('{"hello": "world"}')

    r = await p.chat([LLMMessage(role="user", content="hi")], llm_ctx)
    assert r.text == '{"hello": "world"}'
    assert r.provider == "mock"
    assert len(p.calls) == 1


@pytest.mark.asyncio
async def test_mock_routes_by_stage(llm_ctx: LLMCallContext) -> None:
    p = MockProvider()
    p.add_response_for_stage("intake_parser", '{"a": 1}')
    p.add_response_for_stage("evidence_extractor", '{"phrases": []}')

    r1 = await p.chat([], llm_ctx)
    assert r1.text == '{"a": 1}'

    ctx2 = LLMCallContext(stage="evidence_extractor", model="mock", simulation_id=llm_ctx.simulation_id)
    r2 = await p.chat([], ctx2)
    assert r2.text == '{"phrases": []}'


@pytest.mark.asyncio
async def test_mock_raises_when_no_match(llm_ctx: LLMCallContext) -> None:
    p = MockProvider()
    p.add_response_for_stage("some_other_stage", "{}")
    with pytest.raises(AssertionError, match="no matching response"):
        await p.chat([], llm_ctx)


@pytest.mark.asyncio
async def test_mock_captures_prompt_snapshot_when_requested(llm_ctx: LLMCallContext) -> None:
    p = MockProvider()
    p.add_default("{}")
    msgs = [LLMMessage(role="system", content="sys"), LLMMessage(role="user", content="hi")]
    r = await p.chat(msgs, llm_ctx)
    assert r.prompt_snapshot is not None
    assert len(r.prompt_snapshot["messages"]) == 2
    assert r.prompt_snapshot["ctx"]["stage"] == "intake_parser"
