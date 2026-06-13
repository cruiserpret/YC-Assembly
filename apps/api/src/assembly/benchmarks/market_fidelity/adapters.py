"""Phase 17C — open-weight / local model adapter SCAFFOLD (disabled by default).

Defines the interface a future open-weight/local runner (Ollama / vLLM / llama.cpp /
generic local) will implement, so the benchmark is model-agnostic. In Phase 17C these
are DISABLED stubs: there are NO heavy model dependencies, NO model downloads, and NO
real generation. ``generate_prediction`` raises ``AdapterDisabledError``. A real
adapter (Phase 17E) must keep ``validate_offline_mode`` and only run offline against a
frozen bundle, behind an explicit flag + approval.
"""
from __future__ import annotations

from collections.abc import Mapping

from assembly.benchmarks.market_fidelity.offline_policy import validate_offline_blind_run_config

# 17C ships every adapter disabled; no path flips this on.
LOCAL_MODEL_CALLS_ENABLED = False


class AdapterDisabledError(RuntimeError):
    """Raised when a disabled adapter is asked to generate a prediction."""


class LocalModelAdapter:
    """Base interface. Concrete adapters set ``runner`` and may override loading, but
    ``generate_prediction`` stays disabled in 17C."""

    runner = "abstract_local"
    requires_packages: tuple[str, ...] = ()

    def load_model_config(self, config: Mapping) -> dict:
        """Record (do NOT load) the model config; returns a normalized metadata dict.
        No weights are read or downloaded."""
        return {
            "runner": self.runner,
            "base_model_family": config.get("base_model_family"),
            "base_model_checkpoint": config.get("base_model_checkpoint"),
            "local_or_remote": config.get("local_or_remote", "local"),
            "training_cutoff": config.get("training_cutoff"),
            "model_release_date": config.get("model_release_date"),
            "loaded": False,  # 17C never loads weights
        }

    def validate_offline_mode(self, config: Mapping) -> list[str]:
        """Delegate to the offline blind-run policy (no web/tools/live retrieval)."""
        return validate_offline_blind_run_config(config)

    def record_checkpoint_metadata(self, config: Mapping) -> dict:
        """Return the provenance metadata the audit record needs."""
        return {
            "runner": self.runner,
            "base_model_family": config.get("base_model_family"),
            "base_model_checkpoint": config.get("base_model_checkpoint"),
            "model_release_date": config.get("model_release_date"),
            "training_cutoff": config.get("training_cutoff"),
            "model_provider": config.get("model_provider", "local"),
        }

    def generate_prediction(self, *args: object, **kwargs: object):
        if not LOCAL_MODEL_CALLS_ENABLED:
            raise AdapterDisabledError(
                f"{self.runner} adapter is DISABLED in Phase 17C — no model load, no "
                "download, no generation. Real runs arrive in Phase 17E behind an "
                "explicit flag + approval, offline against a frozen bundle."
            )
        raise AdapterDisabledError("local generation not implemented in 17C")  # unreachable


class OpenWeightModelAdapter(LocalModelAdapter):
    runner = "open_weight_generic"


class OllamaAdapter(LocalModelAdapter):
    runner = "ollama"
    requires_packages = ("ollama",)  # NOT installed/required by 17C


class VLLMAdapter(LocalModelAdapter):
    runner = "vllm"
    requires_packages = ("vllm",)  # placeholder; not required by 17C


class LlamaCppAdapter(LocalModelAdapter):
    runner = "llama_cpp"
    requires_packages = ("llama_cpp_python",)  # placeholder; not required by 17C


ADAPTERS = {
    "open_weight_generic": OpenWeightModelAdapter,
    "ollama": OllamaAdapter,
    "vllm": VLLMAdapter,
    "llama_cpp": LlamaCppAdapter,
}
