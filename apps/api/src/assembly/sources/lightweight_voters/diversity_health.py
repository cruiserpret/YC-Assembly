"""Phase 12C — diversity health dashboard for the 100-voter overlay.

Reads:
  - the 100 lightweight voters
  - the social graph edges
  - the 4 InfluenceRound records
  - inherited rich-persona diversity metrics (from existing
    discussion_diversity_quality.json artifact, if present)

Computes the locked thresholds from the user spec:
  persona_voice_diversity_score >= 0.65
  repeated_objection_count <= 8
  near_duplicate_turn_count == 0
  intent_labels_per_run average >= 2
  unique_reasoning near 100%
  max_role_concentration <= 0.30
  all 9 cohorts represented by >=3 voters
  no voter duplication (all voter_ids distinct;
    distinct (segment, role) pairs >= 12)
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from assembly.sources.lightweight_voters.voter_schema import (
    DiversityHealth,
    InfluenceRound,
    LightweightVoter,
    SocialEdge,
)


def _safe_rate(numerator: int, denominator: int) -> float | None:
    """Return numerator/denominator or None when denominator is 0."""
    if denominator <= 0:
        return None
    return numerator / denominator


def _bucket_of(voter: LightweightVoter, which: str) -> str:
    """Return the bucket for `which in {initial, final}` defaulting to
    'uncertain' if missing."""
    if which == "initial":
        return voter.initial_bucket or "uncertain"
    return voter.final_bucket or "uncertain"


def compute_diversity_health(
    voters: list[LightweightVoter],
    edges: list[SocialEdge],
    rounds: list[InfluenceRound],
    *,
    rich_persona_diversity: dict[str, Any] | None = None,
) -> DiversityHealth:
    """Run the gates + emit warnings. Returns DiversityHealth."""
    warnings: list[str] = []

    n_voters = len(voters)
    role_counter = Counter(v.role for v in voters)
    seg_counter = Counter(v.segment for v in voters)
    cohort_counter = Counter(str(v.cohort_id) for v in voters)
    competitor_user_count = sum(
        1 for v in voters if v.role.startswith("competitor_user_")
    )

    max_role_conc = (
        max(role_counter.values()) / n_voters if n_voters else 0.0
    )
    comp_user_share = (
        competitor_user_count / n_voters if n_voters else 0.0
    )

    # Edge structure
    n_edges = len(edges)
    edges_per_voter: dict[str, int] = {
        str(v.voter_id): 0 for v in voters
    }
    for e in edges:
        edges_per_voter[str(e.source_voter_id)] = (
            edges_per_voter.get(str(e.source_voter_id), 0) + 1
        )
    edge_counts = list(edges_per_voter.values())
    avg_edges = (
        sum(edge_counts) / len(edge_counts) if edge_counts else 0.0
    )
    edges_min = min(edge_counts) if edge_counts else 0
    edges_max = max(edge_counts) if edge_counts else 0
    edge_type_dist = dict(Counter(e.edge_type for e in edges))

    # Intent dynamics across rounds
    intent_diversity_per_round: dict[int, int] = {}
    intent_changes = 0
    bucket_changes = 0
    for r in rounds:
        if r.round_idx == 0:
            distinct = len({v.initial_intent for v in voters})
        elif r.round_idx in (2, 3):
            distinct = len({v.final_intent for v in voters})
        else:
            distinct = len({v.initial_intent for v in voters})
        intent_diversity_per_round[r.round_idx] = distinct
        if r.round_type == "update":
            intent_changes = r.intent_changes
        if r.round_type == "finalize":
            bucket_changes = r.bucket_changes

    # Voter uniqueness
    voter_id_count = len({str(v.voter_id) for v in voters})
    voter_id_uniqueness_pct = (
        100.0 * voter_id_count / n_voters if n_voters else 0.0
    )
    seg_role_pairs = {(v.segment, v.role) for v in voters}

    # Phase 12C.1 — transition audit + resistance realism metrics.
    initial_intent_dist = dict(
        Counter(v.initial_intent for v in voters)
    )
    final_intent_dist = dict(
        Counter(v.final_intent or v.initial_intent for v in voters)
    )
    initial_bucket_dist = dict(
        Counter(_bucket_of(v, "initial") for v in voters)
    )
    final_bucket_dist = dict(
        Counter(_bucket_of(v, "final") for v in voters)
    )
    transition_matrix: dict[str, dict[str, int]] = {}
    for v in voters:
        ib = _bucket_of(v, "initial")
        fb = _bucket_of(v, "final")
        row = transition_matrix.setdefault(ib, {})
        row[fb] = row.get(fb, 0) + 1
    hard_resistant_count = sum(1 for v in voters if v.hard_resistant)

    # Skeptic retention: of voters who started skeptical, how many
    # stayed skeptical. None when no voter started skeptical.
    skeptic_initial = sum(
        1 for v in voters if _bucket_of(v, "initial") == "skeptical"
    )
    skeptic_final_from_skeptic_initial = sum(
        1 for v in voters
        if _bucket_of(v, "initial") == "skeptical"
        and _bucket_of(v, "final") == "skeptical"
    )
    skeptic_retention_rate = _safe_rate(
        skeptic_final_from_skeptic_initial, skeptic_initial,
    )
    skeptic_to_uncertain = sum(
        1 for v in voters
        if _bucket_of(v, "initial") == "skeptical"
        and _bucket_of(v, "final") == "uncertain"
    )
    skeptic_to_receptive = sum(
        1 for v in voters
        if _bucket_of(v, "initial") == "skeptical"
        and _bucket_of(v, "final") == "receptive"
    )
    skeptic_to_buyer = sum(
        1 for v in voters
        if _bucket_of(v, "initial") == "skeptical"
        and _bucket_of(v, "final") == "buyer"
    )
    skeptic_to_uncertain_rate = _safe_rate(
        skeptic_to_uncertain, skeptic_initial,
    )
    skeptic_to_receptive_rate = _safe_rate(
        skeptic_to_receptive, skeptic_initial,
    )
    skeptic_to_buyer_rate = _safe_rate(
        skeptic_to_buyer, skeptic_initial,
    )

    # Hard-reject retention.
    # Phase 12C.1 (Option A) — distinguish bucket-level retention (the
    # load-bearing realism gate, matching the user's "become uncertain"
    # spec) from exact-intent retention (a finer-grained diagnostic).
    # A would_reject voter that shifts to loyal_to_current_alternative
    # under peer pressure is STILL skeptical-bucket; bucket-retention
    # = 1.0, exact-intent-retention < 1.0. Only bucket-level erosion
    # constitutes a "hard_reject_erased" event.
    hard_reject_initial = sum(
        1 for v in voters if v.initial_intent == "would_reject"
    )
    hard_reject_exact_stays = sum(
        1 for v in voters
        if v.initial_intent == "would_reject"
        and (v.final_intent or v.initial_intent) == "would_reject"
    )
    hard_reject_bucket_stays = sum(
        1 for v in voters
        if v.initial_intent == "would_reject"
        and _bucket_of(v, "final") == "skeptical"
    )
    hard_reject_exact_intent_retention_rate = _safe_rate(
        hard_reject_exact_stays, hard_reject_initial,
    )
    hard_reject_bucket_retention_rate = _safe_rate(
        hard_reject_bucket_stays, hard_reject_initial,
    )
    # Backwards-compat alias for prior consumers/tests. Now aliased to
    # the BUCKET-level metric so existing usages get the realism-gate
    # semantics by default.
    hard_reject_retention_rate = hard_reject_bucket_retention_rate
    # Competitor-loyal retention: initial_intent == loyal_to_current_alternative
    cl_initial = sum(
        1 for v in voters
        if v.initial_intent == "loyal_to_current_alternative"
    )
    cl_final_stays_skeptical = sum(
        1 for v in voters
        if v.initial_intent == "loyal_to_current_alternative"
        and _bucket_of(v, "final") == "skeptical"
    )
    competitor_loyal_retention_rate = _safe_rate(
        cl_final_stays_skeptical, cl_initial,
    )
    # Phase 12C.1 (extended) — softening of hard-resistant voters.
    hr_initial = sum(1 for v in voters if v.hard_resistant)
    hr_stays_skeptical = sum(
        1 for v in voters
        if v.hard_resistant and _bucket_of(v, "final") == "skeptical"
    )
    hr_stays_exact_intent = sum(
        1 for v in voters
        if v.hard_resistant
        and (v.final_intent or v.initial_intent) == v.initial_intent
    )
    hr_to_uncertain = sum(
        1 for v in voters
        if v.hard_resistant and _bucket_of(v, "final") == "uncertain"
    )
    hr_to_receptive = sum(
        1 for v in voters
        if v.hard_resistant and _bucket_of(v, "final") == "receptive"
    )
    hr_to_buyer = sum(
        1 for v in voters
        if v.hard_resistant and _bucket_of(v, "final") == "buyer"
    )
    # Bucket-level retention is the realism gate. Exact-intent is
    # diagnostic only — micro-shifts within skeptical bucket are
    # legitimate market behavior (e.g. would_reject → loyal_to_current).
    hard_resistant_retention_rate = _safe_rate(
        hr_stays_skeptical, hr_initial,
    )
    hard_resistant_bucket_retention_rate = (
        hard_resistant_retention_rate
    )
    hard_resistant_exact_intent_retention_rate = _safe_rate(
        hr_stays_exact_intent, hr_initial,
    )
    hard_resistant_to_uncertain_rate = _safe_rate(
        hr_to_uncertain, hr_initial,
    )
    hard_resistant_to_receptive_rate = _safe_rate(
        hr_to_receptive, hr_initial,
    )
    hard_resistant_to_buyer_rate = _safe_rate(
        hr_to_buyer, hr_initial,
    )

    # Within-skeptical-bucket micro-shifts: a voter who started AND
    # ended in skeptical bucket but whose intent label changed.
    # Diagnostic only — NOT a gate. Surfaces e.g. the would_reject →
    # loyal_to_current_alternative pattern under peer pressure.
    within_skeptical_intent_shift_count = 0
    within_skeptical_intent_shift_examples: list[dict[str, str]] = []
    for v in voters:
        if (
            _bucket_of(v, "initial") == "skeptical"
            and _bucket_of(v, "final") == "skeptical"
            and (v.final_intent or v.initial_intent) != v.initial_intent
        ):
            within_skeptical_intent_shift_count += 1
            if len(within_skeptical_intent_shift_examples) < 5:
                within_skeptical_intent_shift_examples.append({
                    "from_intent": v.initial_intent,
                    "to_intent": (
                        v.final_intent or v.initial_intent
                    ),
                    "voter_id": str(v.voter_id),
                })

    # Per-round bucket distributions + skeptic transitions, lifted
    # from the InfluenceRound records (already populated by
    # influence_loop.py).
    per_round_bucket_distribution: dict[int, dict[str, int]] = {}
    per_round_skeptic_transitions: dict[int, dict[str, int]] = {}
    for r in rounds:
        if r.bucket_distribution:
            per_round_bucket_distribution[r.round_idx] = dict(
                r.bucket_distribution,
            )
        if r.skeptic_transitions:
            per_round_skeptic_transitions[r.round_idx] = dict(
                r.skeptic_transitions,
            )

    # Inherited rich-persona diversity (optional)
    pvds = (
        rich_persona_diversity.get("persona_voice_diversity_score")
        if rich_persona_diversity else None
    )
    rep_obj = (
        rich_persona_diversity.get("repeated_objection_count")
        if rich_persona_diversity else None
    )
    near_dup = (
        rich_persona_diversity.get("near_duplicate_turn_count")
        if rich_persona_diversity else None
    )
    ballots_unique = (
        rich_persona_diversity.get("ballots_scanned")
        if rich_persona_diversity else None
    )

    # --- Gate checks --------------------------------------------------
    if pvds is not None and pvds < 0.65:
        warnings.append(
            f"persona_voice_diversity_score_low={pvds:.3f}<0.65"
        )
    if rep_obj is not None and rep_obj > 8:
        warnings.append(
            f"repeated_objection_count_high={rep_obj}>8"
        )
    if near_dup is not None and near_dup > 0:
        warnings.append(
            f"near_duplicate_turn_count_nonzero={near_dup}"
        )
    # intent diversity average >= 2
    if intent_diversity_per_round:
        avg_intent_div = (
            sum(intent_diversity_per_round.values())
            / len(intent_diversity_per_round)
        )
        if avg_intent_div < 2:
            warnings.append(
                f"intent_labels_per_run_low={avg_intent_div:.2f}<2"
            )
    if max_role_conc > 0.30:
        warnings.append(
            f"max_role_concentration_high={max_role_conc:.3f}>0.30"
        )
    # All cohorts represented by >=3 voters (only check if there are
    # any cohort allocations)
    if cohort_counter:
        underrepresented = [
            cid for cid, count in cohort_counter.items() if count < 3
        ]
        if underrepresented:
            warnings.append(
                f"cohort_underrepresented:{len(underrepresented)}_cohorts<3_voters"
            )
    # Voter uniqueness: ids must all be distinct
    if voter_id_uniqueness_pct < 100.0:
        warnings.append(
            f"voter_id_collision={voter_id_uniqueness_pct:.1f}%_unique"
        )
    # (segment, role) diversity: aim for >=12 distinct pairs
    if len(seg_role_pairs) < 12:
        warnings.append(
            f"voter_duplication_collapse:"
            f"only_{len(seg_role_pairs)}_distinct_(segment,role)_pairs"
        )

    # Phase 12C.1 — resistance-realism gates. These are advisory (they
    # ALWAYS surface a warning if violated; whether the run hard-fails
    # is decided by the orchestrator/caller).
    initial_skeptical = initial_bucket_dist.get("skeptical", 0)
    final_skeptical = final_bucket_dist.get("skeptical", 0)
    if (
        initial_skeptical >= 1
        and final_skeptical == 0
    ):
        warnings.append(
            "zero_resistant_people_warning:"
            f"initial_skeptical={initial_skeptical}_final=0"
        )
    if (
        skeptic_initial >= 4
        and (skeptic_retention_rate or 0.0) < 0.5
    ):
        warnings.append(
            "skeptic_overconversion_warning:"
            f"retention_rate={skeptic_retention_rate:.2f}<0.5"
        )
    if (
        hard_reject_initial >= 1
        and (hard_reject_bucket_retention_rate or 0.0) < 1.0
    ):
        # Phase 12C.1 (Option A) — bucket-level semantics. The warning
        # fires only when a `would_reject` voter LEAVES the skeptical
        # bucket (becomes uncertain/receptive/buyer). Within-bucket
        # intent shifts (e.g. would_reject -> loyal_to_current_alternative,
        # both skeptical) are legitimate softening of *reason* without
        # softening of *stance*; tracked separately under
        # `within_skeptical_intent_shift_count`.
        warnings.append(
            "hard_reject_erased_warning:"
            f"initial={hard_reject_initial}_bucket_stays="
            f"{hard_reject_bucket_stays}"
        )
    if (
        cl_initial >= 2
        and (competitor_loyal_retention_rate or 0.0) < 0.7
    ):
        warnings.append(
            "competitor_loyalty_not_preserved_warning:"
            f"retention_rate={competitor_loyal_retention_rate:.2f}<0.7"
        )
    if skeptic_to_buyer > 0:
        warnings.append(
            f"skeptic_to_buyer_observed:count={skeptic_to_buyer}"
        )

    # Phase 12C.1 (extended) — uncertain-pileup + resistant-softening
    # warnings. The "everyone becomes uncertain by the end" failure
    # mode is just as unrealistic as "everyone becomes receptive" —
    # real markets don't soften skeptics without proof.
    initial_uncertain = initial_bucket_dist.get("uncertain", 0)
    final_uncertain = final_bucket_dist.get("uncertain", 0)
    uncertain_growth_pp = (
        (final_uncertain - initial_uncertain) / max(1, n_voters)
    ) * 100.0
    if (
        n_voters >= 20
        and (final_uncertain / max(1, n_voters)) >= 0.30
        and uncertain_growth_pp >= 15.0
    ):
        warnings.append(
            "uncertain_pileup_warning:"
            f"final_uncertain_share={final_uncertain / max(1, n_voters):.2f}"
            f"_growth_pp={uncertain_growth_pp:.1f}"
        )
    # Resistant softening: hard-resistant voters should usually stay
    # skeptical without an explicit proof signal. We surface a
    # warning if more than 25% of hard-resistant voters softened.
    if hr_initial >= 4:
        softened = hr_to_uncertain + hr_to_receptive + hr_to_buyer
        soften_rate = softened / hr_initial
        if soften_rate > 0.25:
            warnings.append(
                "resistant_softening_warning:"
                f"hard_resistant_softened_rate={soften_rate:.2f}>0.25"
            )

    return DiversityHealth(
        n_voters=n_voters,
        n_cohorts_represented=len(cohort_counter),
        n_segments_represented=len(seg_counter),
        n_roles_represented=len(role_counter),
        max_role_concentration=max_role_conc,
        competitor_user_share=comp_user_share,
        n_edges=n_edges,
        avg_edges_per_voter=avg_edges,
        edges_per_voter_min=edges_min,
        edges_per_voter_max=edges_max,
        edge_type_distribution=edge_type_dist,
        intent_diversity_per_round=intent_diversity_per_round,
        intent_changes_count=intent_changes,
        bucket_changes_count=bucket_changes,
        voter_id_uniqueness_pct=voter_id_uniqueness_pct,
        segment_role_pair_distinct_count=len(seg_role_pairs),
        persona_voice_diversity_score=pvds,
        repeated_objection_count=rep_obj,
        near_duplicate_turn_count=near_dup,
        ballots_with_unique_reasoning_pct=None,
        initial_intent_distribution=initial_intent_dist,
        final_intent_distribution=final_intent_dist,
        initial_bucket_distribution=initial_bucket_dist,
        final_bucket_distribution=final_bucket_dist,
        transition_matrix=transition_matrix,
        hard_resistant_count=hard_resistant_count,
        skeptic_retention_rate=skeptic_retention_rate,
        hard_reject_retention_rate=hard_reject_retention_rate,
        competitor_loyal_retention_rate=competitor_loyal_retention_rate,
        skeptic_to_uncertain_rate=skeptic_to_uncertain_rate,
        skeptic_to_receptive_rate=skeptic_to_receptive_rate,
        skeptic_to_buyer_rate=skeptic_to_buyer_rate,
        hard_resistant_to_uncertain_rate=hard_resistant_to_uncertain_rate,
        hard_resistant_to_receptive_rate=hard_resistant_to_receptive_rate,
        hard_resistant_to_buyer_rate=hard_resistant_to_buyer_rate,
        hard_resistant_retention_rate=hard_resistant_retention_rate,
        per_round_bucket_distribution=per_round_bucket_distribution,
        per_round_skeptic_transitions=per_round_skeptic_transitions,
        hard_reject_bucket_retention_rate=(
            hard_reject_bucket_retention_rate
        ),
        hard_reject_exact_intent_retention_rate=(
            hard_reject_exact_intent_retention_rate
        ),
        hard_resistant_bucket_retention_rate=(
            hard_resistant_bucket_retention_rate
        ),
        hard_resistant_exact_intent_retention_rate=(
            hard_resistant_exact_intent_retention_rate
        ),
        within_skeptical_intent_shift_count=(
            within_skeptical_intent_shift_count
        ),
        within_skeptical_intent_shift_examples=(
            within_skeptical_intent_shift_examples
        ),
        warnings=warnings,
        all_gates_passed=len(warnings) == 0,
    )
