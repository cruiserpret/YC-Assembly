"""LLM provider abstraction.

Public interface:

    from assembly.llm import (
        LLMProvider, LLMMessage, LLMCallContext, LLMResponse,
        MockProvider, AnthropicProvider, OpenAIProvider,
        CostCapExceeded, LLMProviderError,
        with_cost_guard, log_llm_call,
        pick_model_for_stage, model_pricing,
    )

The CTO/pipeline code should depend only on `LLMProvider` and `LLMCallContext`,
not on the concrete provider classes. Tests use `MockProvider`.
"""
from assembly.llm.cost_guard import CostCapExceeded, with_cost_guard
from assembly.llm.cost_log import log_llm_call
from assembly.llm.errors import (
    LLMProviderError,
    LLMRepairExhausted,
    LLMSchemaValidationError,
)
from assembly.llm.mock import MockProvider
from assembly.llm.pricing import estimate_cost_usd, model_pricing
from assembly.llm.provider import (
    LLMCallContext,
    LLMMessage,
    LLMProvider,
    LLMResponse,
)
from assembly.llm.router import pick_model_for_stage

__all__ = [
    "AnthropicProvider",
    "CostCapExceeded",
    "LLMCallContext",
    "LLMMessage",
    "LLMProvider",
    "LLMProviderError",
    "LLMRepairExhausted",
    "LLMResponse",
    "LLMSchemaValidationError",
    "MockProvider",
    "OpenAIProvider",
    "estimate_cost_usd",
    "log_llm_call",
    "model_pricing",
    "pick_model_for_stage",
    "with_cost_guard",
]


# Lazy imports for optional providers — avoid forcing the anthropic/openai
# SDKs to be installed for unit tests that only use MockProvider.
def __getattr__(name: str):
    if name == "AnthropicProvider":
        from assembly.llm.anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "OpenAIProvider":
        from assembly.llm.openai import OpenAIProvider

        return OpenAIProvider
    raise AttributeError(f"module 'assembly.llm' has no attribute {name!r}")
