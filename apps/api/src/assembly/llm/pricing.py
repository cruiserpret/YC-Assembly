"""Per-model token pricing in USD per 1M tokens.

Numbers are approximate and conservative — the cost guard uses these for
*estimation* only. The actual `cost_usd` written to `llm_call_log` is computed
from the real token counts returned by the provider.

Update this table as providers update their pricing. Conservative estimates
are preferred; the cost guard refuses calls that would exceed the cap, so
*overestimating* cost only causes false-positive refusals (safe), while
underestimating causes the cap to be silently exceeded (unsafe)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ModelPricing:
    """Cost in USD per 1,000,000 tokens, separately for input and output."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal


# Anthropic — list pricing as of 2026-Q1; update as needed.
_ANTHROPIC: dict[str, ModelPricing] = {
    "claude-opus-4-7": ModelPricing(Decimal("15.00"), Decimal("75.00")),
    "claude-sonnet-4-6": ModelPricing(Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": ModelPricing(Decimal("1.00"), Decimal("5.00")),
}

# OpenAI — approximate for general-availability gpt-4o family.
_OPENAI: dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing(Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": ModelPricing(Decimal("0.15"), Decimal("0.60")),
}

# Special "mock" pricing for tests — zero cost so the cap never trips
# unexpectedly during unit tests.
_MOCK: dict[str, ModelPricing] = {
    "mock": ModelPricing(Decimal("0"), Decimal("0")),
    "mock-expensive": ModelPricing(Decimal("100.00"), Decimal("500.00")),
    "mock-1536": ModelPricing(Decimal("0"), Decimal("0")),
    "none": ModelPricing(Decimal("0"), Decimal("0")),
}

# Embeddings — input-only pricing. We treat completion side as zero since
# embedding calls have no completion tokens.
_EMBEDDINGS: dict[str, ModelPricing] = {
    # OpenAI text-embedding-3-small: $0.02 / Mtok input.
    "text-embedding-3-small": ModelPricing(Decimal("0.02"), Decimal("0")),
    # OpenAI text-embedding-3-large: $0.13 / Mtok input.
    "text-embedding-3-large": ModelPricing(Decimal("0.13"), Decimal("0")),
}


def model_pricing(model: str) -> ModelPricing:
    """Return pricing for a model id. Falls back to a conservative high
    estimate for unknown models so the cost guard errs on the safe side."""
    for table in (_ANTHROPIC, _OPENAI, _MOCK, _EMBEDDINGS):
        if model in table:
            return table[model]
    # Unknown model: use a high default so we don't underestimate.
    return ModelPricing(Decimal("20.00"), Decimal("80.00"))


def estimate_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> Decimal:
    """Compute USD cost from token counts. Returns Decimal for precision.

    Phase 12A.10G: when Anthropic prompt caching is in use, the
    response's `usage` object reports `cache_creation_input_tokens`
    (billed at 1.25× input price, one-time per cache write) and
    `cache_read_input_tokens` (billed at 0.10× input price, per
    cache hit). These tokens are ALSO counted inside `prompt_tokens`
    by the SDK, so we subtract them to avoid double-billing before
    re-adding the cached portion at the discounted rate.

    For non-cached calls (both cache counts None or 0), the math
    reduces to the pre-12A.10G formula exactly.
    """
    p = model_pricing(model)
    cache_create = cache_creation_input_tokens or 0
    cache_read = cache_read_input_tokens or 0
    # Tokens that hit the full input price = total - (cache tokens
    # that are billed at a different rate).
    base_input_tokens = max(0, prompt_tokens - cache_create - cache_read)
    base_cost = (
        Decimal(base_input_tokens) * p.input_per_mtok
        / Decimal(1_000_000)
    )
    cache_write_cost = (
        Decimal(cache_create) * p.input_per_mtok
        * Decimal("1.25") / Decimal(1_000_000)
    )
    cache_read_cost = (
        Decimal(cache_read) * p.input_per_mtok
        * Decimal("0.10") / Decimal(1_000_000)
    )
    output_cost = (
        Decimal(completion_tokens) * p.output_per_mtok
        / Decimal(1_000_000)
    )
    return base_cost + cache_write_cost + cache_read_cost + output_cost


def estimate_call_cost_usd(
    *,
    model: str,
    estimated_prompt_tokens: int,
    estimated_completion_tokens: int,
) -> Decimal:
    """Pre-call estimate used by the cost guard. Same math; named separately
    so the call site reads clearly."""
    return estimate_cost_usd(
        model=model,
        prompt_tokens=estimated_prompt_tokens,
        completion_tokens=estimated_completion_tokens,
    )
