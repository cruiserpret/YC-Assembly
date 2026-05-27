"""Phase 12E.5A — Assembly Market Fidelity scoring module.

Pure-function module implementing the six fidelity pillars defined in
`docs/assembly_market_fidelity_methodology.md`:

  1. Source-Audience Fidelity
  2. Decision Fidelity
  3. Reason Fidelity         (skeleton — depends on labels Phase 12E.6+)
  4. Trajectory/Influence Fidelity
  5. Stability Fidelity
  6. Evidence/Anti-Leakage Fidelity

Plus:
  - `compute_market_fidelity_score(...)` — weighted 0-100 aggregate
  - `evaluate_fidelity_gates(...)` — PASS/WARN/CRITICAL_FAIL gates

ALL functions are pure: same inputs → same outputs, no I/O, no DB, no
LLM calls, no network. Designed to be invoked by:
  - Phase 12E.5B offline recalibration (planned)
  - Future Proof Packet builder
  - Variance harness post-processing

Threshold constants are exposed at module level so they can be tuned as
more validation data lands; the gate logic reads them from the
configurable constants, never inlines them.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Configurable thresholds. Tune as validation data expands. Document any
# change in docs/PHASE_GATES.md per O-12E.5A.3.
# ---------------------------------------------------------------------------

# Decision-fidelity MAE bands (in percentage points).
MAE_STRONG_PP: float = 8.0
MAE_PROMISING_PP: float = 12.0
MAE_WEAK_PP: float = 18.0
# Above MAE_WEAK_PP is "fail".

# Per-bucket gates.
MAX_BUCKET_ERROR_WARN_PP: float = 15.0
MAX_BUCKET_ERROR_FAIL_PP: float = 20.0
BUYER_FALSE_CONFIDENCE_PRED_PCT: float = 5.0
BUYER_FALSE_CONFIDENCE_OBS_PCT: float = 1.0
SKEPTIC_UNDERPRED_WARN_PP: float = 10.0
UNCERTAIN_INJECTION_WARN_PP: float = 10.0

# Stability gates.
RUN_TO_RUN_TVD_WARN: float = 0.10
MAX_BUCKET_RANGE_WARN_PP: float = 5.0
MAX_BUCKET_RANGE_FAIL_PP: float = 10.0
BUCKET_SIGN_FLIP_WARN_RATE: float = 0.30

# Source-audience fidelity normalization (max acceptable role_mix_error_pp).
ROLE_MIX_ERROR_NORM_PP: float = 30.0

# Trajectory thresholds.
SKEPTIC_RETENTION_HEALTHY_FLOOR: float = 0.50
UNREALISTIC_CONVERSION_SKEPTICAL_TO_BUYER_PCT: float = 5.0

# Pillar weights for the internal score (must sum to 1.0).
PILLAR_WEIGHTS: dict[str, float] = {
    "decision":        0.40,
    "source_audience": 0.20,
    "reason":          0.15,
    "trajectory":      0.10,
    "stability":       0.10,
    "evidence":        0.05,
}

# The buckets Assembly scores over.
BUCKETS: tuple[str, ...] = ("buyer", "receptive", "uncertain", "skeptical")


# ---------------------------------------------------------------------------
# Lightweight result containers (dataclasses, not Pydantic, to keep this
# module dependency-light and importable without the rest of Assembly).
# ---------------------------------------------------------------------------


@dataclass
class DecisionFidelity:
    score_0_100: float | None
    mae_pp: float
    tvd: float
    max_bucket_error_pp: float
    signed_errors_pp: dict[str, float]
    abs_errors_pp: dict[str, float]
    band: str
    buyer_false_confidence: bool
    skeptic_underprediction_pp: float
    uncertain_injection_pp: float


@dataclass
class SourceAudienceFidelity:
    score_0_100: float | None
    role_mix_error_pp: float
    target_vs_non_target_share_error_pp: float
    noise_meta_share_pct: float
    audience_coverage_score: float
    source_profile_mae_pp: float | None = None


@dataclass
class StabilityFidelity:
    score_0_100: float | None
    n_runs: int
    mae_stdev_pp: float | None
    run_to_run_tvd_max: float | None
    max_bucket_range_pp: float | None
    bucket_sign_flip_rate: float | None


@dataclass
class TrajectoryFidelity:
    score_0_100: float | None
    skeptic_retention: float | None
    hard_resistant_preservation_count: int | None
    receptive_to_buyer_conversion_pct: float | None
    uncertain_to_receptive_conversion_pct: float | None
    unrealistic_conversion_warnings: list[str] = field(default_factory=list)


@dataclass
class EvidenceFidelity:
    score_0_100: float  # 0 or 100, no middle ground (binary by design)
    leakage_audit_clean: bool
    prediction_artifact_hash_unchanged: bool
    evidence_snapshot_hash_present: bool
    brief_hash_present: bool
    outcome_after_cutoff: bool
    critical_fail_reasons: list[str] = field(default_factory=list)


@dataclass
class ReasonFidelity:
    """Placeholder. Reason scoring requires operator-supplied cluster
    labels (Phase 12E.6+). When labels are absent, `score_0_100=None`
    and the `null_reason` field explains why."""

    score_0_100: float | None
    top_objection_recall: float | None = None
    top_objection_precision: float | None = None
    proof_need_recall: float | None = None
    persuasion_driver_overlap_pct: float | None = None
    semantic_cluster_f1: float | None = None
    reason_rank_correlation: float | None = None
    null_reason: str | None = None


# ---------------------------------------------------------------------------
# Distribution helpers
# ---------------------------------------------------------------------------


def _safe_pct_dist(d: dict[str, float]) -> dict[str, float]:
    """Normalize a bucket dict (counts or percents) to percents
    summing to 100. Missing buckets → 0.0. Pure function."""
    raw = {b: float(d.get(b, 0.0)) for b in BUCKETS}
    total = sum(raw.values())
    if total <= 0:
        return {b: 0.0 for b in BUCKETS}
    # If already in percent (sum ~= 100), keep; if counts, normalize.
    if 99.0 <= total <= 101.0:
        return raw
    return {b: 100.0 * v / total for b in BUCKETS for v in [raw[b]]}


def _mae_pp(predicted_pct: dict[str, float], observed_pct: dict[str, float]) -> float:
    p = _safe_pct_dist(predicted_pct)
    o = _safe_pct_dist(observed_pct)
    return sum(abs(p[b] - o[b]) for b in BUCKETS) / len(BUCKETS)


def _tvd(predicted_pct: dict[str, float], observed_pct: dict[str, float]) -> float:
    p = _safe_pct_dist(predicted_pct)
    o = _safe_pct_dist(observed_pct)
    return 0.5 * sum(abs(p[b] - o[b]) for b in BUCKETS) / 100.0


def _decision_band(mae_pp: float) -> str:
    if mae_pp <= MAE_STRONG_PP:
        return "strong"
    if mae_pp <= MAE_PROMISING_PP:
        return "promising"
    if mae_pp <= MAE_WEAK_PP:
        return "weak"
    return "fail"


# ---------------------------------------------------------------------------
# 1. Decision Fidelity
# ---------------------------------------------------------------------------


def compute_decision_fidelity(
    *,
    predicted_pct: dict[str, float],
    observed_pct: dict[str, float],
) -> DecisionFidelity:
    """Score the predicted-vs-observed bucket distribution."""
    p = _safe_pct_dist(predicted_pct)
    o = _safe_pct_dist(observed_pct)
    signed = {b: p[b] - o[b] for b in BUCKETS}
    abs_err = {b: abs(signed[b]) for b in BUCKETS}
    mae_pp = sum(abs_err.values()) / len(BUCKETS)
    tvd = 0.5 * sum(abs_err.values()) / 100.0
    max_err = max(abs_err.values()) if abs_err else 0.0
    band = _decision_band(mae_pp)
    # Per-pillar score: 0..100. Linear from 0 → 100 at MAE 0pp; 0 at MAE
    # 25pp. Clipped.
    score = max(0.0, min(100.0, 100.0 * (1.0 - mae_pp / 25.0)))
    buyer_false_conf = (
        p["buyer"] >= BUYER_FALSE_CONFIDENCE_PRED_PCT
        and o["buyer"] < BUYER_FALSE_CONFIDENCE_OBS_PCT
    )
    skeptic_underpred_pp = o["skeptical"] - p["skeptical"]
    uncertain_inj_pp = p["uncertain"] - o["uncertain"]
    return DecisionFidelity(
        score_0_100=score,
        mae_pp=mae_pp,
        tvd=tvd,
        max_bucket_error_pp=max_err,
        signed_errors_pp=signed,
        abs_errors_pp=abs_err,
        band=band,
        buyer_false_confidence=buyer_false_conf,
        skeptic_underprediction_pp=skeptic_underpred_pp,
        uncertain_injection_pp=uncertain_inj_pp,
    )


# ---------------------------------------------------------------------------
# 2. Source-Audience Fidelity
# ---------------------------------------------------------------------------


def compute_source_audience_fidelity(
    *,
    planned_role_mix_pct: dict[str, float],
    realized_role_mix_pct: dict[str, float],
    customer_roles: tuple[str, ...] = (
        "target_customer_evaluator", "existing_competitor_user",
    ),
    noise_meta_share_pct: float | None = None,
    source_profile_observed_mix_pct: dict[str, float] | None = None,
) -> SourceAudienceFidelity:
    """Score whether the realized audience composition matches the
    planned profile.

    `planned_role_mix_pct` and `realized_role_mix_pct` both sum to 100
    (or are normalized internally). Keys are audience-role labels.
    """
    p = _normalize_role_mix(planned_role_mix_pct)
    r = _normalize_role_mix(realized_role_mix_pct)
    all_roles = set(p) | set(r)
    role_mix_error_pp = sum(
        abs(p.get(role, 0.0) - r.get(role, 0.0)) for role in all_roles
    )
    customer_share_planned = sum(p.get(c, 0.0) for c in customer_roles)
    customer_share_realized = sum(r.get(c, 0.0) for c in customer_roles)
    target_vs_non_target_err = abs(
        customer_share_planned - customer_share_realized
    )
    # Coverage: fraction of planned-nonzero roles that have at least one
    # realized persona.
    nonzero_planned = [role for role, w in p.items() if w > 0]
    coverage = (
        sum(1 for role in nonzero_planned if r.get(role, 0.0) > 0)
        / max(1, len(nonzero_planned))
    )
    source_profile_mae = None
    if source_profile_observed_mix_pct is not None:
        gt = _normalize_role_mix(source_profile_observed_mix_pct)
        common = set(p) | set(gt)
        source_profile_mae = sum(
            abs(p.get(role, 0.0) - gt.get(role, 0.0)) for role in common
        ) / max(1, len(common))
    score = max(0.0, min(
        100.0,
        100.0 * (1.0 - role_mix_error_pp / ROLE_MIX_ERROR_NORM_PP),
    ))
    return SourceAudienceFidelity(
        score_0_100=score,
        role_mix_error_pp=role_mix_error_pp,
        target_vs_non_target_share_error_pp=target_vs_non_target_err,
        noise_meta_share_pct=(
            noise_meta_share_pct if noise_meta_share_pct is not None
            else 0.0
        ),
        audience_coverage_score=coverage,
        source_profile_mae_pp=source_profile_mae,
    )


def _normalize_role_mix(d: dict[str, float]) -> dict[str, float]:
    raw = {role: float(v) for role, v in d.items()}
    total = sum(raw.values())
    if total <= 0:
        return raw
    if 99.0 <= total <= 101.0:
        return raw
    return {role: 100.0 * v / total for role, v in raw.items()}


# ---------------------------------------------------------------------------
# 3. Reason Fidelity (placeholder)
# ---------------------------------------------------------------------------


def compute_reason_fidelity(
    *,
    predicted_objection_cluster_ids: list[str] | None,
    observed_objection_cluster_ids: list[str] | None,
    predicted_proof_need_cluster_ids: list[str] | None = None,
    observed_proof_need_cluster_ids: list[str] | None = None,
    top_k: int = 5,
) -> ReasonFidelity:
    """Score reason agreement when operator-supplied cluster labels
    exist on both sides.

    When either side has no labels (the common case today), return a
    null-score ReasonFidelity with `null_reason` set so the caller can
    surface the gap in `limited_by`.

    Cluster IDs are intentionally opaque strings — caller supplies them
    from a frozen embedding model or operator-curated taxonomy. This
    function makes no LLM calls and assumes IDs are pre-computed.
    """
    if not (predicted_objection_cluster_ids and observed_objection_cluster_ids):
        return ReasonFidelity(
            score_0_100=None,
            null_reason="no_reason_labels_for_this_case",
        )
    pred_top = predicted_objection_cluster_ids[:top_k]
    obs_top = observed_objection_cluster_ids[:top_k]
    pred_set, obs_set = set(pred_top), set(obs_top)
    intersection = len(pred_set & obs_set)
    recall = intersection / max(1, len(obs_set))
    precision = intersection / max(1, len(pred_set))
    # Rank correlation (Spearman) on the intersection. Pure-stdlib
    # implementation to keep this module dependency-light.
    pn_rank = {cid: i for i, cid in enumerate(pred_top)}
    on_rank = {cid: i for i, cid in enumerate(obs_top)}
    common = sorted(pred_set & obs_set)
    rho: float | None = None
    if len(common) >= 2:
        n = len(common)
        d2 = sum((pn_rank[c] - on_rank[c]) ** 2 for c in common)
        rho = 1.0 - (6.0 * d2) / (n * (n * n - 1))
    proof_recall = None
    if predicted_proof_need_cluster_ids and observed_proof_need_cluster_ids:
        proof_recall = (
            len(set(predicted_proof_need_cluster_ids[:top_k])
                & set(observed_proof_need_cluster_ids[:top_k]))
            / max(1, len(set(observed_proof_need_cluster_ids[:top_k])))
        )
    score = 100.0 * recall  # primary headline; precision + rank surfaced separately
    return ReasonFidelity(
        score_0_100=score,
        top_objection_recall=recall,
        top_objection_precision=precision,
        proof_need_recall=proof_recall,
        reason_rank_correlation=rho,
    )


# ---------------------------------------------------------------------------
# 4. Trajectory / Influence Fidelity
# ---------------------------------------------------------------------------


def compute_trajectory_fidelity(
    *,
    stance_transitions: dict[str, dict[str, int]] | None,
    skeptic_retention: float | None = None,
    hard_resistant_count: int | None = None,
) -> TrajectoryFidelity:
    """Score internal-coherence trajectory metrics from Phase 6 +
    Phase 12C artifacts.

    `stance_transitions[from_stance][to_stance] = count`.

    Returns warnings when implausible mass-transitions are detected
    (e.g. 5%+ of skeptics convert directly to buyer in one run).
    """
    warnings: list[str] = []
    receptive_to_buyer_pct = None
    uncertain_to_receptive_pct = None
    if stance_transitions:
        # Skeptical → buyer: realistic floor.
        skep_to_buyer = (
            stance_transitions.get("skeptical", {}).get("buyer", 0)
        )
        skep_total = sum(stance_transitions.get("skeptical", {}).values())
        if skep_total > 0:
            skep_to_buyer_pct = 100.0 * skep_to_buyer / skep_total
            if skep_to_buyer_pct > UNREALISTIC_CONVERSION_SKEPTICAL_TO_BUYER_PCT:
                warnings.append(
                    f"unrealistic_skeptical_to_buyer_conversion_pct="
                    f"{skep_to_buyer_pct:.1f}"
                )
        # Receptive → buyer / uncertain → receptive (informational).
        rec_total = sum(
            stance_transitions.get("receptive", {}).values()
        )
        if rec_total > 0:
            receptive_to_buyer_pct = (
                100.0 * stance_transitions.get("receptive", {}).get("buyer", 0)
                / rec_total
            )
        unc_total = sum(
            stance_transitions.get("uncertain", {}).values()
        )
        if unc_total > 0:
            uncertain_to_receptive_pct = (
                100.0 * stance_transitions.get("uncertain", {}).get("receptive", 0)
                / unc_total
            )
    # Composite score: 50 baseline, ±25 by signal availability.
    score = 50.0
    if skeptic_retention is not None:
        score += (
            25.0 if skeptic_retention >= SKEPTIC_RETENTION_HEALTHY_FLOOR
            else -25.0
        )
    if warnings:
        score -= 25.0 * len(warnings)
    score = max(0.0, min(100.0, score))
    return TrajectoryFidelity(
        score_0_100=score if (
            skeptic_retention is not None or stance_transitions
        ) else None,
        skeptic_retention=skeptic_retention,
        hard_resistant_preservation_count=hard_resistant_count,
        receptive_to_buyer_conversion_pct=receptive_to_buyer_pct,
        uncertain_to_receptive_conversion_pct=uncertain_to_receptive_pct,
        unrealistic_conversion_warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 5. Stability Fidelity
# ---------------------------------------------------------------------------


def compute_stability_fidelity(
    *,
    per_run_predicted_pct: list[dict[str, float]],
    per_run_mae_pp: list[float] | None = None,
    observed_pct: dict[str, float] | None = None,
) -> StabilityFidelity:
    """Score variance across N runs. Returns null-score for N<2."""
    n = len(per_run_predicted_pct)
    if n < 2:
        return StabilityFidelity(
            score_0_100=None,
            n_runs=n,
            mae_stdev_pp=None,
            run_to_run_tvd_max=None,
            max_bucket_range_pp=None,
            bucket_sign_flip_rate=None,
        )
    runs_norm = [_safe_pct_dist(r) for r in per_run_predicted_pct]
    # Per-bucket range across runs.
    max_range_pp = max(
        max(r[b] for r in runs_norm) - min(r[b] for r in runs_norm)
        for b in BUCKETS
    )
    # Pairwise TVD.
    tvds: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            tvds.append(_tvd(runs_norm[i], runs_norm[j]))
    tvd_max = max(tvds) if tvds else 0.0
    # MAE stdev.
    mae_stdev = (
        statistics.stdev(per_run_mae_pp) if per_run_mae_pp
        and len(per_run_mae_pp) > 1 else None
    )
    # Sign-flip rate (only meaningful if observed_pct is provided).
    sign_flip_rate: float | None = None
    if observed_pct is not None:
        o = _safe_pct_dist(observed_pct)
        flips = 0
        total = 0
        for i in range(n):
            for j in range(i + 1, n):
                for b in BUCKETS:
                    si = (runs_norm[i][b] - o[b])
                    sj = (runs_norm[j][b] - o[b])
                    total += 1
                    if si * sj < 0:
                        flips += 1
        sign_flip_rate = flips / max(1, total)
    score = max(0.0, min(
        100.0, 100.0 * (1.0 - max_range_pp / 15.0)
    ))
    return StabilityFidelity(
        score_0_100=score,
        n_runs=n,
        mae_stdev_pp=mae_stdev,
        run_to_run_tvd_max=tvd_max,
        max_bucket_range_pp=max_range_pp,
        bucket_sign_flip_rate=sign_flip_rate,
    )


# ---------------------------------------------------------------------------
# 6. Evidence / Anti-Leakage Fidelity
# ---------------------------------------------------------------------------


def compute_evidence_fidelity(
    *,
    prediction_artifact_hash_before: str | None,
    prediction_artifact_hash_after: str | None,
    evidence_snapshot_hash: str | None,
    brief_hash: str | None,
    outcome_after_cutoff: bool,
    leakage_audit_clean: bool,
) -> EvidenceFidelity:
    """Binary scoring: 100 if every required signal is present + clean;
    0 if any critical signal fails. No partial credit by design — there
    is no "almost not leaky"."""
    reasons: list[str] = []
    hash_unchanged = bool(
        prediction_artifact_hash_before
        and prediction_artifact_hash_after
        and prediction_artifact_hash_before == prediction_artifact_hash_after
    )
    if not hash_unchanged:
        reasons.append("prediction_artifact_hash_changed_or_missing")
    snap_present = bool(evidence_snapshot_hash)
    if not snap_present:
        reasons.append("evidence_snapshot_hash_missing")
    brief_present = bool(brief_hash)
    if not brief_present:
        reasons.append("brief_hash_missing")
    if not outcome_after_cutoff:
        reasons.append("outcome_not_strictly_after_evidence_cutoff")
    if not leakage_audit_clean:
        reasons.append("leakage_audit_failed")
    passed = not reasons
    return EvidenceFidelity(
        score_0_100=100.0 if passed else 0.0,
        leakage_audit_clean=leakage_audit_clean,
        prediction_artifact_hash_unchanged=hash_unchanged,
        evidence_snapshot_hash_present=snap_present,
        brief_hash_present=brief_present,
        outcome_after_cutoff=outcome_after_cutoff,
        critical_fail_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Aggregate score
# ---------------------------------------------------------------------------


def compute_market_fidelity_score(
    *,
    decision: DecisionFidelity | None,
    source_audience: SourceAudienceFidelity | None,
    reason: ReasonFidelity | None,
    trajectory: TrajectoryFidelity | None,
    stability: StabilityFidelity | None,
    evidence: EvidenceFidelity,
) -> dict[str, Any]:
    """Aggregate the six pillars into a single 0-100 internal score
    (Section 3 of the methodology).

    `evidence` is non-optional: if evidence fidelity is a critical fail,
    the function returns score=None + reason `evidence_leakage_invalidates_run`
    instead of a numeric score. This mirrors the gate behavior in
    Section 4 of the methodology.

    Returns a dict, not a dataclass, so callers can JSON-serialize the
    full breakdown trivially.
    """
    if evidence.score_0_100 < 100.0:
        return {
            "overall_internal_score_0_100": None,
            "withheld_reason": "evidence_leakage_invalidates_run",
            "critical_fail_reasons": evidence.critical_fail_reasons,
            "null_pillars": [],
            "per_pillar_scores": {
                "decision": getattr(decision, "score_0_100", None),
                "source_audience": getattr(source_audience, "score_0_100", None),
                "reason": getattr(reason, "score_0_100", None),
                "trajectory": getattr(trajectory, "score_0_100", None),
                "stability": getattr(stability, "score_0_100", None),
                "evidence": evidence.score_0_100,
            },
            "weights": dict(PILLAR_WEIGHTS),
        }
    per_pillar: dict[str, float | None] = {
        "decision":        getattr(decision, "score_0_100", None),
        "source_audience": getattr(source_audience, "score_0_100", None),
        "reason":          getattr(reason, "score_0_100", None),
        "trajectory":      getattr(trajectory, "score_0_100", None),
        "stability":       getattr(stability, "score_0_100", None),
        "evidence":        evidence.score_0_100,
    }
    null_pillars = [k for k, v in per_pillar.items() if v is None]
    # Redistribute the weight of null pillars proportionally over the
    # pillars that DO have a score, so a missing pillar doesn't deflate
    # the total artificially.
    active_weight_total = sum(
        PILLAR_WEIGHTS[k] for k, v in per_pillar.items() if v is not None
    )
    if active_weight_total <= 0:
        return {
            "overall_internal_score_0_100": None,
            "withheld_reason": "no_pillars_scored",
            "null_pillars": null_pillars,
            "per_pillar_scores": per_pillar,
            "weights": dict(PILLAR_WEIGHTS),
        }
    overall = sum(
        per_pillar[k] * (PILLAR_WEIGHTS[k] / active_weight_total)
        for k, v in per_pillar.items() if v is not None
    )
    return {
        "overall_internal_score_0_100": round(overall, 2),
        "null_pillars": null_pillars,
        "per_pillar_scores": per_pillar,
        "weights": dict(PILLAR_WEIGHTS),
    }


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def evaluate_fidelity_gates(
    *,
    decision: DecisionFidelity | None,
    stability: StabilityFidelity | None,
    evidence: EvidenceFidelity,
) -> dict[str, Any]:
    """Evaluate the PASS/WARN/CRITICAL_FAIL gates from Section 4 of the
    methodology. Returns a dict per gate so callers can JSON-emit it
    into a Proof Packet.

    `evidence` is required; `decision` / `stability` may be None when
    no labels / single-run data are available.
    """
    gates: dict[str, dict[str, Any]] = {}
    # Evidence gates (run first — a critical fail invalidates everything).
    ev_status = "pass" if evidence.score_0_100 >= 100.0 else "critical_fail"
    gates["evidence"] = {
        "status": ev_status,
        "critical_fail_reasons": evidence.critical_fail_reasons,
    }
    overall_critical = bool(evidence.critical_fail_reasons)
    # Decision gates
    if decision is None:
        gates["decision_band"] = {
            "status": "n/a", "reason": "no_decision_data",
        }
        gates["buyer_false_confidence"] = {"status": "n/a"}
        gates["skeptic_underprediction"] = {"status": "n/a"}
        gates["uncertain_injection"] = {"status": "n/a"}
        gates["max_bucket_error"] = {"status": "n/a"}
    else:
        gates["decision_band"] = {
            "status": "pass" if decision.band in ("strong", "promising") else "warn",
            "band": decision.band, "mae_pp": decision.mae_pp,
            "thresholds": {
                "strong_le_pp": MAE_STRONG_PP,
                "promising_le_pp": MAE_PROMISING_PP,
                "weak_le_pp": MAE_WEAK_PP,
            },
        }
        if decision.band == "fail":
            gates["decision_band"]["status"] = "fail"
        # Buyer false confidence — CRITICAL.
        if decision.buyer_false_confidence:
            gates["buyer_false_confidence"] = {
                "status": "critical_fail",
                "threshold_pred_pct": BUYER_FALSE_CONFIDENCE_PRED_PCT,
                "threshold_obs_pct": BUYER_FALSE_CONFIDENCE_OBS_PCT,
            }
            overall_critical = True
        else:
            gates["buyer_false_confidence"] = {"status": "pass"}
        # Skeptic underprediction
        if decision.skeptic_underprediction_pp > SKEPTIC_UNDERPRED_WARN_PP:
            gates["skeptic_underprediction"] = {
                "status": "warn",
                "delta_pp": decision.skeptic_underprediction_pp,
                "threshold_pp": SKEPTIC_UNDERPRED_WARN_PP,
            }
        else:
            gates["skeptic_underprediction"] = {"status": "pass"}
        # Uncertain over/under injection
        if abs(decision.uncertain_injection_pp) > UNCERTAIN_INJECTION_WARN_PP:
            gates["uncertain_injection"] = {
                "status": "warn",
                "delta_pp": decision.uncertain_injection_pp,
                "threshold_pp": UNCERTAIN_INJECTION_WARN_PP,
            }
        else:
            gates["uncertain_injection"] = {"status": "pass"}
        # Max bucket error
        if decision.max_bucket_error_pp > MAX_BUCKET_ERROR_FAIL_PP:
            gates["max_bucket_error"] = {
                "status": "fail",
                "value_pp": decision.max_bucket_error_pp,
                "threshold_pp": MAX_BUCKET_ERROR_FAIL_PP,
            }
        elif decision.max_bucket_error_pp > MAX_BUCKET_ERROR_WARN_PP:
            gates["max_bucket_error"] = {
                "status": "warn",
                "value_pp": decision.max_bucket_error_pp,
                "threshold_pp": MAX_BUCKET_ERROR_WARN_PP,
            }
        else:
            gates["max_bucket_error"] = {"status": "pass"}
    # Stability gates
    if stability is None or stability.score_0_100 is None:
        gates["stability_range"] = {"status": "n/a", "reason": "n_runs_lt_2"}
        gates["stability_tvd"] = {"status": "n/a"}
        gates["stability_sign_flip"] = {"status": "n/a"}
    else:
        # max_bucket_range
        if (stability.max_bucket_range_pp or 0.0) > MAX_BUCKET_RANGE_FAIL_PP:
            gates["stability_range"] = {
                "status": "fail",
                "value_pp": stability.max_bucket_range_pp,
                "threshold_pp": MAX_BUCKET_RANGE_FAIL_PP,
            }
        elif (stability.max_bucket_range_pp or 0.0) > MAX_BUCKET_RANGE_WARN_PP:
            gates["stability_range"] = {
                "status": "warn",
                "value_pp": stability.max_bucket_range_pp,
                "threshold_pp": MAX_BUCKET_RANGE_WARN_PP,
            }
        else:
            gates["stability_range"] = {"status": "pass"}
        # run-to-run TVD
        if (stability.run_to_run_tvd_max or 0.0) > RUN_TO_RUN_TVD_WARN:
            gates["stability_tvd"] = {
                "status": "warn",
                "value": stability.run_to_run_tvd_max,
                "threshold": RUN_TO_RUN_TVD_WARN,
            }
        else:
            gates["stability_tvd"] = {"status": "pass"}
        # sign-flip rate
        sfr = stability.bucket_sign_flip_rate
        if sfr is not None and sfr > BUCKET_SIGN_FLIP_WARN_RATE:
            gates["stability_sign_flip"] = {
                "status": "warn",
                "rate": sfr, "threshold": BUCKET_SIGN_FLIP_WARN_RATE,
            }
        else:
            gates["stability_sign_flip"] = {"status": "pass"}
    statuses = [g.get("status", "pass") for g in gates.values()]
    overall_band = (
        "critical_fail" if overall_critical or "critical_fail" in statuses
        else "fail" if "fail" in statuses
        else "warn" if "warn" in statuses
        else "pass"
    )
    gates["overall_band"] = {"status": overall_band}
    return gates


# ---------------------------------------------------------------------------
# Convenience: single entry point that runs everything available.
# ---------------------------------------------------------------------------


def score_case(
    *,
    predicted_pct: dict[str, float],
    observed_pct: dict[str, float] | None,
    planned_role_mix_pct: dict[str, float] | None,
    realized_role_mix_pct: dict[str, float] | None,
    noise_meta_share_pct: float | None,
    per_run_predicted_pct: list[dict[str, float]] | None,
    per_run_mae_pp: list[float] | None,
    stance_transitions: dict[str, dict[str, int]] | None,
    skeptic_retention: float | None,
    hard_resistant_count: int | None,
    predicted_objection_cluster_ids: list[str] | None,
    observed_objection_cluster_ids: list[str] | None,
    prediction_artifact_hash_before: str | None,
    prediction_artifact_hash_after: str | None,
    evidence_snapshot_hash: str | None,
    brief_hash: str | None,
    outcome_after_cutoff: bool,
    leakage_audit_clean: bool,
) -> dict[str, Any]:
    """One-shot scoring across all six pillars + gates + aggregate.

    Designed for the Phase 12E.5B recalibration script and the Proof
    Packet builder. Returns a JSON-safe dict.

    Each pillar is computed only if its inputs are sufficient — missing
    inputs degrade gracefully into a `null` score for that pillar.
    """
    decision = (
        compute_decision_fidelity(
            predicted_pct=predicted_pct, observed_pct=observed_pct,
        )
        if observed_pct is not None else None
    )
    src_aud = (
        compute_source_audience_fidelity(
            planned_role_mix_pct=planned_role_mix_pct,
            realized_role_mix_pct=realized_role_mix_pct,
            noise_meta_share_pct=noise_meta_share_pct,
        )
        if (planned_role_mix_pct is not None
            and realized_role_mix_pct is not None) else None
    )
    reason = compute_reason_fidelity(
        predicted_objection_cluster_ids=predicted_objection_cluster_ids,
        observed_objection_cluster_ids=observed_objection_cluster_ids,
    )
    trajectory = compute_trajectory_fidelity(
        stance_transitions=stance_transitions,
        skeptic_retention=skeptic_retention,
        hard_resistant_count=hard_resistant_count,
    )
    stability = (
        compute_stability_fidelity(
            per_run_predicted_pct=per_run_predicted_pct,
            per_run_mae_pp=per_run_mae_pp,
            observed_pct=observed_pct,
        )
        if per_run_predicted_pct is not None else None
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before=prediction_artifact_hash_before,
        prediction_artifact_hash_after=prediction_artifact_hash_after,
        evidence_snapshot_hash=evidence_snapshot_hash,
        brief_hash=brief_hash,
        outcome_after_cutoff=outcome_after_cutoff,
        leakage_audit_clean=leakage_audit_clean,
    )
    score = compute_market_fidelity_score(
        decision=decision, source_audience=src_aud, reason=reason,
        trajectory=trajectory, stability=stability, evidence=evidence,
    )
    gates = evaluate_fidelity_gates(
        decision=decision, stability=stability, evidence=evidence,
    )
    return {
        "schema_version": "12e.5a.v1",
        "decision": decision.__dict__ if decision else None,
        "source_audience": src_aud.__dict__ if src_aud else None,
        "reason": reason.__dict__,
        "trajectory": trajectory.__dict__,
        "stability": stability.__dict__ if stability else None,
        "evidence": evidence.__dict__,
        "aggregate_score": score,
        "gates": gates,
    }


__all__ = [
    # Constants (configurable thresholds)
    "MAE_STRONG_PP", "MAE_PROMISING_PP", "MAE_WEAK_PP",
    "MAX_BUCKET_ERROR_WARN_PP", "MAX_BUCKET_ERROR_FAIL_PP",
    "BUYER_FALSE_CONFIDENCE_PRED_PCT", "BUYER_FALSE_CONFIDENCE_OBS_PCT",
    "SKEPTIC_UNDERPRED_WARN_PP", "UNCERTAIN_INJECTION_WARN_PP",
    "RUN_TO_RUN_TVD_WARN", "MAX_BUCKET_RANGE_WARN_PP",
    "MAX_BUCKET_RANGE_FAIL_PP", "BUCKET_SIGN_FLIP_WARN_RATE",
    "ROLE_MIX_ERROR_NORM_PP",
    "SKEPTIC_RETENTION_HEALTHY_FLOOR",
    "UNREALISTIC_CONVERSION_SKEPTICAL_TO_BUYER_PCT",
    "PILLAR_WEIGHTS", "BUCKETS",
    # Pillar functions
    "compute_decision_fidelity",
    "compute_source_audience_fidelity",
    "compute_reason_fidelity",
    "compute_trajectory_fidelity",
    "compute_stability_fidelity",
    "compute_evidence_fidelity",
    # Aggregates
    "compute_market_fidelity_score",
    "evaluate_fidelity_gates",
    "score_case",
    # Dataclasses
    "DecisionFidelity", "SourceAudienceFidelity", "ReasonFidelity",
    "TrajectoryFidelity", "StabilityFidelity", "EvidenceFidelity",
]
