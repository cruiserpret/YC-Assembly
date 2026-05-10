"""Phase 9D — weighted society rollup over cohorts.

Aggregates cohort-level signals into a single weighted view of the
synthetic 9B society, preserving:
  - weighted final-stance distribution
  - weighted objection bucket frequency
  - weighted proof-need bucket frequency
  - social-influence classification across the population
  - resistance map (which cohorts contained the resistance personas)
  - uncertainty map (which cohorts contained the highest
    `needs_more_information` share)

Universal — never produces forecasts, never produces buy/adoption
percentages, never claims representativeness.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def build_society_rollup(
    *,
    cohort_summaries: list[dict[str, Any]],
    cohort_weights: list[float],
    persona_count: int,
) -> dict[str, Any]:
    """Combine per-cohort summaries into a single weighted-rollup dict.

    `cohort_summaries[i]` must include keys `stance_distribution`,
    `objection_summary.by_bucket`, `proof_need_summary.by_bucket`, and
    `discussion_behavior_summary.public_private_delta_distribution`.
    `cohort_weights[i]` is the cohort's share of the society
    (sum to 1.0).
    """
    if len(cohort_summaries) != len(cohort_weights):
        raise ValueError("cohort_summaries and cohort_weights must align")

    weighted_stance: dict[str, float] = {}
    weighted_obj: dict[str, float] = {}
    weighted_proof: dict[str, float] = {}
    delta_counter: dict[str, float] = {}
    resistance_by_cohort: list[dict[str, Any]] = []
    uncertainty_by_cohort: list[dict[str, Any]] = []

    for i, (s, w) in enumerate(zip(cohort_summaries, cohort_weights)):
        stance_dist = s.get("stance_distribution") or {}
        size = sum(stance_dist.values()) or 1
        for stance, n in stance_dist.items():
            if stance is None:
                continue
            weighted_stance[stance] = (
                weighted_stance.get(stance, 0.0) + w * (n / size)
            )
        obj = (s.get("objection_summary") or {}).get("by_bucket") or {}
        obj_total = sum(obj.values()) or 1
        for bucket, n in obj.items():
            weighted_obj[bucket] = (
                weighted_obj.get(bucket, 0.0) + w * (n / obj_total)
            )
        proof = (s.get("proof_need_summary") or {}).get("by_bucket") or {}
        proof_total = sum(proof.values()) or 1
        for bucket, n in proof.items():
            weighted_proof[bucket] = (
                weighted_proof.get(bucket, 0.0) + w * (n / proof_total)
            )
        deltas = (s.get("discussion_behavior_summary") or {}).get(
            "public_private_delta_distribution"
        ) or {}
        d_total = sum(deltas.values()) or 1
        for d, n in deltas.items():
            delta_counter[d] = delta_counter.get(d, 0.0) + w * (n / d_total)
        # resistance / uncertainty cohort flags
        if int(deltas.get("resistance", 0)) > 0:
            resistance_by_cohort.append({
                "cohort_index": i,
                "cohort_size": int(s.get("cohort_size", size)),
                "resistance_count": int(deltas.get("resistance", 0)),
                "resistance_share_of_cohort": round(
                    deltas.get("resistance", 0) / size, 3,
                ),
            })
        nm_share = (
            stance_dist.get("needs_more_information", 0) / size
        )
        if nm_share > 0.0:
            uncertainty_by_cohort.append({
                "cohort_index": i,
                "cohort_size": size,
                "needs_more_information_share": round(nm_share, 3),
            })

    # round / sort the weighted views
    def _rounded_sorted(d: dict[str, float]) -> dict[str, float]:
        return {
            k: round(v, 4) for k, v in sorted(
                d.items(), key=lambda kv: -kv[1],
            )
        }

    return {
        "cohort_count": len(cohort_summaries),
        "persona_count": persona_count,
        "weighted_stance_distribution": _rounded_sorted(weighted_stance),
        "weighted_objection_summary": _rounded_sorted(weighted_obj),
        "weighted_proof_need_summary": _rounded_sorted(weighted_proof),
        "social_influence_summary": _rounded_sorted(delta_counter),
        "resistance_summary": {
            "cohorts_with_resistance": len(resistance_by_cohort),
            "by_cohort": resistance_by_cohort,
        },
        "uncertainty_summary": {
            "cohorts_with_uncertainty": len(uncertainty_by_cohort),
            "by_cohort": uncertainty_by_cohort,
        },
        "caveats": [
            "Synthetic n=66 simulation. Not a forecast. Not a launch verdict.",
            "Cohorts are run-scoped + brief-scoped — never global market segments.",
            "Weighted distributions are over the simulation, not the real market.",
            "The unlaunched product has no real customer evidence.",
        ],
    }
