"""OpenAI provider.

Skeleton implementation. Tests use `MockProvider`, not this.
"""
from __future__ import annotations

import logging
from time import perf_counter

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.llm.provider import LLMCallContext, LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, *, api_key: str | None = None) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise LLMProviderError(
                "openai SDK not installed. Run `uv sync` with the openai "
                "dependency, or use MockProvider in tests."
            ) from e

        key = api_key or get_settings().openai_api_key
        if not key:
            raise LLMProviderError("OPENAI_API_KEY not configured")

        self._client = AsyncOpenAI(api_key=key)

    async def chat(
        self,
        messages: list[LLMMessage],
        ctx: LLMCallContext,
    ) -> LLMResponse:
        t0 = perf_counter()
        result = await self._client.chat.completions.create(
            model=ctx.model,
            max_tokens=ctx.max_tokens,
            temperature=ctx.temperature,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            response_format={"type": "json_object"},
        )
        latency_ms = int((perf_counter() - t0) * 1000)

        text = result.choices[0].message.content or ""
        usage = result.usage

        snapshot = None
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

        return LLMResponse(
            text=text,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
            model=result.model,
            provider=self.name,
            raw=result.model_dump() if hasattr(result, "model_dump") else None,
            prompt_snapshot=snapshot,
        )
