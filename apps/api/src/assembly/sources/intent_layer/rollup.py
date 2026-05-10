"""Phase 9E — intent rollup builder.

Aggregates per-persona simulated intents into a society-wide rollup
keyed by intent_label, switching_status, and cohort.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def build_intent_rollup(
    *,
    intents: list[dict[str, Any]],
    cohort_id_to_label: dict[str, str],
    cohort_id_to_size: dict[str, int],
    cohort_count: int,
) -> dict[str, Any]:
    """Compute a structured rollup over the per-persona intent list.

    `intents[i]` keys: persona_id, cohort_id, simulated_intent,
    intent_strength, switching_status, confidence, current_alternative.
    """
    intent_counter = Counter(
        i.get("simulated_intent") for i in intents
    )
    switching_counter = Counter(
        i.get("switching_status") for i in intents
    )
    intent_by_cohort: dict[str, dict[str, int]] = {}
    high_intent: list[dict[str, Any]] = []
    rejection: list[dict[str, Any]] = []
    for intent in intents:
        cid = intent.get("cohort_id")
        if cid:
            label = cohort_id_to_label.get(cid, cid[:8])
            bucket = intent_by_cohort.setdefault(label, {})
            bucket[intent["simulated_intent"]] = (
                bucket.get(intent["simulated_intent"], 0) + 1
            )
    # high intent personas: would_buy_now / would_try_once / would_join_waitlist
    for intent in intents:
        if intent.get("simulated_intent") in (
            "would_buy_now", "would_try_once", "would_join_waitlist",
            "would_share_with_friend",
        ):
            high_intent.append({
                "persona_id": intent["persona_id"],
                "intent": intent["simulated_intent"],
                "strength": intent.get("intent_strength"),
                "confidence": intent.get("confidence"),
                "cohort_id": intent.get("cohort_id"),
                "cohort_label": cohort_id_to_label.get(
                    intent.get("cohort_id"), None,
                ),
            })
        if intent.get("simulated_intent") in (
            "would_reject", "would_block", "loyal_to_current_alternative",
        ):
            rejection.append({
                "persona_id": intent["persona_id"],
                "intent": intent["simulated_intent"],
                "strength": intent.get("intent_strength"),
                "confidence": intent.get("confidence"),
                "cohort_id": intent.get("cohort_id"),
                "cohort_label": cohort_id_to_label.get(
                    intent.get("cohort_id"), None,
                ),
                "reason": intent.get("reason_for_rejection"),
            })
    return {
        "persona_count": len(intents),
        "cohort_count": cohort_count,
        "intent_distribution": dict(intent_counter),
        "intent_by_cohort": intent_by_cohort,
        "switching_status_distribution": dict(switching_counter),
        "high_intent_segments": high_intent,
        "strongest_rejection_segments": rejection,
        "caveats": [
            "Simulated intent labels — NOT real-world purchase forecasts.",
            "n=66 synthetic run-scoped society. Not a market signal.",
            "Cohorts are run-scoped; not transferable to other briefs.",
            "Personas have not bought/used/owned the unlaunched product.",
        ],
    }
