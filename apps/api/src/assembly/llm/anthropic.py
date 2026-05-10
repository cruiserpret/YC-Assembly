"""Anthropic Claude provider.

Skeleton implementation. Wires through the official `anthropic` SDK when
the package is installed AND `ANTHROPIC_API_KEY` is set; otherwise raises
on instantiation. Tests use `MockProvider`, not this.
"""
from __future__ import annotations

import logging
from time import perf_counter

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.llm.provider import LLMCallContext, LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


def _model_deprecates_temperature(model: str) -> bool:
    """Return True if this Anthropic model rejects the `temperature` param.

    Anthropic deprecated `temperature` for the opus-4-7 family (the API
    returns 400 if the param is sent). Older models still accept it.
    """
    m = model.lower()
    return "opus-4-7" in m


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None) -> None:
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise LLMProviderError(
                "anthropic SDK not installed. Run `uv sync` with the "
                "anthropic dependency, or use MockProvider in tests."
            ) from e

        key = api_key or get_settings().anthropic_api_key
        if not key:
            raise LLMProviderError("ANTHROPIC_API_KEY not configured")

        self._client = anthropic.AsyncAnthropic(api_key=key)

    async def chat(
        self,
        messages: list[LLMMessage],
        ctx: LLMCallContext,
    ) -> LLMResponse:
        # Anthropic separates system message from the conversation.
        system_msgs = [m for m in messages if m.role == "system"]
        user_msgs = [m for m in messages if m.role != "system"]

        system_text = "\n\n".join(m.content for m in system_msgs)

        # Build kwargs conditionally:
        #   - `system=None` would serialize as JSON null and the API rejects
        #     it with "system: Input should be a valid array". Omit when empty.
        #   - `temperature` is deprecated for `claude-opus-4-*` models — passing
        #     it returns 400 ("`temperature` is deprecated for this model"). We
        #     pass it only for models that still accept it.
        kwargs: dict = {
            "model": ctx.model,
            "max_tokens": ctx.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in user_msgs],
        }
        if system_text:
            kwargs["system"] = system_text
        if not _model_deprecates_temperature(ctx.model):
            kwargs["temperature"] = ctx.temperature

        # Use streaming when max_tokens is high enough that the SDK's 10-min
        # non-streaming ceiling could be hit. The SDK enforces this via
        # MODEL_NONSTREAMING_TOKENS — for opus-4-7 it currently rejects
        # max_tokens >= ~21K non-streaming. We always stream above 8K to be
        # safe across model versions.
        use_streaming = ctx.max_tokens > 8192

        t0 = perf_counter()
        if use_streaming:
            text_parts: list[str] = []
            async with self._client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    text_parts.append(chunk)
                final = await stream.get_final_message()
            text = "".join(text_parts)
            result_model = final.model
            result_usage = final.usage
            raw_dump = final.model_dump() if hasattr(final, "model_dump") else None
        else:
            result = await self._client.messages.create(**kwargs)
            text = "".join(
                block.text
                for block in result.content
                if getattr(block, "type", None) == "text"
            )
            result_model = result.model
            result_usage = result.usage
            raw_dump = result.model_dump() if hasattr(result, "model_dump") else None
        latency_ms = int((perf_counter() - t0) * 1000)

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
            prompt_tokens=result_usage.input_tokens,
            completion_tokens=result_usage.output_tokens,
            latency_ms=latency_ms,
            model=result_model,
            provider=self.name,
            raw=raw_dump,
            prompt_snapshot=snapshot,
        )
