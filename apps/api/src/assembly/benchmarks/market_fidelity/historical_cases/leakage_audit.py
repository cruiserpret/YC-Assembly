"""Phase 17D — historical-case leakage audit.

Runs the 17C pre-outcome retrieval filter over an input bundle's evidence and reports
whether the bundle is CLEAN (no source post-dates the prediction/outcome and none
reveals the outcome). Categorizes exclusions (post-prediction / post-outcome /
postmortem / final-number / no-timestamp) and lists eligibility downgrades. Pure.
"""
from __future__ import annotations

from assembly.benchmarks.market_fidelity.historical_cases.input_bundle import InputBundle
from assembly.benchmarks.market_fidelity.historical_cases.source_manifest import (
    build_source_manifest,
)
from assembly.benchmarks.market_fidelity.retrieval_filter import (
    filter_pre_outcome_evidence,
    scan_outcome_text,
)

# The model-facing free-text bundle fields (besides evidence excerpts) that must ALSO
# be scanned for outcome leakage — they are part of what Raw/Assembly see.
_STRUCTURED_FIELDS = (
    "product_description", "target_customer", "ask_pre_outcome", "channel_context",
    "traction_signals_pre_outcome", "evidence_summary", "uncertainty_notes",
)


def run_leakage_audit(
    bundle: InputBundle, outcome_timestamp: str, *, flagged_outcome_values: list[str] | None = None
) -> dict:
    """Return a leakage-audit report for ``bundle`` against ``outcome_timestamp``.

    Audits BOTH the evidence items (timestamp + content) AND the model-facing
    structured free-text fields (product_description, traction_signals, summaries, …)
    — the whole bundle is shown to the model, so outcome text anywhere in it is a leak.
    """
    flagged = flagged_outcome_values or []
    rep = filter_pre_outcome_evidence(
        case_id=bundle.case_id,
        prediction_timestamp=bundle.prediction_timestamp,
        outcome_date=outcome_timestamp,
        sources=bundle.sources_for_filter(),
        flagged_outcome_values=flagged,
    )
    reasons: dict[str, list[str]] = dict(rep["exclusion_reasons"])

    # --- scan the structured model-facing fields for outcome leakage too ---
    structured_field_leak_flags: list[str] = []
    for field in _STRUCTURED_FIELDS:
        text = getattr(bundle, field, "") or ""
        why = scan_outcome_text(text, flagged)
        if why:
            structured_field_leak_flags.append(field)
            reasons[f"structured_field:{field}"] = why

    post_prediction_flags, post_outcome_flags = [], []
    postmortem_flags, final_number_flags, no_timestamp_flags = [], [], []
    for sid, why in reasons.items():
        joined = " ".join(why).lower()
        if "after prediction_timestamp" in joined:
            post_prediction_flags.append(sid)
        if "after outcome_date" in joined:
            post_outcome_flags.append(sid)
        if "outcome-reveal pattern" in joined:  # postmortem phrasing (regex)
            postmortem_flags.append(sid)
        if "flagged outcome value" in joined:  # an explicitly-flagged final number/value
            final_number_flags.append(sid)
        if "no parseable source timestamp" in joined or "missing/unparseable" in joined:
            no_timestamp_flags.append(sid)

    manifest = build_source_manifest(bundle.evidence_items)
    # The bundle is clean ONLY if no evidence source was excluded AND no structured
    # model-facing field leaked the outcome.
    input_bundle_clean = len(rep["excluded_source_ids"]) == 0 and not structured_field_leak_flags

    downgrades: list[str] = []
    if rep["excluded_source_ids"]:
        downgrades.append("input bundle contains excluded/leaky sources — not claim-grade until cleaned")
    if structured_field_leak_flags:
        downgrades.append(
            "structured model-facing fields reveal the outcome: " + ", ".join(structured_field_leak_flags)
        )
    if manifest["coarse_or_missing_timestamps"]:
        downgrades.append(
            "coarse/missing source timestamps reduce temporal confidence: "
            + ", ".join(manifest["coarse_or_missing_timestamps"])
        )
    if manifest.get("accessed_only_timestamps"):
        downgrades.append(
            "sources with only an accessed_at (fetch) timestamp lack publication-date proof: "
            + ", ".join(manifest["accessed_only_timestamps"])
        )

    return {
        "case_id": bundle.case_id,
        "approved_sources": rep["approved_source_ids"],
        "excluded_sources": rep["excluded_source_ids"],
        "exclusion_reasons": reasons,
        "retrieval_weight_overrides": rep["retrieval_weight_overrides"],
        "source_timestamp_confidence": manifest["timestamp_confidence"],
        "post_prediction_flags": post_prediction_flags,
        "post_outcome_flags": post_outcome_flags,
        "outcome_leakage_flags": sorted(
            set(postmortem_flags) | set(final_number_flags) | set(structured_field_leak_flags)
        ),
        "structured_field_leak_flags": structured_field_leak_flags,
        "postmortem_flags": postmortem_flags,
        "final_number_flags": final_number_flags,
        "no_timestamp_flags": no_timestamp_flags,
        "input_bundle_clean": input_bundle_clean,
        "eligibility_downgrades": downgrades,
        "evidence_bundle_hash": rep["evidence_bundle_hash"],
    }
