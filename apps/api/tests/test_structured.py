"""Tests for the structured-output repair loop on LLMProvider."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.mock import MockProvider
from assembly.llm.provider import LLMCallContext, LLMMessage


class Tiny(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    count: int


@pytest.mark.asyncio
async def test_structured_output_parses_clean_json(llm_ctx: LLMCallContext) -> None:
    p = MockProvider()
    p.add_default('{"name": "alice", "count": 3}')

    parsed, response = await p.structured_output(Tiny, [], llm_ctx)
    assert isinstance(parsed, Tiny)
    assert parsed.name == "alice"
    assert parsed.count == 3
    assert response.text == '{"name": "alice", "count": 3}'


@pytest.mark.asyncio
async def test_structured_output_strips_code_fences(llm_ctx: LLMCallContext) -> None:
    p = MockProvider()
    p.add_default('```json\n{"name": "bob", "count": 1}\n```')

    parsed, _ = await p.structured_output(Tiny, [], llm_ctx)
    assert parsed.name == "bob"


@pytest.mark.asyncio
async def test_structured_output_repairs_invalid_json(llm_ctx: LLMCallContext) -> None:
    """First response is malformed; second is valid. Repair loop should succeed."""
    p = MockProvider()
    # First call: garbage
    p.add_response(predicate=lambda *_: True, response="not json at all")
    # Second call: valid (will be served as default after first rule consumed)
    p.add_default('{"name": "carol", "count": 7}')

    parsed, _ = await p.structured_output(
        Tiny, [LLMMessage(role="user", content="hi")], llm_ctx, max_repair_attempts=2
    )
    assert parsed.name == "carol"


@pytest.mark.asyncio
async def test_structured_output_repairs_validation_failure(llm_ctx: LLMCallContext) -> None:
    """First response is valid JSON but missing a field; repair loop succeeds."""
    p = MockProvider()
    p.add_response(predicate=lambda *_: True, response='{"name": "dave"}')  # missing count
    p.add_default('{"name": "dave", "count": 9}')

    parsed, _ = await p.structured_output(Tiny, [], llm_ctx, max_repair_attempts=2)
    assert parsed.count == 9


@pytest.mark.asyncio
async def test_structured_output_exhausts_repair(llm_ctx: LLMCallContext) -> None:
    """All attempts return garbage — should raise LLMRepairExhausted."""
    p = MockProvider()
    p.add_default("not json")

    with pytest.raises(LLMRepairExhausted):
        await p.structured_output(Tiny, [], llm_ctx, max_repair_attempts=1)
