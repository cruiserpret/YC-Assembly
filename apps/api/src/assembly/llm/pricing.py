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
) -> Decimal:
    """Compute USD cost from token counts. Returns Decimal for precision."""
    p = model_pricing(model)
    return (
        Decimal(prompt_tokens) * p.input_per_mtok / Decimal(1_000_000)
        + Decimal(completion_tokens) * p.output_per_mtok / Decimal(1_000_000)
    )


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
