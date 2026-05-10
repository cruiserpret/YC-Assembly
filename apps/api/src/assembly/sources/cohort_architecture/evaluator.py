"""Phase 9D — cohort architecture quality evaluator.

10-score rubric. Universal — no LumaLoop hardcoding.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any


def _intra_cohort_distance_avg(
    cohorts: list[list[str]],
    persona_features: dict[str, dict[str, float]],
) -> float:
    """Average pairwise euclidean distance within each cohort,
    averaged across cohorts. Lower = tighter clusters."""
    totals: list[float] = []
    for c in cohorts:
        if len(c) < 2:
            continue
        pair_total = 0.0
        pair_count = 0
        for i in range(len(c)):
            for j in range(i + 1, len(c)):
                a = persona_features.get(c[i], {})
                b = persona_features.get(c[j], {})
                keys = set(a.keys()) | set(b.keys())
                d = math.sqrt(
                    sum((a.get(k, 0.0) - b.get(k, 0.0)) ** 2 for k in keys)
                )
                pair_total += d
                pair_count += 1
        if pair_count > 0:
            totals.append(pair_total / pair_count)
    return sum(totals) / len(totals) if totals else 0.0


def _inter_cohort_distance_avg(
    cohorts: list[list[str]],
    persona_features: dict[str, dict[str, float]],
) -> float:
    """Average pairwise euclidean distance between cohort centroids.
    Higher = clusters more separated."""
    if len(cohorts) < 2:
        return 0.0
    centroids = []
    for c in cohorts:
        if not c:
            continue
        keys: set = set()
        for pid in c:
            keys.update(persona_features.get(pid, {}).keys())
        cent = {}
        for k in keys:
            cent[k] = sum(
                persona_features.get(pid, {}).get(k, 0.0) for pid in c
            ) / len(c)
        centroids.append(cent)
    distances = []
    for i in range(len(centroids)):
        for j in range(i + 1, len(centroids)):
            keys = set(centroids[i].keys()) | set(centroids[j].keys())
            d = math.sqrt(sum(
                (centroids[i].get(k, 0.0) - centroids[j].get(k, 0.0)) ** 2
                for k in keys
            ))
            distances.append(d)
    return sum(distances) / len(distances) if distances else 0.0


def evaluate_cohort_architecture_quality(
    *,
    cohorts: list[list[str]],
    persona_features: dict[str, dict[str, float]],
    cohort_summaries: list[dict[str, Any]],
    society_rollup: dict[str, Any],
    pre_ballots: dict[str, dict[str, Any]],
    final_ballots: dict[str, dict[str, Any]],
    expected_persona_count: int,
    forbidden_audit: dict[str, Any],
    sensitive_audit: dict[str, Any],
    evidence_link_count: int,
    target_min_cohorts: int = 8,
    target_max_cohorts: int = 14,
) -> dict[str, Any]:
    """Compute the 10 scores + aggregate + readiness label."""
    rationale: list[str] = []

    cohort_count = len(cohorts)
    persona_assigned = sum(len(c) for c in cohorts)
    distinct_assigned = len({pid for c in cohorts for pid in c})

    # 1) clustering_quality: ratio of inter-cohort to intra-cohort
    intra = _intra_cohort_distance_avg(cohorts, persona_features)
    inter = _inter_cohort_distance_avg(cohorts, persona_features)
    if intra > 0:
        ratio = inter / intra
        clustering_quality = round(min(1.0, ratio / 2.0), 3)
    else:
        clustering_quality = 0.0
    rationale.append(
        f"clustering_quality: intra={intra:.3f} inter={inter:.3f} "
        f"ratio={inter / max(intra, 1e-6):.3f}"
    )

    # 2) traceability_score: every cohort has at least one
    # _evidence_links entry; aggregate count vs cohort count threshold.
    cohorts_with_links = sum(
        1 for s in cohort_summaries
        if (s.get("_evidence_links") or [])
    )
    if cohort_count > 0:
        coverage = cohorts_with_links / cohort_count
        avg_links = evidence_link_count / max(cohort_count, 1)
        traceability_score = round(
            min(1.0, 0.5 * coverage + 0.5 * min(1.0, avg_links / 8.0)),
            3,
        )
    else:
        traceability_score = 0.0

    # 3) psychology_preservation: per-trait stdev across the WHOLE
    # society should be retained (within 80%) when computed via the
    # cohort-mean aggregate.
    psy_per_persona: dict[str, list[float]] = {}
    for pid, vec in persona_features.items():
        for k, v in vec.items():
            if k.startswith("psy::"):
                psy_per_persona.setdefault(k, []).append(float(v))
    cohort_means: dict[str, list[float]] = {}
    for s in cohort_summaries:
        psy = s.get("psychology_summary") or {}
        for trait, summary in psy.items():
            cohort_means.setdefault(f"psy::{trait}", []).append(
                float(summary.get("mean", 0.5))
            )
    overlap_keys = set(psy_per_persona.keys()) & set(cohort_means.keys())
    if overlap_keys:
        stdev_ratios = []
        for k in overlap_keys:
            full = psy_per_persona[k]
            cm = cohort_means[k]
            if len(full) >= 2 and len(cm) >= 2:
                full_std = statistics.stdev(full)
                cohort_std = statistics.stdev(cm)
                if full_std > 1e-6:
                    stdev_ratios.append(min(1.0, cohort_std / full_std))
        psychology_preservation = round(
            sum(stdev_ratios) / len(stdev_ratios)
            if stdev_ratios else 0.0,
            3,
        )
    else:
        psychology_preservation = 0.0

    # 4) objection_preservation: how many distinct objection buckets
    # appear across all cohorts (vs total possible).
    obj_buckets = set()
    for s in cohort_summaries:
        for bucket in (
            (s.get("objection_summary") or {}).get("by_bucket") or {}
        ).keys():
            obj_buckets.add(bucket)
    objection_preservation = round(min(1.0, len(obj_buckets) / 4.0), 3)

    # 5) proof_need_preservation
    proof_buckets = set()
    for s in cohort_summaries:
        for bucket in (
            (s.get("proof_need_summary") or {}).get("by_bucket") or {}
        ).keys():
            proof_buckets.add(bucket)
    proof_need_preservation = round(min(1.0, len(proof_buckets) / 4.0), 3)

    # 6) social_influence_preservation: rollup must show >=2 distinct
    # delta classes (e.g. resistance + no_change) at non-zero share.
    sis = society_rollup.get("social_influence_summary") or {}
    distinct_deltas = sum(1 for v in sis.values() if v > 0)
    social_influence_preservation = round(min(1.0, distinct_deltas / 2.0), 3)

    # 7) non_representativeness_integrity: caveats list mentions
    # "synthetic" + "not a forecast" + "run-scoped" + "unlaunched".
    caveats = " ".join(society_rollup.get("caveats") or []).lower()
    required_keywords = ("synthetic", "not a forecast", "run-scoped", "unlaunched")
    found = sum(1 for kw in required_keywords if kw in caveats)
    non_representativeness_integrity = round(found / len(required_keywords), 3)

    # 8) anti_forecast_score
    anti_forecast = (
        1.0 if not forbidden_audit.get("any_forecast_or_verdict") else 0.0
    )
    # 9) unlaunched_product_integrity_score
    unlaunched_integrity = (
        1.0 if not forbidden_audit.get("any_fake_target_product_use")
        else 0.0
    )

    # 10) scalability_score: cohorts vs raw persona ratio. Below 12
    # cohorts for n=66 = good compression; above max blocks.
    if expected_persona_count == 0:
        scalability = 0.0
    else:
        ratio_compression = (
            1.0 - (cohort_count / max(expected_persona_count, 1))
        )
        # bonus if we hit target band
        in_band = (
            target_min_cohorts <= cohort_count <= target_max_cohorts
        )
        scalability = round(
            min(1.0, ratio_compression + (0.05 if in_band else 0.0)),
            3,
        )

    aggregate = round((
        0.10 * clustering_quality
        + 0.15 * traceability_score
        + 0.10 * psychology_preservation
        + 0.10 * objection_preservation
        + 0.10 * proof_need_preservation
        + 0.10 * social_influence_preservation
        + 0.10 * non_representativeness_integrity
        + 0.10 * anti_forecast
        + 0.10 * unlaunched_integrity
        + 0.05 * scalability
    ), 3)

    critical_pass = (
        anti_forecast == 1.0
        and unlaunched_integrity == 1.0
        and target_min_cohorts <= cohort_count <= target_max_cohorts
        and persona_assigned == expected_persona_count
        and distinct_assigned == expected_persona_count
        and traceability_score >= 0.8
        and scalability >= 0.7
        and not sensitive_audit.get("any_sensitive_inference", False)
    )
    if critical_pass:
        ready_state = "READY_FOR_HUGE_SOCIETY_ARCHITECTURE"
    elif aggregate >= 0.55:
        ready_state = "READY_FOR_COHORT_FIX"
    else:
        ready_state = "NOT_READY"

    rationale.append(
        f"aggregate={aggregate} clustering={clustering_quality} "
        f"traceability={traceability_score} "
        f"psy_pres={psychology_preservation} "
        f"obj={objection_preservation} proof={proof_need_preservation} "
        f"social={social_influence_preservation} "
        f"non_rep={non_representativeness_integrity} "
        f"anti_forecast={anti_forecast} unlaunched={unlaunched_integrity} "
        f"scalability={scalability}"
    )

    return {
        "clustering_quality_score": clustering_quality,
        "traceability_score": traceability_score,
        "psychology_preservation_score": psychology_preservation,
        "objection_preservation_score": objection_preservation,
        "proof_need_preservation_score": proof_need_preservation,
        "social_influence_preservation_score": (
            social_influence_preservation
        ),
        "non_representativeness_integrity_score": (
            non_representativeness_integrity
        ),
        "anti_forecast_score": anti_forecast,
        "unlaunched_product_integrity_score": unlaunched_integrity,
        "scalability_score": scalability,
        "aggregate_score": aggregate,
        "ready_state": ready_state,
        "cohort_count": cohort_count,
        "personas_assigned": persona_assigned,
        "distinct_personas_assigned": distinct_assigned,
        "rationale": rationale,
    }
