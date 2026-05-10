"""Phase 9A.4 — discussion quality evaluator.

12-score rubric. Universal — no LumaLoop hardcoding.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


_ALLOWED_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)


@dataclass
class DiscussionQualityScores:
    grounding_score: float
    interaction_score: float
    disagreement_score: float
    non_overcooperation_score: float
    memory_integrity_score: float
    private_public_distinction_score: float
    social_influence_score: float
    stance_validity_score: float
    anti_forecast_score: float
    unlaunched_product_integrity_score: float
    profile_usage_score: float
    caveat_integrity_score: float
    aggregate_score: float
    ready_state: str
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _safe_div(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return float(a) / float(b)


def evaluate_discussion_quality(
    *,
    turns: list[dict[str, Any]],
    pre_ballots: list[dict[str, Any]],
    final_ballots: list[dict[str, Any]],
    memory_atoms: list[dict[str, Any]],
    forbidden_audit: dict[str, Any],
    sensitive_audit: dict[str, Any],
    overcooperation: dict[str, Any],
    expected_persona_count: int,
) -> DiscussionQualityScores:
    """Compute the 12 scores + aggregate + readiness label.

    Each `turns` dict expects:
      {speaker_persona_id, turn_type, public_text, stance,
       referenced_turn_ids, referenced_memory_atom_ids,
       psychology_control_snapshot}

    Each ballot dict expects:
      {persona_id, ballot_stage, private_stance, private_reasoning,
       confidence, public_private_delta}

    Each memory_atom dict expects:
      {origin_type, origin_ref_id, origin_excerpt, persona_id}
    """
    rationale: list[str] = []

    # --- 1) grounding_score: turns reference memory_atom_ids OR
    # carry psychology_control_snapshot
    grounded = sum(
        1 for t in turns
        if (t.get("referenced_memory_atom_ids") or [])
        or (t.get("referenced_source_record_ids") or [])
        or (t.get("psychology_control_snapshot") or {})
    )
    grounding_score = round(_safe_div(grounded, len(turns)), 3)

    # --- 2) interaction_score: peer_response turns reference at least
    # one prior turn id. peer_response is the round explicitly designed
    # for cross-persona interaction; proof_discussion is by design more
    # of a self-statement and is excluded from this metric.
    peer_rounds = [
        t for t in turns if t.get("turn_type") == "peer_response"
    ]
    if peer_rounds:
        peer_with_ref = sum(
            1 for t in peer_rounds
            if (t.get("referenced_turn_ids") or [])
        )
        interaction_score = round(
            _safe_div(peer_with_ref, len(peer_rounds)), 3,
        )
    else:
        any_ref = sum(
            1 for t in turns
            if (t.get("referenced_turn_ids") or [])
        )
        interaction_score = round(_safe_div(any_ref, max(len(turns), 1)), 3)

    # --- 3) disagreement_score: stance variance across turns
    stances = [t.get("stance") for t in turns if t.get("stance")]
    distinct_stances = len(set(stances))
    disagreement_score = round(min(1.0, distinct_stances / 3.0), 3)

    # --- 4) non_overcooperation_score
    non_overcooperation_score = (
        0.0 if overcooperation.get("flag") else 1.0
    )
    if overcooperation.get("flag"):
        rationale.append(
            "over-cooperation flagged: "
            f"{overcooperation.get('warning', '')}"
        )

    # --- 5) memory_integrity_score: every atom has origin + excerpt
    valid_atoms = sum(
        1 for a in memory_atoms
        if (a.get("origin_type") and a.get("origin_ref_id")
            and a.get("origin_excerpt"))
    )
    memory_integrity_score = round(
        _safe_div(valid_atoms, max(len(memory_atoms), 1)), 3,
    )

    # --- 6) private_public_distinction_score: ballots exist + stages
    pre_count = sum(
        1 for b in pre_ballots if b.get("ballot_stage") == "pre"
    )
    final_count = sum(
        1 for b in final_ballots if b.get("ballot_stage") == "final"
    )
    private_public_distinction_score = round(min(
        1.0,
        (
            (1.0 if pre_count >= expected_persona_count else
             pre_count / max(expected_persona_count, 1))
            + (1.0 if final_count >= expected_persona_count else
               final_count / max(expected_persona_count, 1))
        ) / 2.0,
    ), 3)

    # --- 7) social_influence_score: % of final ballots with
    # public_private_delta != 'no_change' must be > 0 but not == 100%
    if not final_ballots:
        social_influence_score = 0.0
    else:
        deltas = [
            b.get("public_private_delta") or "no_change"
            for b in final_ballots
        ]
        non_zero = sum(1 for d in deltas if d != "no_change")
        ratio = non_zero / max(len(deltas), 1)
        # ideal is ~0.2-0.7 — convert distance to that band into score
        if ratio == 0.0:
            social_influence_score = 0.0
        elif ratio >= 0.95:
            social_influence_score = 0.4
        else:
            social_influence_score = round(min(1.0, 0.5 + ratio), 3)

    # --- 8) stance_validity_score: every stance is in ALLOWED set
    bad_stances = sum(
        1 for t in turns
        if t.get("stance") and t["stance"] not in _ALLOWED_STANCES
    )
    bad_stances += sum(
        1 for b in (*pre_ballots, *final_ballots)
        if b.get("private_stance") not in _ALLOWED_STANCES
    )
    stance_validity_score = (
        1.0 if bad_stances == 0 else round(max(
            0.0, 1.0 - bad_stances / max(len(turns) + len(pre_ballots) + len(final_ballots), 1),
        ), 3)
    )

    # --- 9) anti_forecast_score
    anti_forecast_score = (
        1.0 if not forbidden_audit.get("any_forecast_or_verdict") else 0.0
    )

    # --- 10) unlaunched_product_integrity_score
    unlaunched_product_integrity_score = (
        1.0 if not forbidden_audit.get("any_fake_target_product_use") else 0.0
    )

    # --- 11) profile_usage_score: turns carry a non-empty
    # psychology_control_snapshot
    snap_count = sum(
        1 for t in turns if t.get("psychology_control_snapshot")
    )
    profile_usage_score = round(
        _safe_div(snap_count, max(len(turns), 1)), 3,
    )

    # --- 12) caveat_integrity_score: at least one caveat-bearing
    # ballot reasoning per persona
    if final_ballots:
        with_caveat = sum(
            1 for b in final_ballots
            if any(
                kw in (b.get("private_reasoning") or "").lower()
                for kw in (
                    "synthetic", "simulation", "not representative",
                    "n=30", "small sample", "unlaunched", "not a forecast",
                    "this run",
                )
            )
        )
        caveat_integrity_score = round(
            _safe_div(with_caveat, len(final_ballots)), 3,
        )
    else:
        caveat_integrity_score = 0.0

    aggregate = round((
        0.10 * grounding_score
        + 0.10 * interaction_score
        + 0.07 * disagreement_score
        + 0.10 * non_overcooperation_score
        + 0.10 * memory_integrity_score
        + 0.10 * private_public_distinction_score
        + 0.08 * social_influence_score
        + 0.07 * stance_validity_score
        + 0.10 * anti_forecast_score
        + 0.10 * unlaunched_product_integrity_score
        + 0.05 * profile_usage_score
        + 0.03 * caveat_integrity_score
    ), 3)

    critical_pass = (
        anti_forecast_score == 1.0
        and unlaunched_product_integrity_score == 1.0
        and memory_integrity_score >= 0.95
        and private_public_distinction_score >= 0.95
        and interaction_score >= 0.7
        and profile_usage_score >= 0.7
        and non_overcooperation_score >= 0.7
        and stance_validity_score >= 0.95
    )
    if critical_pass:
        ready_state = "READY_FOR_DISCUSSION_REPORT"
    elif aggregate >= 0.6:
        ready_state = "READY_FOR_PROMPT_FIX"
    else:
        ready_state = "NOT_READY"

    rationale.append(
        f"aggregate={aggregate} (anti_forecast={anti_forecast_score}, "
        f"unlaunched={unlaunched_product_integrity_score}, "
        f"memory={memory_integrity_score}, "
        f"private_public={private_public_distinction_score}, "
        f"interaction={interaction_score}, "
        f"profile_usage={profile_usage_score}, "
        f"non_overcooperation={non_overcooperation_score}, "
        f"stance_validity={stance_validity_score})"
    )

    return DiscussionQualityScores(
        grounding_score=grounding_score,
        interaction_score=interaction_score,
        disagreement_score=disagreement_score,
        non_overcooperation_score=non_overcooperation_score,
        memory_integrity_score=memory_integrity_score,
        private_public_distinction_score=private_public_distinction_score,
        social_influence_score=social_influence_score,
        stance_validity_score=stance_validity_score,
        anti_forecast_score=anti_forecast_score,
        unlaunched_product_integrity_score=(
            unlaunched_product_integrity_score
        ),
        profile_usage_score=profile_usage_score,
        caveat_integrity_score=caveat_integrity_score,
        aggregate_score=aggregate,
        ready_state=ready_state,
        rationale=rationale,
    )


# -----------------------------------------------------------------------
# Phase 9B — scaled (50-100 persona) discussion evaluator with three
# additional scores: scale_reliability, reflection_completeness,
# cost_efficiency.
# -----------------------------------------------------------------------


def evaluate_scaled_discussion_quality(
    *,
    base_scores: DiscussionQualityScores,
    expected_persona_count: int,
    persisted_persona_count: int,
    expected_reflection_count: int,
    persisted_reflection_count: int,
    expected_pre_ballot_count: int,
    persisted_pre_ballot_count: int,
    expected_final_ballot_count: int,
    persisted_final_ballot_count: int,
    expected_call_count: int,
    actual_call_count: int,
    failed_call_count: int,
    transient_retry_count: int,
    cost_hard_cap_usd: float,
    estimated_cost_usd: float,
) -> dict[str, Any]:
    """Compute the three Phase-9B-specific scores + an aggregate that
    blends them with the 12-dim base evaluator. Returns a flat dict that
    the orchestrator embeds as `quality_scores`.
    """

    # 13) scale_reliability_score: percentage of expected ballots actually
    # persisted, with a bonus credit for retries that recovered failures.
    expected_ballots = (
        expected_pre_ballot_count + expected_final_ballot_count
        + expected_reflection_count
    )
    persisted_ballots = (
        persisted_pre_ballot_count + persisted_final_ballot_count
        + persisted_reflection_count
    )
    if expected_ballots <= 0:
        scale_reliability_score = 0.0
    else:
        scale_reliability_score = round(
            persisted_ballots / expected_ballots, 3,
        )

    # 14) reflection_completeness_score: pure ratio of reflection ballots
    # actually persisted to the persona count. The spec demands >= 95%.
    if expected_reflection_count <= 0:
        reflection_completeness_score = 0.0
    else:
        reflection_completeness_score = round(
            persisted_reflection_count / expected_reflection_count, 3,
        )

    # 15) cost_efficiency_score: penalize large overages of the call
    # estimate (run-away costs) and reward staying under the hard cap.
    if expected_call_count <= 0 or actual_call_count <= 0:
        cost_efficiency_score = 0.0
    else:
        ratio = actual_call_count / expected_call_count
        if ratio <= 1.10:
            base_eff = 1.0
        elif ratio <= 1.30:
            base_eff = 0.85
        elif ratio <= 1.50:
            base_eff = 0.70
        else:
            base_eff = 0.5
        if cost_hard_cap_usd > 0:
            within_cap = estimated_cost_usd <= cost_hard_cap_usd
            cap_penalty = 0.0 if within_cap else 0.3
        else:
            cap_penalty = 0.0
        cost_efficiency_score = round(max(0.0, base_eff - cap_penalty), 3)

    base_dict = base_scores.to_dict()
    extended_aggregate = round(
        0.85 * float(base_dict["aggregate_score"])
        + 0.05 * scale_reliability_score
        + 0.05 * reflection_completeness_score
        + 0.05 * cost_efficiency_score,
        3,
    )
    base_critical_pass = base_dict["ready_state"] == "READY_FOR_DISCUSSION_REPORT"
    scale_critical_pass = (
        scale_reliability_score >= 0.95
        and reflection_completeness_score >= 0.95
        and cost_efficiency_score >= 0.7
    )
    if base_critical_pass and scale_critical_pass:
        ready_state = "READY_FOR_DISCUSSION_REPORT"
    elif base_dict["ready_state"] != "NOT_READY":
        ready_state = "READY_FOR_PROMPT_FIX"
    else:
        ready_state = "NOT_READY"

    out = dict(base_dict)
    out["scale_reliability_score"] = scale_reliability_score
    out["reflection_completeness_score"] = reflection_completeness_score
    out["cost_efficiency_score"] = cost_efficiency_score
    out["aggregate_score"] = extended_aggregate
    out["ready_state"] = ready_state
    out["rationale"] = list(base_dict.get("rationale") or [])
    out["rationale"].append(
        f"scale_reliability={scale_reliability_score} "
        f"reflection_completeness={reflection_completeness_score} "
        f"cost_efficiency={cost_efficiency_score} "
        f"persisted_personas={persisted_persona_count}/{expected_persona_count} "
        f"calls={actual_call_count}/{expected_call_count} "
        f"transient_retries={transient_retry_count} "
        f"failed_calls={failed_call_count} "
        f"est_cost=${estimated_cost_usd:.2f}/cap=${cost_hard_cap_usd:.2f}"
    )
    return out
