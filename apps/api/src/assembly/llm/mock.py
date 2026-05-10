"""Deterministic mock LLM provider for tests.

Pre-load with `(predicate, response)` pairs OR `(stage, response)` pairs OR a
default. The provider returns the first matching response for each call.

Usage:

    mock = MockProvider()
    mock.add_response_for_stage("intake_parser", json.dumps({...valid PIO...}))
    mock.add_default(json.dumps({}))

    parsed, _ = await mock.structured_output(SomeSchema, messages, ctx)
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from assembly.llm.provider import LLMCallContext, LLMMessage, LLMProvider, LLMResponse


@dataclass
class _Rule:
    predicate: Callable[[list[LLMMessage], LLMCallContext], bool]
    response: str
    prompt_tokens: int
    completion_tokens: int


def _approximate_token_count(text: str) -> int:
    """Rough char/4 estimate. Good enough for cost-guard tests."""
    return max(1, len(text) // 4)


class MockProvider(LLMProvider):
    """Test double for `LLMProvider`. Returns pre-canned responses, never
    contacts a real API. Records every call into `self.calls` for assertion."""

    name = "mock"

    def __init__(self, *, default_model: str = "mock") -> None:
        self._rules: list[_Rule] = []
        self._default: _Rule | None = None
        self._default_model = default_model
        self.calls: list[tuple[list[LLMMessage], LLMCallContext]] = []

    # --- registration helpers -------------------------------------------

    def add_response(
        self,
        predicate: Callable[[list[LLMMessage], LLMCallContext], bool],
        response: str,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        self._rules.append(
            _Rule(
                predicate=predicate,
                response=response,
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or _approximate_token_count(response),
            )
        )

    def add_response_for_stage(self, stage: str, response: str) -> None:
        self.add_response(
            predicate=lambda _msgs, ctx, s=stage: ctx.stage == s,
            response=response,
        )

    def add_response_sequence(self, stage: str, responses: list[str]) -> None:
        """Register N rules for `stage`, served FIFO. Each rule fires once
        (rules are popped on match by `_serve()`), so successive calls in
        `stage` get responses[0], responses[1], ..."""
        for r in responses:
            self.add_response_for_stage(stage, r)

    def add_default(self, response: str) -> None:
        self._default = _Rule(
            predicate=lambda *_: True,
            response=response,
            prompt_tokens=0,
            completion_tokens=_approximate_token_count(response),
        )

    # --- LLMProvider implementation -------------------------------------

    async def chat(
        self,
        messages: list[LLMMessage],
        ctx: LLMCallContext,
    ) -> LLMResponse:
        self.calls.append((messages, ctx))
        rule = self._serve(messages, ctx)
        if rule is None:
            raise AssertionError(
                f"MockProvider has no matching response for stage={ctx.stage!r}. "
                f"Registered rules: {len(self._rules)} + default={self._default is not None}"
            )

        prompt_tokens = rule.prompt_tokens or sum(
            _approximate_token_count(m.content) for m in messages
        )

        snapshot: dict | None = None
        if ctx.capture_prompt_snapshot:
            snapshot = {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "ctx": {
                    "stage": ctx.stage,
                    "model": ctx.model,
                    "max_tokens": ctx.max_tokens,
                    "temperature": ctx.temperature,
                },
            }

        # tiny synthetic latency
        time.sleep(0)

        return LLMResponse(
            text=rule.response,
            prompt_tokens=prompt_tokens,
            completion_tokens=rule.completion_tokens,
            latency_ms=1,
            model=ctx.model or self._default_model,
            provider=self.name,
            raw={"mock": True},
            prompt_snapshot=snapshot,
        )

    # --- internals ------------------------------------------------------

    def _serve(self, messages: list[LLMMessage], ctx: LLMCallContext) -> _Rule | None:
        """First-match wins; once consumed, the rule is removed so sequences
        advance on each call."""
        for i, rule in enumerate(self._rules):
            try:
                if rule.predicate(messages, ctx):
                    # Pop so successive calls move to the next rule (lets
                    # us simulate "first attempt fails, second succeeds").
                    self._rules.pop(i)
                    return rule
            except Exception:
                continue
        return self._default
