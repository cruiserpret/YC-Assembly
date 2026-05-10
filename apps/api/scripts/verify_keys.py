"""Live LLM-key smoke test.

Hits the configured providers through Assembly's own LLMProvider abstraction
with the smallest possible payload and reports auth, latency, tokens, cost.

Usage:
    cd apps/api
    uv run python scripts/verify_keys.py

Exit codes:
    0  every configured provider responded successfully
    1  at least one configured provider failed
    2  config error (key missing, SDK missing, etc.)

This script does NOT print the API key. It only reports whether a key was
loaded, what model it called, and the response shape.
"""
from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel, ConfigDict

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.llm.pricing import estimate_cost_usd
from assembly.llm.provider import LLMCallContext, LLMMessage


class _Tiny(BaseModel):
    """Tiny structured-output schema for the smoke test."""

    model_config = ConfigDict(extra="forbid")

    color: str


PROMPT = (
    "Return one JSON object with exactly one key, 'color', whose value is "
    "the word 'blue'. Reply with only the JSON object — no prose, no markdown."
)


def _print_header(provider: str, model: str) -> None:
    print(f"\n--- {provider} ({model}) ---")


def _print_success(parsed: _Tiny, response, *, provider: str) -> None:
    cost = estimate_cost_usd(
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
    )
    print(f"  status:            OK")
    print(f"  model:             {response.model}")
    print(f"  prompt_tokens:     {response.prompt_tokens}")
    print(f"  completion_tokens: {response.completion_tokens}")
    print(f"  latency_ms:        {response.latency_ms}")
    print(f"  estimated_cost:    ${cost:.6f}")
    print(f"  parsed.color:      {parsed.color!r}")


async def verify_openai() -> int:
    settings = get_settings()
    if not settings.openai_api_key:
        print("(SKIP) OPENAI_API_KEY not set in .env")
        return 0  # not configured ≠ failure

    _print_header("OpenAI", "gpt-4o-mini")

    try:
        from assembly.llm.openai import OpenAIProvider
    except ImportError as e:
        print(f"  status:            FAIL (openai SDK not installed: {e})")
        return 2

    try:
        provider = OpenAIProvider()
    except LLMProviderError as e:
        print(f"  status:            FAIL (provider init: {e})")
        return 2

    ctx = LLMCallContext(
        stage="verify_keys",
        model="gpt-4o-mini",
        max_tokens=64,
        temperature=0.0,
    )
    messages = [LLMMessage(role="user", content=PROMPT)]

    try:
        parsed, response = await provider.structured_output(
            _Tiny, messages, ctx, max_repair_attempts=1
        )
    except Exception as e:
        print(f"  status:            FAIL ({type(e).__name__}: {e})")
        return 1

    _print_success(parsed, response, provider="OpenAI")
    return 0


async def verify_anthropic() -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("(SKIP) ANTHROPIC_API_KEY not set in .env")
        return 0

    _print_header("Anthropic", "claude-haiku-4-5")

    try:
        from assembly.llm.anthropic import AnthropicProvider
    except ImportError as e:
        print(f"  status:            FAIL (anthropic SDK not installed: {e})")
        return 2

    try:
        provider = AnthropicProvider()
    except LLMProviderError as e:
        print(f"  status:            FAIL (provider init: {e})")
        return 2

    ctx = LLMCallContext(
        stage="verify_keys",
        model="claude-haiku-4-5",
        max_tokens=64,
        temperature=0.0,
    )
    messages = [LLMMessage(role="user", content=PROMPT)]

    try:
        parsed, response = await provider.structured_output(
            _Tiny, messages, ctx, max_repair_attempts=1
        )
    except Exception as e:
        print(f"  status:            FAIL ({type(e).__name__}: {e})")
        return 1

    _print_success(parsed, response, provider="Anthropic")
    return 0


async def main() -> int:
    print("=== Assembly LLM-key smoke test ===")
    rc_openai = await verify_openai()
    rc_anthropic = await verify_anthropic()
    print()
    if rc_openai == 0 and rc_anthropic == 0:
        print("Result: PASS — every configured provider answered cleanly.")
        return 0
    print("Result: FAIL — at least one configured provider did not answer.")
    return rc_openai or rc_anthropic


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
