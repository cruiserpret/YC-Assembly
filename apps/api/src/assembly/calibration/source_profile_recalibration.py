"""Phase 12E.5B — offline source-audience profile recalibration.

PURE MODULE. No DB, no LLM, no network. Reuses saved Phase 12E
artifacts (intent_distribution + per-role synthetic injection logic) +
operator-supplied outcome labels to evaluate candidate `hn_show_hn`
role-weight profiles.

Design goals:
  * Reuse the production augmenter's algorithm — do NOT re-implement
    the largest-remainder allocation. We invoke
    `augment_intent_drafts_with_source_audience(profile_override=...)`.
  * Score every candidate through Phase 12E.5A's
    `market_fidelity.score_case`, so the fidelity methodology and the
    recalibration pipeline share one source of truth.
  * Optimize **worst-case MAE across products**, not mean. A profile
    that helps one product and hurts another loses.
  * Respect plausibility constraints (all 10 roles present, weights in
    [0, 1], sum = 1.0, no role-specific value outside its sane band).

Public surfaces:
  * `reconstruct_legacy_drafts_from_intent_distribution(...)` — turn a
    saved `intent_distribution` dict back into synthetic `intent_drafts`
    that the augmenter can consume.
  * `project_with_candidate_profile(...)` — single-product projection.
  * `score_product_under_profile(...)` — projection + fidelity scoring
    via market_fidelity.score_case.
  * `generate_candidate_profiles(...)` — bounded grid search.
  * `recalibrate_hn_show_hn(...)` — top-level entry. Returns ranked
    candidate list + recommended v2.
  * `build_proof_packet_draft(...)` — draft Proof Packet for one
    (profile, product) pair.

Constants (TUNABLE):
  * `BUCKET_NAMES`, `CANDIDATE_PLAUSIBILITY_BANDS` for grid bounds.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assembly.calibration.market_fidelity import (
    BUCKETS,
    score_case,
)
from assembly.sources.audience.augmenter import (
    augment_intent_drafts_with_source_audience,
    split_view_distributions,
)
from assembly.sources.audience.role_taxonomy import (
    AUDIENCE_ROLES,
    SOURCE_PROFILES,
)


# All 10 audience roles in canonical order. Profile dicts must contain
# every key, even if its weight is 0.0.
ALL_AUDIENCE_ROLES: tuple[str, ...] = tuple(AUDIENCE_ROLES.keys())


# Plausibility bands — used to constrain the candidate grid search.
# A Show HN audience can reasonably contain target customers /
# competitor users at high share; observers/proof-seekers at moderate
# share; off-topic noise at very low share. These bands are SOFT —
# they govern which candidates the search generates, not whether the
# augmenter accepts them.
CANDIDATE_PLAUSIBILITY_BANDS: dict[str, tuple[float, float]] = {
    "target_customer_evaluator":      (0.18, 0.42),
    "existing_competitor_user":       (0.10, 0.25),
    "proof_seeker_only":              (0.04, 0.14),
    "industry_observer":              (0.06, 0.20),
    "technical_or_legal_explainer":   (0.02, 0.10),
    "meta_commenter":                 (0.02, 0.10),
    "category_skeptic":               (0.04, 0.12),
    "incumbent_defender":             (0.03, 0.10),
    "casual_bystander":               (0.01, 0.06),
    "off_topic_noise_candidate":      (0.00, 0.04),
}


# Backwards-compat sanity: the v1 profile shipped with Phase 12E.
HN_SHOW_HN_V1: dict[str, float] = dict(SOURCE_PROFILES["hn_show_hn"])


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


# Heuristic: which intent labels map a legacy customer voice to the
# `existing_competitor_user` role vs `target_customer_evaluator`. This
# mirrors the augmenter's `assign_audience_role` heuristic.
_COMPETITOR_INTENT_LABELS: frozenset[str] = frozenset({
    "loyal_to_current_alternative",
})


def reconstruct_legacy_drafts_from_intent_distribution(
    *,
    intent_distribution: dict[str, int],
    run_scope_id: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Rebuild a synthetic list of intent_drafts from a saved
    `intent_distribution` so the augmenter can re-project under a new
    candidate profile.

    Returns `(drafts, persona_metadata_by_pid)` where each draft is a
    dict matching the SimulatedIntentDraft schema, and the metadata
    dict supplies `segment_label` per persona for the augmenter's
    heuristic role assignment.

    This is a defensible reconstruction because:
      * The legacy 24 personas are all customer-class voices
        (Phase 12E `n_legacy_customer_voices=24` for both products).
      * The augmenter's only use of `persona_meta` is to call
        `assign_audience_role(segment_label=...)`, which routes
        "competitor_user_*" segments to `existing_competitor_user`
        and everything else to `target_customer_evaluator`.
      * The bucket assignment downstream uses `audience_role +
        intent_label`, both of which we have.
    """
    drafts: list[dict[str, Any]] = []
    persona_meta: dict[str, dict[str, Any]] = {}
    pid = 0
    for intent_label, count in intent_distribution.items():
        for _ in range(int(count)):
            pid += 1
            persona_id = f"{run_scope_id}_legacy_{pid:03d}"
            is_competitor = intent_label in _COMPETITOR_INTENT_LABELS
            segment_label = (
                "competitor_user_alt" if is_competitor else "trust_seeker"
            )
            switching_status = (
                "loyal_to_current_alternative" if is_competitor
                else "weakly_attached_to_alternative"
            )
            stance_label = (
                "skeptical" if intent_label in (
                    "would_reject", "loyal_to_current_alternative",
                )
                else "curious_but_unconvinced"
            )
            drafts.append({
                "persona_id": persona_id,
                "cohort_id": "reconstructed_cohort_0",
                "stance_label": stance_label,
                "simulated_intent": intent_label,
                "intent_strength": "medium",
                "switching_status": switching_status,
                "current_alternative": None,
                "conditions_to_buy": [],
                "reason_for_rejection": None,
                "proof_needed": [],
                "evidence_basis": (
                    "rule:reconstructed_from_intent_distribution"
                ),
                "discussion_turn_ids": [],
                "ballot_ids": [],
                "memory_atom_ids": [],
                "confidence": "medium",
                "caveat": (
                    "Reconstructed from saved intent_distribution for "
                    "offline recalibration; not from a fresh LLM ballot."
                ),
                "intent_signal": None,
                "intent_signal_basis": None,
            })
            persona_meta[persona_id] = {"segment_label": segment_label}
    return drafts, persona_meta


# ---------------------------------------------------------------------------
# Projection + scoring
# ---------------------------------------------------------------------------


@dataclass
class ProductFixture:
    """Inputs for one product in the recalibration."""

    name: str
    intent_distribution: dict[str, int]
    observed_pct: dict[str, float]
    run_scope_id: str
    # Evidence-fidelity audit fields. We carry them along so the
    # final scoring run records that this case was offline (no live
    # retrieval, no prediction tampering possible).
    evidence_snapshot_hash: str | None = None
    brief_hash: str | None = None
    outcome_after_cutoff: bool = True
    leakage_audit_clean: bool = True
    # Optional: prior-run skeptic retention for trajectory fidelity.
    skeptic_retention: float | None = None
    hard_resistant_count: int | None = None


def _normalize_dist_to_pct(d: dict[str, int]) -> dict[str, float]:
    total = sum(d.get(b, 0) for b in BUCKETS)
    if total <= 0:
        return {b: 0.0 for b in BUCKETS}
    return {b: 100.0 * d.get(b, 0) / total for b in BUCKETS}


def _validate_profile(profile: dict[str, float]) -> None:
    """Strict validation: every audience role present, all weights in
    [0, 1], sum = 1.0 (±1e-6)."""
    missing = [r for r in ALL_AUDIENCE_ROLES if r not in profile]
    if missing:
        raise ValueError(
            f"profile missing roles: {missing}. All 10 roles required."
        )
    for role, w in profile.items():
        if role not in ALL_AUDIENCE_ROLES:
            raise ValueError(f"unknown role in profile: {role!r}")
        if w < 0:
            raise ValueError(
                f"negative weight rejected: {role}={w}"
            )
        if w > 1.0:
            raise ValueError(
                f"weight > 1.0 rejected: {role}={w}"
            )
    s = sum(profile.values())
    if abs(s - 1.0) > 1e-6:
        raise ValueError(
            f"profile weights must sum to 1.0; got {s:.6f}"
        )


def project_with_candidate_profile(
    *,
    intent_drafts: list[dict[str, Any]],
    persona_meta: dict[str, dict[str, Any]],
    candidate_profile: dict[str, float],
    run_scope_id: str,
) -> dict[str, Any]:
    """Run the augmenter under `candidate_profile` and return the
    4-view bucket counters + augmentation audit.

    No LLM calls — pure invocation of the existing augmenter logic
    with the offline `profile_override` hook added in Phase 12E.5B.
    """
    _validate_profile(candidate_profile)
    augmented, audit = augment_intent_drafts_with_source_audience(
        intent_drafts=intent_drafts,
        persona_metadata_by_pid=persona_meta,
        launch_source="hn_show_hn",  # honored for audit metadata only
        run_scope_id=run_scope_id,
        profile_override=candidate_profile,
    )
    views = split_view_distributions(augmented)
    return {
        "augmented_drafts": augmented,
        "augmentation_audit": audit,
        "audience_views": views,
    }


def _realized_role_mix_pct(
    augmented: list[dict[str, Any]],
) -> dict[str, float]:
    """Compute the realized role-mix from an augmented drafts list."""
    n = len(augmented)
    if n == 0:
        return {r: 0.0 for r in ALL_AUDIENCE_ROLES}
    counts: dict[str, int] = {r: 0 for r in ALL_AUDIENCE_ROLES}
    for d in augmented:
        role = d.get("audience_role", "target_customer_evaluator")
        counts[role] = counts.get(role, 0) + 1
    return {r: 100.0 * counts.get(r, 0) / n for r in ALL_AUDIENCE_ROLES}


def score_product_under_profile(
    *,
    product: ProductFixture,
    candidate_profile: dict[str, float],
) -> dict[str, Any]:
    """Project + score one product under one candidate profile.

    Returns a dict with predicted_pct + observed_pct + the full
    fidelity report from `score_case`.
    """
    drafts, persona_meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution=product.intent_distribution,
        run_scope_id=product.run_scope_id,
    )
    projection = project_with_candidate_profile(
        intent_drafts=drafts,
        persona_meta=persona_meta,
        candidate_profile=candidate_profile,
        run_scope_id=product.run_scope_id,
    )
    src_view = projection["audience_views"]["source_audience_reaction"]
    predicted_pct = _normalize_dist_to_pct(src_view)
    target_view = projection["audience_views"]["target_market_reaction"]
    realized_mix_pct = _realized_role_mix_pct(
        projection["augmented_drafts"],
    )
    planned_mix_pct = {
        r: 100.0 * float(candidate_profile[r])
        for r in ALL_AUDIENCE_ROLES
    }
    fidelity = score_case(
        predicted_pct=predicted_pct,
        observed_pct=product.observed_pct,
        planned_role_mix_pct=planned_mix_pct,
        realized_role_mix_pct=realized_mix_pct,
        noise_meta_share_pct=(
            100.0 * projection["audience_views"]
            ["noise_meta_estimate"]["count"]
            / max(1, len(projection["augmented_drafts"]))
        ),
        per_run_predicted_pct=None,
        per_run_mae_pp=None,
        stance_transitions=None,
        skeptic_retention=product.skeptic_retention,
        hard_resistant_count=product.hard_resistant_count,
        predicted_objection_cluster_ids=None,
        observed_objection_cluster_ids=None,
        prediction_artifact_hash_before=(
            product.evidence_snapshot_hash or "offline_projection"
        ),
        prediction_artifact_hash_after=(
            product.evidence_snapshot_hash or "offline_projection"
        ),
        evidence_snapshot_hash=product.evidence_snapshot_hash,
        brief_hash=product.brief_hash,
        outcome_after_cutoff=product.outcome_after_cutoff,
        leakage_audit_clean=product.leakage_audit_clean,
    )
    return {
        "product": product.name,
        "predicted_pct_source_audience": predicted_pct,
        "predicted_pct_target_market": _normalize_dist_to_pct(target_view),
        "observed_pct": product.observed_pct,
        "augmentation_audit": projection["augmentation_audit"],
        "realized_role_mix_pct": realized_mix_pct,
        "planned_role_mix_pct": planned_mix_pct,
        "fidelity": fidelity,
    }


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def _step_range(lo: float, hi: float, step: float) -> Iterator[float]:
    if hi < lo:
        return
    v = lo
    # tiny epsilon avoids float drift at the upper end
    while v <= hi + 1e-9:
        yield round(v, 4)
        v += step


def _renormalize(profile: dict[str, float]) -> dict[str, float]:
    s = sum(profile.values())
    if s <= 0:
        raise ValueError("cannot renormalize profile with sum<=0")
    return {k: v / s for k, v in profile.items()}


def generate_candidate_profiles(
    *,
    base_profile: dict[str, float] = None,
    coarse_step: float = 0.02,
    include_baseline: bool = True,
    bands: dict[str, tuple[float, float]] | None = None,
    max_candidates: int = 5000,
) -> Iterator[dict[str, float]]:
    """Yield candidate profiles by varying the under-performing roles
    around the current baseline. The Phase 12E.5B diagnosis is that
    `industry_observer + proof_seeker_only + casual_bystander` are
    over-injected on Opslane; we narrow the grid to the most
    diagnostic dimensions and redistribute the freed mass over the
    customer + skeptic-class roles.

    To keep the search tractable, the grid spans ONLY 3 dimensions
    explicitly:
      - `industry_observer` ∈ [0.06, 0.20] in `coarse_step`
      - `proof_seeker_only` ∈ [0.04, 0.14] in `coarse_step`
      - `target_customer_evaluator` ∈ [0.18, 0.42] in `coarse_step`

    For each `(io, ps, tc)` triple, the remaining mass is distributed
    over the other 7 roles in PROPORTION to the v1 baseline weights
    of those 7 roles. This guarantees plausibility (each role stays
    inside its band) while keeping the search at O(~6 × 6 × 13) =
    ~470 candidates — small enough to evaluate exhaustively.

    Set `coarse_step` smaller for a finer grid.

    Always yields the baseline first (so it appears in the report
    ranking as a reference point).
    """
    base = dict(base_profile or HN_SHOW_HN_V1)
    bands_eff = bands or CANDIDATE_PLAUSIBILITY_BANDS
    if include_baseline:
        yield dict(base)
    free_roles = (
        "existing_competitor_user",
        "technical_or_legal_explainer",
        "meta_commenter",
        "category_skeptic",
        "incumbent_defender",
        "casual_bystander",
        "off_topic_noise_candidate",
    )
    free_total_v1 = sum(base[r] for r in free_roles)
    seen: set[tuple[float, ...]] = set()
    count = 0
    for io in _step_range(*bands_eff["industry_observer"], coarse_step):
        for ps in _step_range(*bands_eff["proof_seeker_only"], coarse_step):
            for tc in _step_range(
                *bands_eff["target_customer_evaluator"], coarse_step,
            ):
                remaining = 1.0 - (io + ps + tc)
                if remaining <= 0 or free_total_v1 <= 0:
                    continue
                scale = remaining / free_total_v1
                # Build candidate
                cand = dict(base)
                cand["industry_observer"] = io
                cand["proof_seeker_only"] = ps
                cand["target_customer_evaluator"] = tc
                for r in free_roles:
                    cand[r] = base[r] * scale
                # Plausibility band check per role
                ok = True
                for r, (lo, hi) in bands_eff.items():
                    if cand[r] < lo - 1e-6 or cand[r] > hi + 1e-6:
                        ok = False
                        break
                if not ok:
                    continue
                # Renormalize defensively for float drift
                cand = _renormalize(cand)
                key = tuple(round(cand[r], 4) for r in ALL_AUDIENCE_ROLES)
                if key in seen:
                    continue
                seen.add(key)
                yield cand
                count += 1
                if count >= max_candidates:
                    return


# ---------------------------------------------------------------------------
# Optimization objective
# ---------------------------------------------------------------------------


def _candidate_summary(
    *,
    candidate_profile: dict[str, float],
    product_scores: list[dict[str, Any]],
    baseline_per_product_mae: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the summary record used for ranking."""
    per_product_mae: dict[str, float] = {}
    per_product_max_pp: dict[str, float] = {}
    per_product_tvd: dict[str, float] = {}
    per_product_uncertain_inj: dict[str, float] = {}
    per_product_skeptic_under: dict[str, float] = {}
    per_product_buyer_false_conf: dict[str, bool] = {}
    per_product_band: dict[str, str] = {}
    fidelity_scores: list[float] = []
    warnings: list[str] = []
    for ps in product_scores:
        name = ps["product"]
        decision = (ps["fidelity"].get("decision") or {})
        per_product_mae[name] = float(decision.get("mae_pp", 0.0))
        per_product_max_pp[name] = float(
            decision.get("max_bucket_error_pp", 0.0)
        )
        per_product_tvd[name] = float(decision.get("tvd", 0.0))
        per_product_uncertain_inj[name] = float(
            decision.get("uncertain_injection_pp", 0.0)
        )
        per_product_skeptic_under[name] = float(
            decision.get("skeptic_underprediction_pp", 0.0)
        )
        per_product_buyer_false_conf[name] = bool(
            decision.get("buyer_false_confidence", False)
        )
        per_product_band[name] = decision.get("band") or "n/a"
        agg = ps["fidelity"].get("aggregate_score") or {}
        score = agg.get("overall_internal_score_0_100")
        if score is not None:
            fidelity_scores.append(float(score))
        # Surface gate warnings
        gates = ps["fidelity"].get("gates") or {}
        for gname, gdata in gates.items():
            if not isinstance(gdata, dict):
                continue
            status = gdata.get("status")
            if status in ("warn", "fail", "critical_fail"):
                warnings.append(f"{name}:{gname}={status}")
    mae_vals = list(per_product_mae.values())
    worst_mae = max(mae_vals) if mae_vals else float("inf")
    mean_mae = (sum(mae_vals) / len(mae_vals)) if mae_vals else float("inf")
    median_fidelity = (
        statistics.median(fidelity_scores) if fidelity_scores else None
    )
    # Regression analysis vs baseline
    regressions: dict[str, float] = {}
    if baseline_per_product_mae:
        for name, mae in per_product_mae.items():
            base_mae = baseline_per_product_mae.get(name)
            if base_mae is not None:
                regressions[name] = mae - base_mae
    return {
        "profile": candidate_profile,
        "per_product_mae_pp": per_product_mae,
        "per_product_max_bucket_pp": per_product_max_pp,
        "per_product_tvd": per_product_tvd,
        "per_product_uncertain_injection_pp": per_product_uncertain_inj,
        "per_product_skeptic_underprediction_pp": per_product_skeptic_under,
        "per_product_buyer_false_confidence": per_product_buyer_false_conf,
        "per_product_decision_band": per_product_band,
        "worst_case_mae_pp": worst_mae,
        "mean_mae_pp": mean_mae,
        "median_internal_fidelity_score": median_fidelity,
        "gate_warnings": warnings,
        "regression_vs_baseline_pp": regressions,
    }


def _candidate_passes_hard_constraints(
    summary: dict[str, Any],
    *,
    baseline_per_product_mae: dict[str, float] | None,
    max_allowed_regression_pp: float = 3.0,
) -> tuple[bool, list[str]]:
    """Return (passes, reasons_for_failure_or_warning)."""
    reasons: list[str] = []
    # Buyer false confidence is always a critical block.
    for name, flag in summary[
        "per_product_buyer_false_confidence"
    ].items():
        if flag:
            reasons.append(f"buyer_false_confidence:{name}")
    # Regression threshold
    if baseline_per_product_mae:
        for name, delta in summary["regression_vs_baseline_pp"].items():
            if delta > max_allowed_regression_pp:
                reasons.append(
                    f"regression_gt_{max_allowed_regression_pp}pp:"
                    f"{name}={delta:.2f}"
                )
    return (not reasons, reasons)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def recalibrate_hn_show_hn(
    *,
    products: list[ProductFixture],
    base_profile: dict[str, float] | None = None,
    coarse_step: float = 0.02,
    top_k: int = 10,
    max_candidates: int = 5000,
    max_allowed_regression_pp: float = 3.0,
) -> dict[str, Any]:
    """Run the recalibration end-to-end.

    Returns:
      {
        "baseline": { ...summary of HN_SHOW_HN_V1 across products... },
        "candidates_evaluated": int,
        "top_candidates": [<top_k summaries, ranked>],
        "recommended_v2": <summary of the selected profile>,
        "recommendation_rationale": str,
      }
    """
    base = dict(base_profile or HN_SHOW_HN_V1)
    # 1) Baseline scores (v1)
    baseline_product_scores: list[dict[str, Any]] = [
        score_product_under_profile(
            product=p, candidate_profile=base,
        )
        for p in products
    ]
    baseline_summary = _candidate_summary(
        candidate_profile=base,
        product_scores=baseline_product_scores,
    )
    baseline_per_product_mae = dict(
        baseline_summary["per_product_mae_pp"]
    )
    # 2) Grid search
    candidates_evaluated = 0
    all_summaries: list[dict[str, Any]] = []
    seen_keys: set[tuple[float, ...]] = set()
    for cand in generate_candidate_profiles(
        base_profile=base,
        coarse_step=coarse_step,
        include_baseline=False,
        max_candidates=max_candidates,
    ):
        key = tuple(round(cand[r], 4) for r in ALL_AUDIENCE_ROLES)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        product_scores = [
            score_product_under_profile(
                product=p, candidate_profile=cand,
            )
            for p in products
        ]
        summary = _candidate_summary(
            candidate_profile=cand,
            product_scores=product_scores,
            baseline_per_product_mae=baseline_per_product_mae,
        )
        passes, reasons = _candidate_passes_hard_constraints(
            summary,
            baseline_per_product_mae=baseline_per_product_mae,
            max_allowed_regression_pp=max_allowed_regression_pp,
        )
        summary["hard_constraints_passed"] = passes
        summary["hard_constraint_violations"] = reasons
        all_summaries.append(summary)
        candidates_evaluated += 1

    # 3) Rank — prefer hard-pass candidates, then by worst_case_mae,
    #    then by mean_mae as tiebreaker.
    def _rank_key(s: dict[str, Any]) -> tuple[int, float, float]:
        return (
            0 if s.get("hard_constraints_passed") else 1,
            s.get("worst_case_mae_pp", float("inf")),
            s.get("mean_mae_pp", float("inf")),
        )
    all_summaries.sort(key=_rank_key)
    top = all_summaries[: max(1, top_k)]
    recommended = top[0] if top else baseline_summary
    rationale = (
        f"Selected candidate with worst-case MAE "
        f"{recommended['worst_case_mae_pp']:.2f}pp across "
        f"{len(products)} products (baseline worst-case "
        f"{baseline_summary['worst_case_mae_pp']:.2f}pp). "
        f"Mean MAE {recommended['mean_mae_pp']:.2f}pp vs baseline "
        f"{baseline_summary['mean_mae_pp']:.2f}pp. "
        + (
            "All hard constraints passed."
            if recommended.get("hard_constraints_passed")
            else "Hard-constraint violations: "
            + ", ".join(recommended.get("hard_constraint_violations") or [])
        )
    )
    return {
        "baseline_v1": baseline_summary,
        "baseline_v1_product_scores": baseline_product_scores,
        "candidates_evaluated": candidates_evaluated,
        "top_candidates": top,
        "recommended_v2": recommended,
        "recommendation_rationale": rationale,
        "all_audience_roles": list(ALL_AUDIENCE_ROLES),
        "schema_version": "12e.5b.v1",
    }


# ---------------------------------------------------------------------------
# Proof Packet draft
# ---------------------------------------------------------------------------


def build_proof_packet_draft(
    *,
    case_id: str,
    product: ProductFixture,
    candidate_profile: dict[str, float],
    product_score: dict[str, Any],
    model_routing: dict[str, str] | None = None,
    prior_validation_support_count: dict[str, int] | None = None,
    nearest_historical_cases: list[dict[str, Any]] | None = None,
    do_not_over_trust_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Draft a Proof Packet for one (profile, product) pair per
    Section 5 of the Market Fidelity Methodology.

    `product_score` is the dict returned by
    `score_product_under_profile`.
    """
    candidate_profile_hash = hashlib.sha256(
        json.dumps(candidate_profile, sort_keys=True).encode("utf-8")
    ).hexdigest()
    fidelity = product_score.get("fidelity", {})
    aug_audit = product_score.get("augmentation_audit", {})
    return {
        "schema_version": "12e.5a.v1",
        "case_id": case_id,
        "draft": True,  # NOTE: this is a draft; hash-locked only after a paid run.
        "prediction_artifact_hash": product_score.get("predicted_pct_source_audience"),
        "evidence_snapshot_hash": product.evidence_snapshot_hash,
        "brief_hash": product.brief_hash,
        "model_routing": model_routing or {
            "roleplay_model": "claude-sonnet-4-6",
            "synthesis_model": "claude-opus-4-7",
        },
        "source_audience_profile": "hn_show_hn_v2_candidate",
        "candidate_profile_hash": candidate_profile_hash,
        "candidate_profile_role_weights": candidate_profile,
        "persona_composition": {
            "n_legacy_drafts": aug_audit.get("n_legacy_drafts"),
            "n_legacy_customer_voices": aug_audit.get(
                "n_legacy_customer_voices",
            ),
            "n_synthetic_added_by_role": aug_audit.get(
                "n_synthetic_added_by_role", {},
            ),
            "total_after_augmentation": aug_audit.get(
                "total_after_augmentation",
            ),
        },
        "decision_distribution": {
            "source_audience_view": product_score.get(
                "predicted_pct_source_audience",
            ),
            "target_market_view": product_score.get(
                "predicted_pct_target_market",
            ),
            "observed": product.observed_pct,
        },
        "confidence_level_internal": "medium" if (
            fidelity.get("aggregate_score", {})
            .get("overall_internal_score_0_100", 0) or 0
        ) >= 50 else "medium_low",
        "prior_validation_support_count": (
            prior_validation_support_count or {}
        ),
        "nearest_historical_cases": nearest_historical_cases or [],
        "fidelity": {
            "decision":         fidelity.get("decision"),
            "source_audience":  fidelity.get("source_audience"),
            "reason":           fidelity.get("reason"),
            "trajectory":       fidelity.get("trajectory"),
            "stability":        fidelity.get("stability"),
            "evidence":         fidelity.get("evidence"),
        },
        "overall_internal_score_0_100": (
            fidelity.get("aggregate_score", {})
            .get("overall_internal_score_0_100")
        ),
        "null_pillars": (
            fidelity.get("aggregate_score", {}).get("null_pillars")
        ),
        "gates": fidelity.get("gates"),
        "known_limitations": [
            "Offline projection — predicted distribution is derived "
            "from a saved Phase 12E paid run + a candidate role-weight "
            "profile applied to its augmentation step. Not a fresh "
            "paid run.",
            "DocuSeal observed labels are the corrected QA pass set "
            "(scorable n=83 after noise re-classification).",
            "Opslane observed labels are the raw first-pass set "
            "(scorable n=133).",
            "Profile is a calibration-stage prior; needs a fresh "
            "paid confirmation run before public claim.",
        ],
        "do_not_over_trust_warnings": (
            do_not_over_trust_warnings or [
                "This Proof Packet is a DRAFT. A real Proof Packet "
                "must lock the prediction artifact hash with a fresh "
                "paid run; offline projection cannot satisfy "
                "Evidence Fidelity's hash-unchanged invariant.",
                "Two-product validation set is small; results may not "
                "generalize to non-HN sources.",
            ]
        ),
    }


__all__ = [
    "ALL_AUDIENCE_ROLES",
    "CANDIDATE_PLAUSIBILITY_BANDS",
    "HN_SHOW_HN_V1",
    "ProductFixture",
    "reconstruct_legacy_drafts_from_intent_distribution",
    "project_with_candidate_profile",
    "score_product_under_profile",
    "generate_candidate_profiles",
    "recalibrate_hn_show_hn",
    "build_proof_packet_draft",
]
