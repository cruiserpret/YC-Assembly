"""Phase 12E.5E — anti-overfit diagnostic tests.

Covers `apps/api/src/assembly/calibration/overfit_diagnostic.py`. No
DB, no LLM, no network. Tests run against in-memory artifacts to keep
isolation tight.

Groups:
  A. Loading saved run artifacts.
  B. Per-run diagnostic (receptive skew, skeptic underpred, buyer miss,
     uncertain overinjection).
  C. Cross-product comparison + anti-overfit threshold.
  D. Counterfactual projections (E, F, G).
  E. Root-cause classification.
  F. Discipline (no provider imports, no apps/web, no DB migration).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from assembly.calibration.overfit_diagnostic import (
    BUYER_MISS_WARN_PP,
    GLOBAL_FIX_THRESHOLD,
    PerRunDiagnostic,
    RECEPTIVE_SKEW_WARN_PP,
    RootCauseAssessment,
    RunArtifact,
    SKEPTIC_UNDERPRED_WARN_PP,
    classify_root_cause,
    compare_across_products,
    compute_per_run_diagnostic,
    counterfactual_route_wcip_to_loyal,
    counterfactual_route_wcip_to_uncertain,
    intent_distribution_for_target_mae,
    load_paid_run,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Fixtures (synthetic artifacts to avoid coupling to live runs)
# ---------------------------------------------------------------------------


def _write_synthetic_run(
    tmp_path: Path,
    *,
    run_id: str,
    launch_source: str,
    intent_distribution: dict[str, int],
    src_audience_counts: dict[str, int],
    target_market_counts: dict[str, int] | None = None,
    noise_meta_count: int = 5,
    diversity_health: dict[str, Any] | None = None,
) -> Path:
    rd = tmp_path / run_id
    rd.mkdir(parents=True, exist_ok=True)
    si = {
        "phase": "12e",
        "intent_distribution": intent_distribution,
        "phase_12e": {
            "audience_views": {
                "source_audience_reaction": src_audience_counts,
                "target_market_reaction": target_market_counts or {
                    "buyer": 0, "receptive": 0,
                    "uncertain": 0, "skeptical": 0,
                },
            },
        },
    }
    fr = {
        "audience_breakdown": {
            "launch_source_used": launch_source,
            "source_audience_reaction": src_audience_counts,
            "target_market_reaction": target_market_counts or {
                "buyer": 0, "receptive": 0,
                "uncertain": 0, "skeptical": 0,
            },
            "noise_meta_estimate": {"count": noise_meta_count},
            "augmentation_audit": {
                "n_legacy_drafts": sum(intent_distribution.values()),
                "n_legacy_customer_voices": sum(intent_distribution.values()),
                "total_after_augmentation": (
                    sum(src_audience_counts.values()) + noise_meta_count
                ),
            },
        },
    }
    (rd / "simulated_intent.json").write_text(json.dumps(si))
    (rd / "founder_report.json").write_text(json.dumps(fr))
    if diversity_health:
        (rd / "diversity_health.json").write_text(
            json.dumps(diversity_health),
        )
    return rd.parent  # audit_root


def _opslane_v2_like(observed_pct: dict[str, float] | None = None) -> RunArtifact:
    """Stand-in for the Opslane v2 paid result (n_scorable=45).
    Predicted source-audience pct: 0/40/27/33."""
    return RunArtifact(
        product="opslane",
        run_id="opslane_v2_test",
        launch_source="hn_show_hn_v2",
        intent_distribution={
            "would_consider_if_proven": 18, "would_reject": 3,
            "loyal_to_current_alternative": 3,
        },
        target_market_view_pct={"buyer": 0.0, "receptive": 75.0,
                                "uncertain": 0.0, "skeptical": 25.0},
        source_audience_view_pct={"buyer": 0.0, "receptive": 40.0,
                                   "uncertain": 26.67, "skeptical": 33.33},
        noise_meta_count=17,
        augmentation_audit={"n_legacy_drafts": 24, "n_legacy_customer_voices": 24},
        observed_pct=observed_pct or {"buyer": 0.75, "receptive": 44.36,
                                       "uncertain": 18.05, "skeptical": 36.84},
        diversity_health={"skeptic_retention_rate": 1.0, "hard_resistant_count": 29},
    )


def _docuseal_v2_like(observed_pct: dict[str, float] | None = None) -> RunArtifact:
    """Stand-in for the DocuSeal v2 paid result. Predicted source-
    audience pct: 0/44.44/26.67/28.89; observed (corrected) 8/19/25/47."""
    return RunArtifact(
        product="docuseal",
        run_id="docuseal_v2_test",
        launch_source="hn_show_hn_v2",
        intent_distribution={
            "would_consider_if_proven": 20,
            "loyal_to_current_alternative": 4,
        },
        target_market_view_pct={"buyer": 0.0, "receptive": 83.33,
                                "uncertain": 0.0, "skeptical": 16.67},
        source_audience_view_pct={"buyer": 0.0, "receptive": 44.44,
                                   "uncertain": 26.67, "skeptical": 28.89},
        noise_meta_count=17,
        augmentation_audit={"n_legacy_drafts": 24, "n_legacy_customer_voices": 24},
        observed_pct=observed_pct or {"buyer": 8.43, "receptive": 19.28,
                                       "uncertain": 25.30, "skeptical": 46.99},
        diversity_health={"skeptic_retention_rate": 1.0, "hard_resistant_count": 21},
    )


# ---------------------------------------------------------------------------
# A. Loading saved run artifacts
# ---------------------------------------------------------------------------


def test_load_paid_run_reads_intent_distribution_and_views(tmp_path):
    audit = _write_synthetic_run(
        tmp_path, run_id="run_a", launch_source="hn_show_hn_v2",
        intent_distribution={
            "would_consider_if_proven": 18,
            "loyal_to_current_alternative": 6,
        },
        src_audience_counts={
            "buyer": 0, "receptive": 18,
            "uncertain": 12, "skeptical": 15,
        },
    )
    art = load_paid_run(
        product="testprod", run_id="run_a",
        observed_pct={"buyer": 1, "receptive": 45,
                      "uncertain": 18, "skeptical": 36},
        audit_root=audit,
    )
    assert art.product == "testprod"
    assert art.launch_source == "hn_show_hn_v2"
    assert art.intent_distribution["would_consider_if_proven"] == 18
    # source-audience normalized to percent
    s = sum(art.source_audience_view_pct.values())
    assert abs(s - 100.0) < 1e-6


def test_load_paid_run_raises_on_missing_run_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_paid_run(
            product="x", run_id="ghost",
            observed_pct={"buyer": 0, "receptive": 0,
                          "uncertain": 0, "skeptical": 0},
            audit_root=tmp_path,
        )


def test_load_paid_run_handles_missing_diversity_health(tmp_path):
    audit = _write_synthetic_run(
        tmp_path, run_id="no_dh", launch_source="default",
        intent_distribution={"would_consider_if_proven": 10},
        src_audience_counts={"buyer": 0, "receptive": 10,
                             "uncertain": 0, "skeptical": 0},
    )
    art = load_paid_run(
        product="x", run_id="no_dh",
        observed_pct=None, audit_root=audit,
    )
    assert art.diversity_health == {}


# ---------------------------------------------------------------------------
# B. Per-run diagnostic
# ---------------------------------------------------------------------------


def test_per_run_diagnostic_detects_receptive_overprediction():
    art = _docuseal_v2_like()
    d = compute_per_run_diagnostic(art)
    assert d.receptive_overpredict is True
    # The actual signed error is ~+25pp (predicted 44 vs observed 19)
    assert d.receptive_signed_pp > RECEPTIVE_SKEW_WARN_PP
    # Skeptic under-prediction also fires on DocuSeal v2-like
    assert d.skeptic_underpredict is True


def test_per_run_diagnostic_opslane_v2_does_not_trigger_receptive_overpredict():
    art = _opslane_v2_like()
    d = compute_per_run_diagnostic(art)
    assert d.receptive_overpredict is False
    # Opslane: predicted 40 vs observed 44 → signed ≈ -4
    assert d.receptive_signed_pp < 0


def test_per_run_diagnostic_detects_buyer_miss():
    """DocuSeal observed buyer ≥5%; predicted ~0% → buyer_miss=True."""
    art = _docuseal_v2_like()
    d = compute_per_run_diagnostic(art)
    assert d.buyer_miss is True


def test_per_run_diagnostic_does_not_fire_buyer_miss_when_observed_near_zero():
    art = _opslane_v2_like()
    d = compute_per_run_diagnostic(art)
    assert d.buyer_miss is False


def test_per_run_diagnostic_computes_pct_wcip():
    art = _docuseal_v2_like()
    d = compute_per_run_diagnostic(art)
    # 20 / 24 = 83.33%
    assert d.pct_legacy_would_consider_if_proven == pytest.approx(83.33, abs=0.05)


def test_per_run_diagnostic_raises_when_no_observed_pct():
    art = _docuseal_v2_like()
    art.observed_pct = None
    with pytest.raises(ValueError):
        compute_per_run_diagnostic(art)


# ---------------------------------------------------------------------------
# C. Cross-product comparison + anti-overfit threshold
# ---------------------------------------------------------------------------


def test_global_fix_threshold_is_at_least_two():
    """The anti-overfit invariant: never label something systemic
    from a single product."""
    assert GLOBAL_FIX_THRESHOLD >= 2


def test_compare_marks_pattern_systemic_only_when_n_geq_threshold():
    docuseal = compute_per_run_diagnostic(_docuseal_v2_like())
    opslane = compute_per_run_diagnostic(_opslane_v2_like())
    comp = compare_across_products(
        pattern_name="receptive_overpredict_v2",
        per_run=[docuseal, opslane],
        pattern_attr="receptive_overpredict",
    )
    # Only DocuSeal exhibits → 1 product → NOT systemic.
    assert comp.n_products_with_pattern == 1
    assert comp.crosses_threshold is False
    assert comp.per_product_signal == {"docuseal": True, "opslane": False}


def test_compare_marks_systemic_when_both_products_exhibit():
    a = PerRunDiagnostic(
        product="a", run_id="r1", launch_source="x",
        source_audience_mae_pp=15, receptive_signed_pp=20,
        skeptic_signed_pp=-18, uncertain_signed_pp=2, buyer_signed_pp=-5,
        receptive_overpredict=True, skeptic_underpredict=True,
        uncertain_overinject=False, buyer_miss=False,
        pct_legacy_would_consider_if_proven=80,
        pct_legacy_competitor_loyal_or_reject=10,
    )
    b = PerRunDiagnostic(
        product="b", run_id="r2", launch_source="x",
        source_audience_mae_pp=14, receptive_signed_pp=22,
        skeptic_signed_pp=-17, uncertain_signed_pp=0, buyer_signed_pp=-4,
        receptive_overpredict=True, skeptic_underpredict=True,
        uncertain_overinject=False, buyer_miss=False,
        pct_legacy_would_consider_if_proven=78,
        pct_legacy_competitor_loyal_or_reject=12,
    )
    comp = compare_across_products(
        pattern_name="receptive_overpredict_test",
        per_run=[a, b], pattern_attr="receptive_overpredict",
    )
    assert comp.n_products_with_pattern == 2
    assert comp.crosses_threshold is True


def test_compare_aggregates_signals_within_same_product():
    """If product 'x' has two runs and only ONE shows the pattern,
    the product is counted as exhibiting (any-run signal)."""
    a = PerRunDiagnostic(
        product="docuseal", run_id="run_a", launch_source="v1",
        source_audience_mae_pp=14, receptive_signed_pp=14,
        skeptic_signed_pp=-19, uncertain_signed_pp=14, buyer_signed_pp=-8,
        receptive_overpredict=True, skeptic_underpredict=True,
        uncertain_overinject=True, buyer_miss=True,
        pct_legacy_would_consider_if_proven=71,
        pct_legacy_competitor_loyal_or_reject=29,
    )
    b = PerRunDiagnostic(
        product="docuseal", run_id="run_b", launch_source="v2",
        source_audience_mae_pp=13, receptive_signed_pp=25,
        skeptic_signed_pp=-18, uncertain_signed_pp=1, buyer_signed_pp=-8,
        receptive_overpredict=True, skeptic_underpredict=True,
        uncertain_overinject=False, buyer_miss=True,
        pct_legacy_would_consider_if_proven=83,
        pct_legacy_competitor_loyal_or_reject=17,
    )
    comp = compare_across_products(
        pattern_name="uncertain_overinject",
        per_run=[a, b], pattern_attr="uncertain_overinject",
    )
    # Only docuseal product overall; one of two runs exhibits → still "1 product"
    assert comp.n_products_with_pattern == 1
    assert comp.crosses_threshold is False


# ---------------------------------------------------------------------------
# D. Counterfactual projections
# ---------------------------------------------------------------------------


def test_counterfactual_route_wcip_to_uncertain_runs():
    art = _docuseal_v2_like()
    out = counterfactual_route_wcip_to_uncertain(
        art=art, fraction=0.5,
    )
    # Half of 20 = 10 voices moved to wait_and_see (uncertain bucket).
    assert out["moved_n"] == 10
    assert "mae_pp" in out
    # MAE should differ from the baseline (which was 13.27pp).
    assert out["mae_pp"] != 13.27


def test_counterfactual_route_wcip_to_uncertain_zero_fraction_is_identity():
    art = _docuseal_v2_like()
    out = counterfactual_route_wcip_to_uncertain(art=art, fraction=0.0)
    assert out["moved_n"] == 0
    assert out["modified_intent_distribution"][
        "would_consider_if_proven"
    ] == 20


def test_counterfactual_route_wcip_to_loyal_increases_skeptical():
    art = _docuseal_v2_like()
    out = counterfactual_route_wcip_to_loyal(
        art=art, fraction=0.5,
    )
    # Half of 20 → 10 moved to skeptical bucket via loyal_to_current_alternative.
    assert out["predicted_pct_source_audience"]["skeptical"] > 28.89


def test_counterfactual_fraction_validation():
    art = _docuseal_v2_like()
    with pytest.raises(ValueError):
        counterfactual_route_wcip_to_uncertain(art=art, fraction=1.5)
    with pytest.raises(ValueError):
        counterfactual_route_wcip_to_loyal(art=art, fraction=-0.1)


def test_grid_search_finds_target_mae():
    """Counterfactual G: there must exist SOME re-routing of DocuSeal's
    20 would_consider_if_proven voices that brings MAE ≤ 8pp."""
    art = _docuseal_v2_like()
    out = intent_distribution_for_target_mae(
        art=art, target_mae_pp=8.0, grid_step=0.1,
    )
    assert "best_overall" in out
    # The best in the grid must be lower than the un-rerouted MAE.
    assert out["best_overall"]["mae_pp"] < 13.27


def test_grid_search_unreachable_target_returns_flag():
    """Targeting MAE < 0 is impossible → reachable_within_grid=False."""
    art = _docuseal_v2_like()
    out = intent_distribution_for_target_mae(
        art=art, target_mae_pp=-1.0, grid_step=0.2,
    )
    assert out["reachable_within_grid"] is False
    assert out["best_overall"] is not None  # still emits best-overall


# ---------------------------------------------------------------------------
# E. Root-cause classification
# ---------------------------------------------------------------------------


def test_classify_root_cause_marks_product_specific_with_one_product():
    docuseal = compute_per_run_diagnostic(_docuseal_v2_like())
    opslane = compute_per_run_diagnostic(_opslane_v2_like())
    a = classify_root_cause(
        cause_id="receptive_skew",
        per_run=[docuseal, opslane],
        pattern_attr="receptive_overpredict",
        recommended_action="diagnose intent-cascade routing",
    )
    assert a.classification == "product_specific"
    assert "docuseal" in a.products_exhibiting
    assert "opslane" not in a.products_exhibiting


def test_classify_root_cause_marks_systemic_when_threshold_crossed():
    a = PerRunDiagnostic(
        product="a", run_id="r1", launch_source="x",
        source_audience_mae_pp=15, receptive_signed_pp=20,
        skeptic_signed_pp=-18, uncertain_signed_pp=2, buyer_signed_pp=-5,
        receptive_overpredict=True, skeptic_underpredict=True,
        uncertain_overinject=False, buyer_miss=False,
        pct_legacy_would_consider_if_proven=80,
        pct_legacy_competitor_loyal_or_reject=10,
    )
    b = PerRunDiagnostic(
        product="b", run_id="r2", launch_source="x",
        source_audience_mae_pp=14, receptive_signed_pp=22,
        skeptic_signed_pp=-17, uncertain_signed_pp=0, buyer_signed_pp=-4,
        receptive_overpredict=True, skeptic_underpredict=True,
        uncertain_overinject=False, buyer_miss=False,
        pct_legacy_would_consider_if_proven=78,
        pct_legacy_competitor_loyal_or_reject=12,
    )
    out = classify_root_cause(
        cause_id="x", per_run=[a, b],
        pattern_attr="receptive_overpredict",
    )
    assert out.classification == "likely_systemic"


def test_classify_root_cause_marks_inconclusive_when_no_product_signal():
    docuseal = compute_per_run_diagnostic(_docuseal_v2_like())
    opslane = compute_per_run_diagnostic(_opslane_v2_like())
    out = classify_root_cause(
        cause_id="never_fires",
        per_run=[docuseal, opslane],
        pattern_attr="uncertain_overinject",
    )
    # On v2 paid runs, uncertain_overinject is FALSE for both (v2 fixed it).
    assert out.classification == "inconclusive"
    assert out.certainty == "low"


# ---------------------------------------------------------------------------
# F. Discipline
# ---------------------------------------------------------------------------


def test_overfit_diagnostic_module_has_no_llm_or_network_imports():
    p = (
        API_ROOT / "src" / "assembly" / "calibration"
        / "overfit_diagnostic.py"
    )
    text = p.read_text(encoding="utf-8")
    for needle in (
        "provider.chat(", "provider.structured_output(",
        ".messages.create(", "with_cost_guard(",
        "import anthropic", "from anthropic",
        "import openai", "from openai",
        "import httpx", "import requests",
    ):
        assert needle not in text, (
            f"overfit_diagnostic uses forbidden surface: {needle!r}"
        )


def test_no_new_alembic_migration_in_12e5e():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in (
            "phase_12e5e", "overfit_diagnostic",
        ):
            assert needle not in text


def test_no_apps_web_changes_in_12e5e():
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web not present")
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "apps/web"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available")
    if (r.stdout or "").strip():
        raise AssertionError(
            f"apps/web touched in 12E.5E:\n{r.stdout}"
        )
