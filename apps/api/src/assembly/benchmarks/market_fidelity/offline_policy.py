"""Phase 17C — offline blind-run execution policy.

For an OFFLINE blind backtest, a run may use ONLY a frozen, approved, pre-outcome
input bundle: no live web, no tools, no live retrieval. RAG is allowed ONLY when it
is restricted to the frozen approved evidence bundle. This validator hard-fails any
config that could leak post-outcome information into the run. Pure; no network.
"""
from __future__ import annotations

from collections.abc import Mapping


def validate_offline_blind_run_config(config: Mapping) -> list[str]:
    """Return BLOCKING issues for an offline blind-run config (empty == ok).

    Fails if: web_enabled / live_retrieval / tools enabled; rag_enabled without a
    frozen approved evidence bundle; the input bundle lacks per-source timestamps; or
    outcome_date / prediction_timestamp / model metadata are missing.
    """
    issues: list[str] = []

    def _truthy(key: str) -> bool:
        return bool(config.get(key))

    if _truthy("web_enabled"):
        issues.append("web_enabled must be false for an offline blind run")
    if _truthy("live_retrieval"):
        issues.append("live_retrieval must be false for an offline blind run")
    if _truthy("tools_enabled"):
        issues.append("tools_enabled must be false for an offline blind run")
    # RAG only allowed over a FROZEN approved evidence bundle (no live store). The
    # protective flag must be STRICTLY True (a truthy string like 'false'/'no' from
    # YAML/JSON must NOT disable the restriction — fail closed).
    if _truthy("rag_enabled") and config.get("frozen_evidence_bundle_only") is not True:
        issues.append(
            "rag_enabled is only permitted with frozen_evidence_bundle_only=true (boolean) "
            "— a frozen, approved, pre-outcome evidence bundle; live RAG is leakage"
        )

    if not config.get("prediction_timestamp"):
        issues.append("prediction_timestamp is required")
    if not config.get("outcome_date"):
        issues.append("outcome_date is required")

    # Model provenance metadata must be present.
    model = config.get("model_metadata") or {}
    if not isinstance(model, Mapping) or not model.get("base_model_checkpoint"):
        issues.append("model_metadata.base_model_checkpoint is required (model provenance)")

    # The input bundle must carry per-source timestamps so the leakage filter can run.
    bundle = config.get("input_bundle") or {}
    sources = bundle.get("sources") if isinstance(bundle, Mapping) else None
    if not isinstance(sources, list) or not sources:
        issues.append("input_bundle.sources is required (with per-source timestamps)")
    else:
        missing_ts: list[str] = []
        for i, s in enumerate(sources):
            if not isinstance(s, Mapping):
                missing_ts.append(f"item_{i}_is_not_an_object")
                continue
            if not (s.get("published_at") or s.get("archived_at") or s.get("retrieved_at")):
                missing_ts.append(str(s.get("id") or s.get("url") or i))
        if missing_ts:
            issues.append(
                "every input_bundle source must be an object with a timestamp "
                "(published_at / archived_at / retrieved_at); invalid/missing for: "
                + ", ".join(missing_ts)
            )

    return issues


def is_offline_blind_ok(config: Mapping) -> bool:
    return not validate_offline_blind_run_config(config)
