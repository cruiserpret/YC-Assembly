"""Phase 12E.5A — Assembly Market Fidelity scoring tests.

Covers the pure-function module
`apps/api/src/assembly/calibration/market_fidelity.py`. No DB, no LLM,
no network.

Test groups:
  A. Decision-fidelity numeric correctness (MAE / TVD / band / signed errors).
  B. Source-audience-fidelity numeric correctness.
  C. Reason-fidelity null-degradation + happy path.
  D. Trajectory-fidelity warnings.
  E. Stability-fidelity (N<2 → null; N>=2 → range/TVD/sign-flip).
  F. Evidence-fidelity binary scoring (clean = 100, any leak = 0).
  G. Aggregate score: weights sum to 1; bounded [0, 100];
     null-pillar redistribution; evidence-leak withholds the score.
  H. Gates: buyer false confidence = critical_fail; leakage = critical;
     stability range > FAIL threshold = fail; warn vs pass thresholds.
  I. Discipline: no provider imports, no apps/web, no DB migration.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest

from assembly.calibration.market_fidelity import (
    BUCKETS,
    BUYER_FALSE_CONFIDENCE_OBS_PCT,
    BUYER_FALSE_CONFIDENCE_PRED_PCT,
    MAE_PROMISING_PP,
    MAE_STRONG_PP,
    MAE_WEAK_PP,
    MAX_BUCKET_ERROR_FAIL_PP,
    MAX_BUCKET_ERROR_WARN_PP,
    MAX_BUCKET_RANGE_FAIL_PP,
    MAX_BUCKET_RANGE_WARN_PP,
    PILLAR_WEIGHTS,
    ROLE_MIX_ERROR_NORM_PP,
    RUN_TO_RUN_TVD_WARN,
    SKEPTIC_UNDERPRED_WARN_PP,
    UNCERTAIN_INJECTION_WARN_PP,
    compute_decision_fidelity,
    compute_evidence_fidelity,
    compute_market_fidelity_score,
    compute_reason_fidelity,
    compute_source_audience_fidelity,
    compute_stability_fidelity,
    compute_trajectory_fidelity,
    evaluate_fidelity_gates,
    score_case,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Group A — Decision Fidelity
# ---------------------------------------------------------------------------


def test_decision_fidelity_exact_match_yields_zero_error_and_100_score():
    d = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    assert d.mae_pp == pytest.approx(0.0)
    assert d.tvd == pytest.approx(0.0)
    assert d.max_bucket_error_pp == pytest.approx(0.0)
    assert d.score_0_100 == pytest.approx(100.0)
    assert d.band == "strong"
    assert d.buyer_false_confidence is False


def test_decision_fidelity_mae_tvd_relationship():
    """For 4 buckets, TVD = (MAE * 4) / (2 * 100) = MAE / 50."""
    d = compute_decision_fidelity(
        predicted_pct={"buyer": 0, "receptive": 70, "uncertain": 0, "skeptical": 30},
        observed_pct={"buyer": 1, "receptive": 44, "uncertain": 18, "skeptical": 37},
    )
    # |0-1| + |70-44| + |0-18| + |30-37| = 1 + 26 + 18 + 7 = 52
    # MAE = 52 / 4 = 13.0
    # TVD = 52 / 200 = 0.26
    assert d.mae_pp == pytest.approx(13.0)
    assert d.tvd == pytest.approx(0.26)
    assert d.max_bucket_error_pp == pytest.approx(26.0)
    assert d.band == "weak"  # >12, <=18


def test_decision_fidelity_signed_errors_carry_sign():
    d = compute_decision_fidelity(
        predicted_pct={"buyer": 0, "receptive": 70, "uncertain": 0, "skeptical": 30},
        observed_pct={"buyer": 1, "receptive": 44, "uncertain": 18, "skeptical": 37},
    )
    assert d.signed_errors_pp["buyer"] == pytest.approx(-1.0)
    assert d.signed_errors_pp["receptive"] == pytest.approx(26.0)
    assert d.signed_errors_pp["uncertain"] == pytest.approx(-18.0)
    assert d.signed_errors_pp["skeptical"] == pytest.approx(-7.0)
    # uncertain over/under injection should be negative (under-prediction).
    assert d.uncertain_injection_pp == pytest.approx(-18.0)
    # Skeptic underprediction = obs - pred (positive = underpredicted).
    assert d.skeptic_underprediction_pp == pytest.approx(7.0)


def test_decision_fidelity_band_transitions():
    """Verify the 4 band thresholds (strong / promising / weak / fail)."""
    # MAE 5.0 → strong
    d1 = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 20, "skeptical": 30},
        observed_pct={"buyer": 5, "receptive": 45, "uncertain": 25, "skeptical": 25},
    )
    assert d1.mae_pp == pytest.approx(5.0)
    assert d1.band == "strong"
    # MAE 10.0 → promising
    d2 = compute_decision_fidelity(
        predicted_pct={"buyer": 20, "receptive": 40, "uncertain": 30, "skeptical": 10},
        observed_pct={"buyer": 10, "receptive": 50, "uncertain": 20, "skeptical": 20},
    )
    assert d2.mae_pp == pytest.approx(10.0)
    assert d2.band == "promising"
    # MAE 25.0 → fail (over MAE_WEAK_PP)
    d3 = compute_decision_fidelity(
        predicted_pct={"buyer": 50, "receptive": 50, "uncertain": 0, "skeptical": 0},
        observed_pct={"buyer": 0, "receptive": 0, "uncertain": 50, "skeptical": 50},
    )
    assert d3.mae_pp == pytest.approx(50.0)
    assert d3.band == "fail"


def test_decision_fidelity_score_is_bounded():
    """Score must always live in [0, 100], even for terrible predictions."""
    d = compute_decision_fidelity(
        predicted_pct={"buyer": 100, "receptive": 0, "uncertain": 0, "skeptical": 0},
        observed_pct={"buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 100},
    )
    assert 0.0 <= d.score_0_100 <= 100.0


def test_decision_fidelity_handles_count_inputs():
    """Inputs may be counts (sum != 100); helper normalizes to percent."""
    d_pct = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    d_count = compute_decision_fidelity(
        predicted_pct={"buyer": 5, "receptive": 20, "uncertain": 15, "skeptical": 10},
        observed_pct={"buyer": 2, "receptive": 8, "uncertain": 6, "skeptical": 4},
    )
    assert d_pct.mae_pp == pytest.approx(d_count.mae_pp)


# ---------------------------------------------------------------------------
# Group B — Source-Audience Fidelity
# ---------------------------------------------------------------------------


def test_source_audience_perfect_match():
    s = compute_source_audience_fidelity(
        planned_role_mix_pct={
            "target_customer_evaluator": 22.0,
            "existing_competitor_user": 15.0,
            "proof_seeker_only": 12.0,
            "industry_observer": 18.0,
        },
        realized_role_mix_pct={
            "target_customer_evaluator": 22.0,
            "existing_competitor_user": 15.0,
            "proof_seeker_only": 12.0,
            "industry_observer": 18.0,
        },
        noise_meta_share_pct=10.0,
    )
    assert s.role_mix_error_pp == pytest.approx(0.0)
    assert s.target_vs_non_target_share_error_pp == pytest.approx(0.0)
    assert s.audience_coverage_score == pytest.approx(1.0)
    assert s.score_0_100 == pytest.approx(100.0)


def test_source_audience_coverage_penalizes_missing_roles():
    """A planned role with weight > 0 that has 0 realized personas
    lowers the audience_coverage_score."""
    s = compute_source_audience_fidelity(
        planned_role_mix_pct={"role_a": 50, "role_b": 30, "role_c": 20},
        realized_role_mix_pct={"role_a": 70, "role_b": 30, "role_c": 0},
        noise_meta_share_pct=0.0,
    )
    # Two of three planned non-zero roles realized → 2/3 coverage.
    assert s.audience_coverage_score == pytest.approx(2 / 3)


def test_source_audience_role_mix_error_is_sum_of_abs_diffs():
    s = compute_source_audience_fidelity(
        planned_role_mix_pct={"role_a": 50, "role_b": 50},
        realized_role_mix_pct={"role_a": 60, "role_b": 40},
        noise_meta_share_pct=0.0,
    )
    # |50-60| + |50-40| = 20
    assert s.role_mix_error_pp == pytest.approx(20.0)


def test_source_audience_score_is_bounded():
    """Even with maximally-wrong role mix, score stays in [0, 100]."""
    s = compute_source_audience_fidelity(
        planned_role_mix_pct={"role_a": 100, "role_b": 0},
        realized_role_mix_pct={"role_a": 0, "role_b": 100},
        noise_meta_share_pct=0.0,
    )
    assert 0.0 <= s.score_0_100 <= 100.0


# ---------------------------------------------------------------------------
# Group C — Reason Fidelity
# ---------------------------------------------------------------------------


def test_reason_fidelity_returns_null_when_no_labels():
    r = compute_reason_fidelity(
        predicted_objection_cluster_ids=None,
        observed_objection_cluster_ids=None,
    )
    assert r.score_0_100 is None
    assert r.null_reason == "no_reason_labels_for_this_case"


def test_reason_fidelity_recall_precision_happy_path():
    r = compute_reason_fidelity(
        predicted_objection_cluster_ids=["a", "b", "c", "d", "e"],
        observed_objection_cluster_ids=["a", "b", "x"],
        top_k=5,
    )
    # intersection = {a, b} → recall = 2/3, precision = 2/5
    assert r.top_objection_recall == pytest.approx(2 / 3)
    assert r.top_objection_precision == pytest.approx(2 / 5)
    assert r.score_0_100 == pytest.approx(100.0 * 2 / 3)


def test_reason_fidelity_rank_correlation_perfect():
    r = compute_reason_fidelity(
        predicted_objection_cluster_ids=["a", "b", "c"],
        observed_objection_cluster_ids=["a", "b", "c"],
    )
    assert r.reason_rank_correlation == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Group D — Trajectory Fidelity
# ---------------------------------------------------------------------------


def test_trajectory_returns_null_when_no_signal():
    t = compute_trajectory_fidelity(
        stance_transitions=None, skeptic_retention=None,
        hard_resistant_count=None,
    )
    assert t.score_0_100 is None


def test_trajectory_skeptic_retention_above_floor_credits_score():
    t = compute_trajectory_fidelity(
        stance_transitions=None, skeptic_retention=0.8,
        hard_resistant_count=10,
    )
    # Baseline 50 + 25 credit for healthy skeptic retention.
    assert t.score_0_100 == pytest.approx(75.0)


def test_trajectory_unrealistic_skeptical_to_buyer_warns():
    transitions = {
        "skeptical": {"skeptical": 10, "buyer": 5},  # 5/15 ≈ 33% → way above 5%
        "receptive": {"receptive": 10},
    }
    t = compute_trajectory_fidelity(
        stance_transitions=transitions, skeptic_retention=0.8,
        hard_resistant_count=5,
    )
    assert len(t.unrealistic_conversion_warnings) >= 1
    assert "skeptical_to_buyer" in t.unrealistic_conversion_warnings[0]
    # Score should be penalized by 25 per warning, then clipped to >= 0.
    assert t.score_0_100 <= 50.0


# ---------------------------------------------------------------------------
# Group E — Stability Fidelity
# ---------------------------------------------------------------------------


def test_stability_returns_null_for_n1():
    s = compute_stability_fidelity(
        per_run_predicted_pct=[
            {"buyer": 10, "receptive": 30, "uncertain": 30, "skeptical": 30},
        ],
        per_run_mae_pp=[5.0],
        observed_pct=None,
    )
    assert s.score_0_100 is None
    assert s.n_runs == 1


def test_stability_identical_runs_score_perfect():
    """Three identical runs should score 100 (max_bucket_range = 0)."""
    run = {"buyer": 10, "receptive": 30, "uncertain": 30, "skeptical": 30}
    s = compute_stability_fidelity(
        per_run_predicted_pct=[run, dict(run), dict(run)],
        per_run_mae_pp=[8.0, 8.0, 8.0],
        observed_pct={"buyer": 5, "receptive": 35, "uncertain": 30, "skeptical": 30},
    )
    assert s.score_0_100 == pytest.approx(100.0)
    assert s.max_bucket_range_pp == pytest.approx(0.0)
    assert s.run_to_run_tvd_max == pytest.approx(0.0)


def test_stability_high_variance_lowers_score():
    s = compute_stability_fidelity(
        per_run_predicted_pct=[
            {"buyer": 10, "receptive": 50, "uncertain": 20, "skeptical": 20},
            {"buyer": 30, "receptive": 30, "uncertain": 20, "skeptical": 20},
            {"buyer": 5, "receptive": 65, "uncertain": 15, "skeptical": 15},
        ],
        per_run_mae_pp=[10.0, 14.0, 8.0],
        observed_pct={"buyer": 10, "receptive": 50, "uncertain": 20, "skeptical": 20},
    )
    # max_bucket_range_pp = max range for any bucket, which is receptive 65-30=35
    assert s.max_bucket_range_pp == pytest.approx(35.0)
    assert s.score_0_100 == pytest.approx(0.0)  # clipped to 0


def test_stability_sign_flip_rate_in_zero_to_one():
    s = compute_stability_fidelity(
        per_run_predicted_pct=[
            {"buyer": 10, "receptive": 40, "uncertain": 20, "skeptical": 30},
            {"buyer": 20, "receptive": 30, "uncertain": 30, "skeptical": 20},
        ],
        per_run_mae_pp=[5.0, 7.0],
        observed_pct={"buyer": 15, "receptive": 35, "uncertain": 25, "skeptical": 25},
    )
    assert 0.0 <= (s.bucket_sign_flip_rate or 0.0) <= 1.0


# ---------------------------------------------------------------------------
# Group F — Evidence Fidelity (binary)
# ---------------------------------------------------------------------------


def test_evidence_clean_run_scores_100():
    e = compute_evidence_fidelity(
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="abc",
        evidence_snapshot_hash="snap",
        brief_hash="brief",
        outcome_after_cutoff=True,
        leakage_audit_clean=True,
    )
    assert e.score_0_100 == 100.0
    assert e.critical_fail_reasons == []
    assert e.prediction_artifact_hash_unchanged is True


def test_evidence_hash_changed_critical_fail():
    e = compute_evidence_fidelity(
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="def",  # tampered
        evidence_snapshot_hash="snap",
        brief_hash="brief",
        outcome_after_cutoff=True,
        leakage_audit_clean=True,
    )
    assert e.score_0_100 == 0.0
    assert "prediction_artifact_hash_changed_or_missing" in e.critical_fail_reasons


def test_evidence_missing_snapshot_hash_critical_fail():
    e = compute_evidence_fidelity(
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="abc",
        evidence_snapshot_hash=None,
        brief_hash="brief",
        outcome_after_cutoff=True,
        leakage_audit_clean=True,
    )
    assert e.score_0_100 == 0.0
    assert "evidence_snapshot_hash_missing" in e.critical_fail_reasons


def test_evidence_outcome_before_cutoff_critical_fail():
    e = compute_evidence_fidelity(
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="abc",
        evidence_snapshot_hash="snap",
        brief_hash="brief",
        outcome_after_cutoff=False,  # temporal leak
        leakage_audit_clean=True,
    )
    assert e.score_0_100 == 0.0
    assert "outcome_not_strictly_after_evidence_cutoff" in e.critical_fail_reasons


def test_evidence_leakage_audit_fail_critical():
    e = compute_evidence_fidelity(
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="abc",
        evidence_snapshot_hash="snap",
        brief_hash="brief",
        outcome_after_cutoff=True,
        leakage_audit_clean=False,  # forbidden keys in brief
    )
    assert e.score_0_100 == 0.0
    assert "leakage_audit_failed" in e.critical_fail_reasons


# ---------------------------------------------------------------------------
# Group G — Aggregate score
# ---------------------------------------------------------------------------


def test_pillar_weights_sum_to_one():
    assert math.isclose(sum(PILLAR_WEIGHTS.values()), 1.0, rel_tol=1e-9)


def test_aggregate_score_all_pillars_perfect_yields_100():
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    src = compute_source_audience_fidelity(
        planned_role_mix_pct={"r1": 100},
        realized_role_mix_pct={"r1": 100},
        noise_meta_share_pct=0.0,
    )
    reason = compute_reason_fidelity(
        predicted_objection_cluster_ids=["a", "b"],
        observed_objection_cluster_ids=["a", "b"],
    )
    trajectory = compute_trajectory_fidelity(
        stance_transitions={"skeptical": {"skeptical": 10}},
        skeptic_retention=1.0, hard_resistant_count=10,
    )
    stab_run = {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20}
    stability = compute_stability_fidelity(
        per_run_predicted_pct=[stab_run, dict(stab_run)],
        per_run_mae_pp=[0.0, 0.0],
        observed_pct=stab_run,
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    out = compute_market_fidelity_score(
        decision=decision, source_audience=src, reason=reason,
        trajectory=trajectory, stability=stability, evidence=evidence,
    )
    # Trajectory baseline is 75 even on perfect signal (50 + 25 retention
    # credit), so the aggregate will be slightly below 100. Confirm in
    # a reasonable band.
    assert out["overall_internal_score_0_100"] is not None
    assert 90.0 <= out["overall_internal_score_0_100"] <= 100.0
    assert out["null_pillars"] == []


def test_aggregate_score_bounded_0_to_100():
    """Aggregate must always be in [0, 100] regardless of inputs."""
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 100, "receptive": 0, "uncertain": 0, "skeptical": 0},
        observed_pct={"buyer": 0, "receptive": 0, "uncertain": 0, "skeptical": 100},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    out = compute_market_fidelity_score(
        decision=decision, source_audience=None, reason=None,
        trajectory=None, stability=None, evidence=evidence,
    )
    score = out["overall_internal_score_0_100"]
    assert score is None or (0.0 <= score <= 100.0)


def test_aggregate_score_evidence_leak_withholds_score():
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a",
        prediction_artifact_hash_after="DIFFERENT",  # tampered
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    out = compute_market_fidelity_score(
        decision=decision, source_audience=None, reason=None,
        trajectory=None, stability=None, evidence=evidence,
    )
    # Score withheld; specific reason reported.
    assert out["overall_internal_score_0_100"] is None
    assert out["withheld_reason"] == "evidence_leakage_invalidates_run"


def test_aggregate_null_pillars_redistribute_weight():
    """A null pillar must not deflate the aggregate; its weight is
    redistributed proportionally over the present pillars."""
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    out = compute_market_fidelity_score(
        decision=decision, source_audience=None, reason=None,
        trajectory=None, stability=None, evidence=evidence,
    )
    # Two pillars (decision + evidence) both at 100 → aggregate = 100.
    assert out["overall_internal_score_0_100"] == pytest.approx(100.0)
    assert sorted(out["null_pillars"]) == [
        "reason", "source_audience", "stability", "trajectory",
    ]


# ---------------------------------------------------------------------------
# Group H — Gates
# ---------------------------------------------------------------------------


def test_gate_buyer_false_confidence_critical_fail():
    decision = compute_decision_fidelity(
        # predicted buyer 15%, observed buyer 0.5% → triggers the gate.
        predicted_pct={"buyer": 15, "receptive": 40, "uncertain": 25, "skeptical": 20},
        observed_pct={"buyer": 0.5, "receptive": 40, "uncertain": 30, "skeptical": 29.5},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    gates = evaluate_fidelity_gates(
        decision=decision, stability=None, evidence=evidence,
    )
    assert gates["buyer_false_confidence"]["status"] == "critical_fail"
    assert gates["overall_band"]["status"] == "critical_fail"


def test_gate_leakage_critical_fail_independent_of_decision():
    """Even if decision is perfect, evidence-leak still critical-fails."""
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="DIFFERENT",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    gates = evaluate_fidelity_gates(
        decision=decision, stability=None, evidence=evidence,
    )
    assert gates["evidence"]["status"] == "critical_fail"
    assert gates["overall_band"]["status"] == "critical_fail"


def test_gate_stability_range_warning_and_fail_thresholds():
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    # Range of 6pp → > 5pp warn, ≤ 10pp fail → WARN.
    stab_warn = compute_stability_fidelity(
        per_run_predicted_pct=[
            {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
            {"buyer": 16, "receptive": 40, "uncertain": 24, "skeptical": 20},
        ],
        per_run_mae_pp=[5.0, 7.0],
        observed_pct={"buyer": 12, "receptive": 40, "uncertain": 28, "skeptical": 20},
    )
    g_warn = evaluate_fidelity_gates(
        decision=decision, stability=stab_warn, evidence=evidence,
    )
    assert g_warn["stability_range"]["status"] == "warn"
    # Range of 15pp → FAIL.
    stab_fail = compute_stability_fidelity(
        per_run_predicted_pct=[
            {"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
            {"buyer": 25, "receptive": 40, "uncertain": 15, "skeptical": 20},
        ],
        per_run_mae_pp=[5.0, 12.0],
        observed_pct={"buyer": 12, "receptive": 40, "uncertain": 28, "skeptical": 20},
    )
    g_fail = evaluate_fidelity_gates(
        decision=decision, stability=stab_fail, evidence=evidence,
    )
    assert g_fail["stability_range"]["status"] == "fail"


def test_gate_uncertain_over_injection_warn():
    decision = compute_decision_fidelity(
        # predicted uncertain 39%, observed 18% → +21pp → warn.
        predicted_pct={"buyer": 0, "receptive": 29, "uncertain": 39, "skeptical": 32},
        observed_pct={"buyer": 1, "receptive": 44, "uncertain": 18, "skeptical": 37},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    gates = evaluate_fidelity_gates(
        decision=decision, stability=None, evidence=evidence,
    )
    assert gates["uncertain_injection"]["status"] == "warn"


def test_gate_skeptic_underprediction_warn():
    decision = compute_decision_fidelity(
        # predicted skeptical 20%, observed 35% → underpred by 15pp.
        predicted_pct={"buyer": 5, "receptive": 50, "uncertain": 25, "skeptical": 20},
        observed_pct={"buyer": 5, "receptive": 30, "uncertain": 30, "skeptical": 35},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    gates = evaluate_fidelity_gates(
        decision=decision, stability=None, evidence=evidence,
    )
    assert gates["skeptic_underprediction"]["status"] == "warn"


def test_gate_pass_band_overall():
    decision = compute_decision_fidelity(
        predicted_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
        observed_pct={"buyer": 10, "receptive": 40, "uncertain": 30, "skeptical": 20},
    )
    evidence = compute_evidence_fidelity(
        prediction_artifact_hash_before="a", prediction_artifact_hash_after="a",
        evidence_snapshot_hash="s", brief_hash="b",
        outcome_after_cutoff=True, leakage_audit_clean=True,
    )
    gates = evaluate_fidelity_gates(
        decision=decision, stability=None, evidence=evidence,
    )
    assert gates["overall_band"]["status"] == "pass"


# ---------------------------------------------------------------------------
# Group I — One-shot score_case + discipline guards
# ---------------------------------------------------------------------------


def test_score_case_one_shot_returns_json_safe_dict():
    """Smoke test: score_case wires all pillars + gates into a dict
    that's JSON-serializable end-to-end (no dataclass instances leak)."""
    import json
    out = score_case(
        predicted_pct={"buyer": 0, "receptive": 29, "uncertain": 39, "skeptical": 32},
        observed_pct={"buyer": 1, "receptive": 44, "uncertain": 18, "skeptical": 37},
        planned_role_mix_pct={"target_customer_evaluator": 22, "existing_competitor_user": 15, "proof_seeker_only": 12, "industry_observer": 18},
        realized_role_mix_pct={"target_customer_evaluator": 28, "existing_competitor_user": 18, "proof_seeker_only": 8, "industry_observer": 22},
        noise_meta_share_pct=17.7,
        per_run_predicted_pct=None,
        per_run_mae_pp=None,
        stance_transitions={"skeptical": {"skeptical": 24}},
        skeptic_retention=1.0,
        hard_resistant_count=29,
        predicted_objection_cluster_ids=None,
        observed_objection_cluster_ids=None,
        prediction_artifact_hash_before="abc",
        prediction_artifact_hash_after="abc",
        evidence_snapshot_hash="snap",
        brief_hash="brief",
        outcome_after_cutoff=True,
        leakage_audit_clean=True,
    )
    s = json.dumps(out, default=str)
    assert len(s) > 100
    assert out["aggregate_score"]["overall_internal_score_0_100"] is not None
    assert out["gates"]["overall_band"]["status"] in ("pass", "warn", "fail")


def test_market_fidelity_module_has_no_provider_calls():
    p = (
        API_ROOT / "src" / "assembly" / "calibration" / "market_fidelity.py"
    )
    text = p.read_text(encoding="utf-8")
    forbidden = (
        "provider.chat(",
        "provider.structured_output(",
        ".messages.create(",
        "with_cost_guard(",
        "import anthropic",
        "from anthropic",
        "import openai",
        "from openai",
        "httpx",
        "requests.get",
        "requests.post",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"market_fidelity.py contains forbidden surface: {needle!r}"
        )


def test_no_apps_web_changes_in_phase_12e5a():
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web not present in checkout")
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "apps/web"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available")
    changes = (r.stdout or "").strip()
    if changes:
        raise AssertionError(
            f"apps/web touched during Phase 12E.5A:\n{changes}"
        )


def test_no_new_db_migration_in_12e5a():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in ("phase_12e5a", "market_fidelity", "proof_packet"):
            assert needle not in text, (
                f"unexpected migration {f.name} mentions {needle!r}"
            )


def test_thresholds_are_module_level_constants():
    """Thresholds must be tunable. Confirm they live as module-level
    constants (not buried in function bodies)."""
    for name in (
        "MAE_STRONG_PP", "MAE_PROMISING_PP", "MAE_WEAK_PP",
        "MAX_BUCKET_ERROR_WARN_PP", "MAX_BUCKET_ERROR_FAIL_PP",
        "BUYER_FALSE_CONFIDENCE_PRED_PCT",
        "BUYER_FALSE_CONFIDENCE_OBS_PCT",
        "SKEPTIC_UNDERPRED_WARN_PP",
        "UNCERTAIN_INJECTION_WARN_PP",
        "RUN_TO_RUN_TVD_WARN", "MAX_BUCKET_RANGE_WARN_PP",
        "MAX_BUCKET_RANGE_FAIL_PP", "BUCKET_SIGN_FLIP_WARN_RATE",
        "ROLE_MIX_ERROR_NORM_PP",
        "SKEPTIC_RETENTION_HEALTHY_FLOOR",
        "UNREALISTIC_CONVERSION_SKEPTICAL_TO_BUYER_PCT",
    ):
        import assembly.calibration.market_fidelity as m
        assert hasattr(m, name), f"missing constant: {name}"
        v = getattr(m, name)
        assert isinstance(v, (int, float)), f"{name} is not numeric"
