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

        # Phase 12A.10G: prompt caching gate. The cache flag controls
        # whether `cache_control` blocks are attached. Content sent to
        # Anthropic is byte-identical with caching on vs off — only the
        # `cache_control` attribute differs. Verified by content-identity
        # tests in test_anthropic_prompt_caching_12a_10g.py.
        cache_enabled = bool(
            get_settings().anthropic_prompt_cache_enabled
        )
        any_system_breakpoint = any(
            m.cache_breakpoint for m in system_msgs
        )
        any_user_breakpoint = any(
            m.cache_breakpoint for m in user_msgs
        )

        # ---- System content -----------------------------------------
        # When no system breakpoint is requested, keep the simple
        # string form (preserves byte-identical wire format vs
        # pre-12A.10G). When breakpoint requested + caching enabled,
        # switch to the content-block-list form Anthropic requires for
        # cache_control on system content.
        if cache_enabled and any_system_breakpoint:
            system_blocks: list[dict] = []
            for m in system_msgs:
                block: dict = {"type": "text", "text": m.content}
                if m.cache_breakpoint:
                    block["cache_control"] = {"type": "ephemeral"}
                system_blocks.append(block)
            system_value: Any = system_blocks
        else:
            system_text = "\n\n".join(m.content for m in system_msgs)
            system_value = system_text if system_text else None

        # ---- User / assistant content -------------------------------
        # Same logic: keep plain-string form unless a breakpoint
        # actually fires.
        if cache_enabled and any_user_breakpoint:
            built_messages: list[dict] = []
            for m in user_msgs:
                block: dict = {"type": "text", "text": m.content}
                if m.cache_breakpoint:
                    block["cache_control"] = {"type": "ephemeral"}
                built_messages.append({
                    "role": m.role, "content": [block],
                })
        else:
            built_messages = [
                {"role": m.role, "content": m.content}
                for m in user_msgs
            ]

        kwargs: dict = {
            "model": ctx.model,
            "max_tokens": ctx.max_tokens,
            "messages": built_messages,
        }
        if system_value is not None:
            kwargs["system"] = system_value
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

        # Phase 12A.10G: capture cache usage from Anthropic's usage
        # object. These attributes are present on every Anthropic
        # response when caching is in use; absent / None when the
        # call had no cache_control block. We defensively read with
        # getattr in case the SDK version is older.
        cache_creation = getattr(
            result_usage, "cache_creation_input_tokens", None,
        )
        cache_read = getattr(
            result_usage, "cache_read_input_tokens", None,
        )

        snapshot = None
        if ctx.capture_prompt_snapshot:
            snapshot = {
                # NOTE: snapshot records the LMessage shape we received,
                # NOT the post-cache_control wire format. This keeps
                # backtests deterministic: snapshots from cached and
                # uncached runs of the same call site are identical
                # except for the cache_breakpoint flag.
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "cache_breakpoint": m.cache_breakpoint,
                    }
                    for m in messages
                ],
                "ctx": {
                    "stage": ctx.stage,
                    "model": ctx.model,
                    "max_tokens": ctx.max_tokens,
                    "temperature": ctx.temperature,
                },
                "anthropic_prompt_cache_enabled": cache_enabled,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
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
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        )
