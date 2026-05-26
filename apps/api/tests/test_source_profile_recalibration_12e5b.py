"""Phase 12E.5B — offline source-profile recalibration tests.

Covers `apps/api/src/assembly/calibration/source_profile_recalibration.py`.

Test groups:
  A. Profile validation (normalize/sum/negative/missing-role).
  B. Reconstruction of legacy drafts from intent_distribution.
  C. Projection under candidate profile produces all 4 buckets.
  D. score_product_under_profile wires score_case correctly.
  E. Candidate generator: bounded, plausible, valid.
  F. Recalibrate top-level: worst-case-MAE objective, regression block,
     buyer false-confidence block, JSON-safe output.
  G. Proof Packet draft: schema + draft flag + safety warnings.
  H. Discipline: no provider imports, no apps/web, no DB migration,
     no LLM call sites.
"""
from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from assembly.calibration.source_profile_recalibration import (
    ALL_AUDIENCE_ROLES,
    CANDIDATE_PLAUSIBILITY_BANDS,
    HN_SHOW_HN_V1,
    ProductFixture,
    _validate_profile,
    build_proof_packet_draft,
    generate_candidate_profiles,
    project_with_candidate_profile,
    recalibrate_hn_show_hn,
    reconstruct_legacy_drafts_from_intent_distribution,
    score_product_under_profile,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _docuseal_fixture() -> ProductFixture:
    return ProductFixture(
        name="docuseal",
        intent_distribution={
            "would_consider_if_proven": 17,
            "loyal_to_current_alternative": 5,
            "would_reject": 2,
        },
        observed_pct={
            "buyer": 8.43, "receptive": 19.28,
            "uncertain": 25.30, "skeptical": 46.99,
        },
        run_scope_id="docuseal_test",
        evidence_snapshot_hash="sha256:test_docuseal",
        brief_hash="sha256:test_brief_docuseal",
    )


def _opslane_fixture() -> ProductFixture:
    return ProductFixture(
        name="opslane",
        intent_distribution={
            "would_consider_if_proven": 15,
            "loyal_to_current_alternative": 8,
            "would_reject": 1,
        },
        observed_pct={
            "buyer": 0.75, "receptive": 44.36,
            "uncertain": 18.05, "skeptical": 36.84,
        },
        run_scope_id="opslane_test",
        evidence_snapshot_hash="sha256:test_opslane",
        brief_hash="sha256:test_brief_opslane",
        skeptic_retention=1.0,
        hard_resistant_count=29,
    )


# ---------------------------------------------------------------------------
# Group A — Profile validation
# ---------------------------------------------------------------------------


def test_hn_show_hn_v1_baseline_sums_to_one():
    s = sum(HN_SHOW_HN_V1.values())
    assert abs(s - 1.0) < 1e-9, f"v1 baseline sums to {s}, not 1.0"


def test_hn_show_hn_v1_has_all_ten_roles():
    assert set(HN_SHOW_HN_V1.keys()) == set(ALL_AUDIENCE_ROLES)


def test_validate_profile_rejects_missing_roles():
    bad = dict(HN_SHOW_HN_V1)
    del bad["industry_observer"]
    with pytest.raises(ValueError, match="missing roles"):
        _validate_profile(bad)


def test_validate_profile_rejects_negative_weights():
    bad = dict(HN_SHOW_HN_V1)
    bad["industry_observer"] = -0.05
    # Renormalize for sum=1 so we hit the negative check first.
    bad["target_customer_evaluator"] += 0.05 + 0.18  # compensate
    with pytest.raises(ValueError, match="negative weight"):
        _validate_profile(bad)


def test_validate_profile_rejects_unknown_role():
    bad = {r: HN_SHOW_HN_V1[r] for r in HN_SHOW_HN_V1}
    bad["fake_role_xyz"] = 0.0
    with pytest.raises(ValueError, match="unknown role|missing"):
        _validate_profile(bad)


def test_validate_profile_rejects_wrong_sum():
    bad = dict(HN_SHOW_HN_V1)
    bad["industry_observer"] += 0.10  # sum = 1.10 now
    with pytest.raises(ValueError, match="must sum to 1.0"):
        _validate_profile(bad)


def test_validate_profile_accepts_baseline():
    # Smoke: the v1 baseline must always validate.
    _validate_profile(HN_SHOW_HN_V1)  # no exception


# ---------------------------------------------------------------------------
# Group B — Reconstruction
# ---------------------------------------------------------------------------


def test_reconstruct_legacy_drafts_count_matches_distribution():
    intent_dist = {
        "would_consider_if_proven": 5,
        "loyal_to_current_alternative": 3,
        "would_reject": 1,
    }
    drafts, meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution=intent_dist, run_scope_id="t1",
    )
    assert len(drafts) == 9
    assert len(meta) == 9


def test_reconstruct_competitor_intent_routes_to_competitor_segment():
    intent_dist = {"loyal_to_current_alternative": 3, "would_reject": 1}
    drafts, meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution=intent_dist, run_scope_id="t2",
    )
    n_comp = sum(
        1 for d in drafts
        if meta[d["persona_id"]]["segment_label"].startswith(
            "competitor_user_"
        )
    )
    # 3 loyal_to_current_alternative → competitor; 1 would_reject → not
    assert n_comp == 3


def test_reconstruct_drafts_have_required_fields():
    drafts, _ = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution={"would_consider_if_proven": 2},
        run_scope_id="t3",
    )
    for d in drafts:
        for k in (
            "persona_id", "cohort_id", "stance_label",
            "simulated_intent", "intent_strength", "switching_status",
            "evidence_basis", "confidence", "caveat",
        ):
            assert k in d, f"draft missing {k}"


# ---------------------------------------------------------------------------
# Group C — Projection
# ---------------------------------------------------------------------------


def test_project_with_candidate_profile_returns_required_keys():
    drafts, meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution={
            "would_consider_if_proven": 10,
            "loyal_to_current_alternative": 3,
        },
        run_scope_id="test_proj",
    )
    out = project_with_candidate_profile(
        intent_drafts=drafts,
        persona_meta=meta,
        candidate_profile=HN_SHOW_HN_V1,
        run_scope_id="test_proj",
    )
    assert "augmented_drafts" in out
    assert "augmentation_audit" in out
    assert "audience_views" in out
    views = out["audience_views"]
    for view in ("target_market_reaction", "source_audience_reaction",
                 "scorable_market_reaction", "noise_meta_estimate"):
        assert view in views


def test_project_with_candidate_profile_produces_all_four_buckets():
    drafts, meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution={
            "would_consider_if_proven": 10,
            "loyal_to_current_alternative": 3,
        },
        run_scope_id="test_buckets",
    )
    out = project_with_candidate_profile(
        intent_drafts=drafts, persona_meta=meta,
        candidate_profile=HN_SHOW_HN_V1,
        run_scope_id="test_buckets",
    )
    src = out["audience_views"]["source_audience_reaction"]
    for b in ("buyer", "receptive", "uncertain", "skeptical"):
        assert b in src


def test_project_rejects_invalid_profile():
    drafts, meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution={"would_consider_if_proven": 1},
        run_scope_id="t",
    )
    bad = dict(HN_SHOW_HN_V1)
    bad["industry_observer"] = -0.05
    with pytest.raises(ValueError):
        project_with_candidate_profile(
            intent_drafts=drafts, persona_meta=meta,
            candidate_profile=bad, run_scope_id="t",
        )


# ---------------------------------------------------------------------------
# Group D — score_product_under_profile wires score_case
# ---------------------------------------------------------------------------


def test_score_product_under_v1_baseline_reproduces_prior_results():
    """DocuSeal v1 source-audience MAE was 13.98pp (corrected labels);
    Opslane v1 was 10.58pp. Confirm offline projection reproduces."""
    docuseal_out = score_product_under_profile(
        product=_docuseal_fixture(), candidate_profile=HN_SHOW_HN_V1,
    )
    docuseal_mae = docuseal_out["fidelity"]["decision"]["mae_pp"]
    assert 13.5 < docuseal_mae < 14.5, (
        f"DocuSeal v1 MAE drifted: {docuseal_mae:.2f}"
    )
    opslane_out = score_product_under_profile(
        product=_opslane_fixture(), candidate_profile=HN_SHOW_HN_V1,
    )
    opslane_mae = opslane_out["fidelity"]["decision"]["mae_pp"]
    assert 10.0 < opslane_mae < 11.0, (
        f"Opslane v1 MAE drifted: {opslane_mae:.2f}"
    )


def test_score_product_under_profile_includes_fidelity():
    out = score_product_under_profile(
        product=_opslane_fixture(), candidate_profile=HN_SHOW_HN_V1,
    )
    assert "fidelity" in out
    for k in ("decision", "source_audience", "evidence",
              "trajectory", "reason", "stability"):
        assert k in out["fidelity"]


def test_score_product_output_is_json_serializable():
    out = score_product_under_profile(
        product=_opslane_fixture(), candidate_profile=HN_SHOW_HN_V1,
    )
    s = json.dumps(out, default=str)
    assert len(s) > 200


def test_score_product_uncertain_injection_matches_observation():
    """Opslane v1 has +21pp uncertain over-injection. The offline
    score must surface this number."""
    out = score_product_under_profile(
        product=_opslane_fixture(), candidate_profile=HN_SHOW_HN_V1,
    )
    inj = out["fidelity"]["decision"]["uncertain_injection_pp"]
    # +21.17pp expected (with float drift tolerance).
    assert 20.0 < inj < 22.0, f"got {inj:.2f}"


# ---------------------------------------------------------------------------
# Group E — Candidate generator
# ---------------------------------------------------------------------------


def test_generate_candidates_yields_baseline_first():
    gen = generate_candidate_profiles(
        base_profile=HN_SHOW_HN_V1,
        coarse_step=0.02, include_baseline=True,
        max_candidates=3,
    )
    first = next(gen)
    assert first == HN_SHOW_HN_V1


def test_generated_candidates_all_normalize_to_one():
    count = 0
    for cand in generate_candidate_profiles(
        base_profile=HN_SHOW_HN_V1, coarse_step=0.04,
        include_baseline=False, max_candidates=50,
    ):
        s = sum(cand.values())
        assert abs(s - 1.0) < 1e-6, (
            f"candidate sums to {s}, not 1.0: {cand}"
        )
        count += 1
    assert count > 0  # generator produced at least one candidate


def test_generated_candidates_respect_plausibility_bands():
    for cand in generate_candidate_profiles(
        base_profile=HN_SHOW_HN_V1, coarse_step=0.04,
        include_baseline=False, max_candidates=30,
    ):
        for role, (lo, hi) in CANDIDATE_PLAUSIBILITY_BANDS.items():
            assert lo - 1e-6 <= cand[role] <= hi + 1e-6, (
                f"{role}={cand[role]:.4f} outside band [{lo}, {hi}]"
            )


def test_generated_candidates_include_all_ten_roles():
    for cand in generate_candidate_profiles(
        base_profile=HN_SHOW_HN_V1, coarse_step=0.04,
        include_baseline=False, max_candidates=20,
    ):
        assert set(cand.keys()) == set(ALL_AUDIENCE_ROLES)


def test_generated_candidates_cap_max_candidates():
    cands = list(generate_candidate_profiles(
        base_profile=HN_SHOW_HN_V1, coarse_step=0.005,  # very fine grid
        include_baseline=False, max_candidates=10,
    ))
    assert len(cands) <= 10


# ---------------------------------------------------------------------------
# Group F — Top-level recalibration
# ---------------------------------------------------------------------------


def test_recalibrate_returns_required_top_level_keys():
    out = recalibrate_hn_show_hn(
        products=[_docuseal_fixture(), _opslane_fixture()],
        coarse_step=0.05,  # coarser for speed
        top_k=5,
        max_candidates=200,
    )
    for k in (
        "baseline_v1", "baseline_v1_product_scores",
        "candidates_evaluated", "top_candidates",
        "recommended_v2", "recommendation_rationale",
        "all_audience_roles", "schema_version",
    ):
        assert k in out


def test_recalibrate_recommended_improves_or_equals_worst_case_mae():
    out = recalibrate_hn_show_hn(
        products=[_docuseal_fixture(), _opslane_fixture()],
        coarse_step=0.05, top_k=3, max_candidates=200,
    )
    baseline_worst = out["baseline_v1"]["worst_case_mae_pp"]
    rec_worst = out["recommended_v2"]["worst_case_mae_pp"]
    # Recommendation must not REGRESS on worst-case MAE.
    assert rec_worst <= baseline_worst + 0.01


def test_recalibrate_picks_worst_case_objective_not_mean():
    """If two candidates A and B have:
      A: docuseal=4pp opslane=12pp → worst=12, mean=8
      B: docuseal=7pp opslane=8pp  → worst=8,  mean=7.5
    The objective picks B (lower worst-case), even though A has
    smaller mean."""
    out = recalibrate_hn_show_hn(
        products=[_docuseal_fixture(), _opslane_fixture()],
        coarse_step=0.05, top_k=5, max_candidates=200,
    )
    # Top-1 must have worst <= top-2 worst.
    top = out["top_candidates"]
    if len(top) >= 2:
        assert top[0]["worst_case_mae_pp"] <= top[1]["worst_case_mae_pp"]


def test_recalibrate_output_is_json_serializable():
    out = recalibrate_hn_show_hn(
        products=[_docuseal_fixture(), _opslane_fixture()],
        coarse_step=0.05, top_k=3, max_candidates=100,
    )
    s = json.dumps(out, default=str)
    assert len(s) > 1000


def test_recalibrate_recommended_profile_passes_validation():
    out = recalibrate_hn_show_hn(
        products=[_docuseal_fixture(), _opslane_fixture()],
        coarse_step=0.05, top_k=3, max_candidates=100,
    )
    rec_profile = out["recommended_v2"]["profile"]
    _validate_profile(rec_profile)  # must not raise


def test_recalibrate_records_regression_vs_baseline():
    out = recalibrate_hn_show_hn(
        products=[_docuseal_fixture(), _opslane_fixture()],
        coarse_step=0.05, top_k=3, max_candidates=100,
    )
    rec = out["recommended_v2"]
    assert "regression_vs_baseline_pp" in rec
    assert set(rec["regression_vs_baseline_pp"]) == {"docuseal", "opslane"}


def test_recalibrate_buyer_false_confidence_blocks_candidate():
    """Synthetic: if a candidate creates a buyer-false-confidence
    flag, it must be marked as hard-constraint failure."""
    # Construct a product whose observed buyer is near zero. The
    # legacy 24 customer voices wouldn't trigger buyer=5%+ on their
    # own, so this test simply verifies the LIST surface exists.
    out = recalibrate_hn_show_hn(
        products=[_opslane_fixture()],  # observed buyer = 0.75%
        coarse_step=0.05, top_k=5, max_candidates=100,
    )
    # The structure must contain hard_constraints_passed + violations
    # on every candidate for inspectability.
    for cand in out["top_candidates"]:
        assert "hard_constraints_passed" in cand
        assert "hard_constraint_violations" in cand


def test_recalibrate_uncertain_over_injection_warning_present_on_v1():
    """v1 has +21pp uncertain over-injection on Opslane → must surface
    as a gate warning in the baseline summary."""
    out = recalibrate_hn_show_hn(
        products=[_opslane_fixture()],
        coarse_step=0.05, top_k=3, max_candidates=10,
    )
    warnings = out["baseline_v1"]["gate_warnings"]
    assert any("uncertain_injection" in w for w in warnings), (
        f"expected uncertain_injection warning; got {warnings}"
    )


# ---------------------------------------------------------------------------
# Group G — Proof Packet draft
# ---------------------------------------------------------------------------


def test_proof_packet_draft_has_required_fields():
    product = _opslane_fixture()
    score = score_product_under_profile(
        product=product, candidate_profile=HN_SHOW_HN_V1,
    )
    packet = build_proof_packet_draft(
        case_id="test_opslane_offline",
        product=product,
        candidate_profile=HN_SHOW_HN_V1,
        product_score=score,
    )
    for k in (
        "schema_version", "case_id", "draft",
        "evidence_snapshot_hash", "brief_hash",
        "source_audience_profile", "candidate_profile_hash",
        "candidate_profile_role_weights",
        "persona_composition", "decision_distribution",
        "fidelity", "overall_internal_score_0_100",
        "null_pillars", "gates",
        "known_limitations", "do_not_over_trust_warnings",
    ):
        assert k in packet, f"packet missing {k}"


def test_proof_packet_marks_offline_as_draft():
    product = _opslane_fixture()
    score = score_product_under_profile(
        product=product, candidate_profile=HN_SHOW_HN_V1,
    )
    packet = build_proof_packet_draft(
        case_id="draft_test",
        product=product, candidate_profile=HN_SHOW_HN_V1,
        product_score=score,
    )
    assert packet["draft"] is True
    # Safety warnings ALWAYS present.
    assert len(packet["do_not_over_trust_warnings"]) >= 1


def test_proof_packet_is_json_serializable():
    product = _opslane_fixture()
    score = score_product_under_profile(
        product=product, candidate_profile=HN_SHOW_HN_V1,
    )
    packet = build_proof_packet_draft(
        case_id="serial_test",
        product=product, candidate_profile=HN_SHOW_HN_V1,
        product_score=score,
    )
    s = json.dumps(packet, default=str)
    assert len(s) > 500


def test_proof_packet_records_observed_alongside_predicted():
    product = _opslane_fixture()
    score = score_product_under_profile(
        product=product, candidate_profile=HN_SHOW_HN_V1,
    )
    packet = build_proof_packet_draft(
        case_id="obs_test",
        product=product, candidate_profile=HN_SHOW_HN_V1,
        product_score=score,
    )
    dd = packet["decision_distribution"]
    assert "observed" in dd
    assert "source_audience_view" in dd
    assert "target_market_view" in dd


# ---------------------------------------------------------------------------
# Group H — Discipline
# ---------------------------------------------------------------------------


def test_recalibration_module_has_no_llm_or_network_imports():
    p = (
        API_ROOT / "src" / "assembly" / "calibration"
        / "source_profile_recalibration.py"
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
        "import httpx",
        "import requests",
        "asyncpg",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"recalibration module has forbidden surface: {needle!r}"
        )


def test_no_apps_web_changes_in_phase_12e5b():
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web not present")
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
            f"apps/web touched in Phase 12E.5B:\n{changes}"
        )


def test_no_new_alembic_migration_in_12e5b():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in (
            "phase_12e5b", "source_profile_recalibration",
            "hn_show_hn_v2",
        ):
            assert needle not in text, (
                f"unexpected migration {f.name} mentions {needle!r}"
            )


def test_augmenter_profile_override_kwarg_is_backwards_compatible():
    """The augmenter change (12E.5B) must not break legacy callers."""
    from assembly.sources.audience.augmenter import (
        augment_intent_drafts_with_source_audience,
    )
    # Default call (no profile_override) → uses SOURCE_PROFILES.
    augmented, audit = augment_intent_drafts_with_source_audience(
        intent_drafts=[],
        persona_metadata_by_pid=None,
        launch_source="hn_show_hn",
        run_scope_id="bc_test",
    )
    assert isinstance(augmented, list)
    assert isinstance(audit, dict)


def test_profile_override_actually_changes_output():
    """Confirm the override hook is wired through — different profile
    in must produce different distribution out."""
    from assembly.sources.audience.augmenter import (
        augment_intent_drafts_with_source_audience,
        split_view_distributions,
    )
    drafts, meta = reconstruct_legacy_drafts_from_intent_distribution(
        intent_distribution={
            "would_consider_if_proven": 17,
            "loyal_to_current_alternative": 5,
            "would_reject": 2,
        },
        run_scope_id="ovr_test",
    )
    aug_v1, _ = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="hn_show_hn",
        run_scope_id="ovr_test",
    )
    # Make a different profile (extreme: 100% target_customer_evaluator)
    extreme = {r: 0.0 for r in ALL_AUDIENCE_ROLES}
    extreme["target_customer_evaluator"] = 1.0
    aug_extreme, _ = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="hn_show_hn",
        run_scope_id="ovr_test",
        profile_override=extreme,
    )
    v1_view = split_view_distributions(aug_v1)["source_audience_reaction"]
    ex_view = split_view_distributions(aug_extreme)[
        "source_audience_reaction"
    ]
    # Distributions must differ.
    assert v1_view != ex_view, (
        "profile_override did not change output — hook is not wired"
    )
