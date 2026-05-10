"""Phase 9E — intent + debate quality evaluator.

10-score rubric. Universal — no LumaLoop hardcoding.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


_ALLOWED_INTENTS = (
    "would_buy_now", "would_try_once", "would_join_waitlist",
    "would_consider_if_proven", "would_share_with_friend",
    "would_compare_to_current_brand", "loyal_to_current_alternative",
    "would_reject", "would_block",
)
_ALLOWED_RESPONSE_TYPES = (
    "adopted", "resisted", "modified", "ignored", "intensified",
)
_ALLOWED_EFFECTS = (
    "increased_intent", "decreased_intent",
    "no_change", "increased_uncertainty",
)


def _safe_div(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return float(a) / float(b)


def evaluate_intent_and_debate_quality(
    *,
    intents: list[dict[str, Any]],
    arguments: list[dict[str, Any]],
    propagations: list[dict[str, Any]],
    forbidden_audit: dict[str, Any],
    sensitive_audit: dict[str, Any],
    expected_persona_count: int,
    cohort_count: int,
) -> dict[str, Any]:
    rationale: list[str] = []

    # 1) intent_coverage_score: every persona has exactly one intent
    intent_persona_ids = [i.get("persona_id") for i in intents]
    distinct = len(set(intent_persona_ids))
    coverage = (
        1.0 if (distinct == expected_persona_count
                and len(intents) == expected_persona_count)
        else round(_safe_div(distinct, expected_persona_count), 3)
    )

    # 2) intent_label_validity_score: every intent in allowed set
    bad_labels = sum(
        1 for i in intents
        if i.get("simulated_intent") not in _ALLOWED_INTENTS
    )
    label_validity = (
        1.0 if bad_labels == 0 else round(
            max(0.0, 1.0 - bad_labels / max(len(intents), 1)), 3,
        )
    )

    # 3) intent_evidence_score: every intent has non-empty evidence_basis
    with_basis = sum(
        1 for i in intents if (i.get("evidence_basis") or "").strip()
    )
    intent_evidence = round(_safe_div(with_basis, max(len(intents), 1)), 3)

    # 4) intent_caveat_score: every intent has a non-empty caveat
    # mentioning "synthetic" / "simulation" / "not a forecast"
    with_caveat = sum(
        1 for i in intents
        if any(
            kw in (i.get("caveat") or "").lower()
            for kw in (
                "synthetic", "simulation", "not a real-world",
                "not a forecast",
            )
        )
    )
    caveat_score = round(_safe_div(with_caveat, max(len(intents), 1)), 3)

    # 5) intent_diversity_score: at least 3 distinct intent labels
    distinct_intents = len(set(intent_persona_ids and [
        i.get("simulated_intent") for i in intents
    ]))
    intent_diversity = round(min(1.0, distinct_intents / 3.0), 3)

    # 6) argument_traceability_score: every argument has origin_ref_id
    with_origin = sum(
        1 for a in arguments
        if (a.get("origin_ref_id") or "").strip()
    )
    arg_traceability = round(
        _safe_div(with_origin, max(len(arguments), 1)), 3,
    )

    # 7) propagation_validity_score: every propagation has closed-set
    # response_type + effect_on_intent + non-empty evidence
    bad_props = sum(
        1 for p in propagations
        if (
            p.get("response_type") not in _ALLOWED_RESPONSE_TYPES
            or p.get("effect_on_intent") not in _ALLOWED_EFFECTS
            or not (p.get("evidence_basis") or "").strip()
        )
    )
    propagation_validity = (
        1.0 if bad_props == 0 else round(
            max(0.0, 1.0 - bad_props / max(len(propagations), 1)), 3,
        )
    )

    # 8) anti_forecast_score
    anti_forecast = (
        1.0 if not forbidden_audit.get("any_forecast_or_verdict") else 0.0
    )
    # 9) unlaunched_product_integrity_score
    unlaunched_integrity = (
        1.0 if not forbidden_audit.get("any_fake_target_product_use")
        else 0.0
    )

    # 10) propagation_coverage_score: at least one propagation per arg ×
    # (cohort_count - 1).
    expected_props = len(arguments) * max(cohort_count - 1, 0)
    propagation_coverage = (
        round(_safe_div(len(propagations), max(expected_props, 1)), 3)
        if expected_props > 0 else 0.0
    )

    aggregate = round((
        0.10 * coverage
        + 0.10 * label_validity
        + 0.10 * intent_evidence
        + 0.10 * caveat_score
        + 0.05 * intent_diversity
        + 0.10 * arg_traceability
        + 0.10 * propagation_validity
        + 0.10 * propagation_coverage
        + 0.15 * anti_forecast
        + 0.10 * unlaunched_integrity
    ), 3)

    critical_pass = (
        anti_forecast == 1.0
        and unlaunched_integrity == 1.0
        and coverage >= 1.0
        and label_validity >= 0.95
        and intent_evidence >= 0.95
        and caveat_score >= 0.95
        and arg_traceability >= 0.95
        and propagation_validity >= 0.95
        and not sensitive_audit.get("any_sensitive_inference", False)
    )
    if critical_pass:
        ready_state = "READY_FOR_PHASE_10A"
    elif aggregate >= 0.6:
        ready_state = "READY_FOR_INTENT_FIX"
    else:
        ready_state = "NOT_READY"

    rationale.append(
        f"aggregate={aggregate} coverage={coverage} "
        f"label_validity={label_validity} intent_evidence={intent_evidence} "
        f"caveat={caveat_score} diversity={intent_diversity} "
        f"arg_trace={arg_traceability} prop_valid={propagation_validity} "
        f"prop_cov={propagation_coverage} anti_forecast={anti_forecast} "
        f"unlaunched={unlaunched_integrity}"
    )

    return {
        "intent_coverage_score": coverage,
        "intent_label_validity_score": label_validity,
        "intent_evidence_score": intent_evidence,
        "intent_caveat_score": caveat_score,
        "intent_diversity_score": intent_diversity,
        "argument_traceability_score": arg_traceability,
        "propagation_validity_score": propagation_validity,
        "anti_forecast_score": anti_forecast,
        "unlaunched_product_integrity_score": unlaunched_integrity,
        "propagation_coverage_score": propagation_coverage,
        "aggregate_score": aggregate,
        "ready_state": ready_state,
        "rationale": rationale,
    }
