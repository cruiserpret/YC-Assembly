"""Phase 17B — provider adapter STUBS (disabled by default; NO spend).

Interfaces for future GPT / Claude / Gemini baseline calls. In Phase 17B they are
HARD-DISABLED: every attempt to make a live provider call raises
``ProviderCallDisabledError``. There are NO SDK imports, NO API-key reads, and NO network
here. Live baseline locking arrives only in a later phase (17B-L) behind an explicit
flag + cost gate + human approval. The exact model ids below are June-2026
point-in-time and MUST be re-verified at run time.
"""
from __future__ import annotations

# Global kill-switch. 17B ships this False and provides no path to flip it on.
LIVE_PROVIDER_CALLS_ENABLED = False


class ProviderCallDisabledError(RuntimeError):
    """Raised when any live provider call is attempted while disabled."""


class _ProviderStub:
    provider = "unknown"
    # Point-in-time default model id (re-verify at run time; see Phase 17A spec).
    default_model_id = "unset"
    structured_output_method = "unset"

    def lock_prediction(self, *args: object, **kwargs: object):
        raise ProviderCallDisabledError(
            f"live {self.provider} calls are DISABLED in Phase 17B "
            "(no API keys, no spend). Use --naive or manual_output mode; live "
            "baseline locking arrives in Phase 17B-L behind an explicit flag + cost gate."
        )


class OpenAIBaselineStub(_ProviderStub):
    provider = "openai"
    default_model_id = "gpt-5.5"  # re-verify; gpt-5.5-pro is the higher/costlier tier
    structured_output_method = "response_format=json_schema(strict)"


class AnthropicBaselineStub(_ProviderStub):
    provider = "anthropic"
    default_model_id = "claude-opus-4-8"  # claude-fable-5 is refusal-capable; handle fallback
    structured_output_method = "forced tool_use (emit_forecast)"


class GeminiBaselineStub(_ProviderStub):
    provider = "google"
    default_model_id = "gemini-3.5-flash"  # gemini-3.5-pro not GA as of 2026-06-12
    structured_output_method = "responseSchema (+ optional Search grounding)"


PROVIDER_STUBS = {
    "openai": OpenAIBaselineStub,
    "anthropic": AnthropicBaselineStub,
    "google": GeminiBaselineStub,
}


def assert_live_calls_disabled() -> None:
    """Guard the harness can call to PROVE no live provider path is active in 17B."""
    if LIVE_PROVIDER_CALLS_ENABLED:
        raise ProviderCallDisabledError(
            "LIVE_PROVIDER_CALLS_ENABLED must be False in Phase 17B"
        )
