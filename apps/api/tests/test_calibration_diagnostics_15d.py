"""Phase 15D0 — source-bias / category-prior diagnostics tests.

Pure, deterministic: no LLM, no network, no DB. Verifies the diagnostics
measure error correctly, fit on training only (never holdout), surface
small-N / not-validated honestly, and emit NO forecast.
"""
from __future__ import annotations

import ast
from pathlib import Path

from assembly.market_calibration.calibration_diagnostics import (
    build_calibration_diagnostics_report,
)
from assembly.market_calibration.category_priors import estimate_category_profiles
from assembly.market_calibration.source_profiles import (
    estimate_source_profiles,
    minimum_case_warning,
    summarize_source_bias,
)
from assembly.validation_ledger import load_cases
from assembly.validation_ledger.schema import (
    AntiOverfit,
    CaseMetadata,
    MarketDistribution,
    ObservedProportions,
    ValidationCase,
)

_DIAG_FILES = ["source_profiles.py", "category_priors.py", "calibration_diagnostics.py"]
_PKG_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "market_calibration"
)
_BUCKETS = ("buyer_action_positive", "receptive", "uncertain_proof_needed",
            "skeptical_resistant")


def _case(source, category, pred, obs, *, training=True, holdout=False,
          status="scored", denom="independent_voices", cid=None) -> ValidationCase:
    def _d(t):
        return dict(zip(_BUCKETS, t, strict=True))
    return ValidationCase(
        case_id=cid or f"{source}_{category}_{pred}",
        metadata=CaseMetadata(
            product_name="P", source_type=source, product_category=category,
            launch_stage="launched", date_run="2026-05-30", validation_status=status,
        ),
        predicted=MarketDistribution(**_d(pred)) if pred else None,
        observed=ObservedProportions(**_d(obs), denominator_type=denom) if obs else None,
        anti_overfit=AntiOverfit(used_for_training=training, used_for_holdout=holdout),
    )


# --------------------------------------------------------------------------
# Source profiles
# --------------------------------------------------------------------------


def test_source_signed_bucket_errors_correct():
    # predicted (0,80,0,20) vs observed (0,30,0,70) -> signed = (0,+50,0,-50)
    c = _case("hacker_news", "open_source_software", (0, 80, 0, 20), (0, 30, 0, 70))
    p = estimate_source_profiles([c])["hacker_news"]
    sb = p.avg_signed_bucket_error
    assert sb.receptive == 50.0
    assert sb.skeptical_resistant == -50.0
    assert p.overpredicted_buckets == ["receptive"]
    assert p.underpredicted_buckets == ["skeptical_resistant"]


def test_source_groups_by_source_type():
    cases = [
        _case("hacker_news", "open_source_software", (0, 60, 20, 20), (0, 40, 30, 30)),
        _case("product_hunt", "consumer_apps", (0, 40, 20, 40), (10, 40, 30, 20)),
    ]
    profiles = estimate_source_profiles(cases)
    assert set(profiles) == {"hacker_news", "product_hunt"}
    assert profiles["hacker_news"].case_count == 1


def test_source_fitting_excludes_holdout():
    train = _case("reddit", "consumer_apps", (10, 40, 25, 25), (10, 40, 25, 25),
                  training=True, holdout=False, cid="r_train")
    hold = _case("reddit", "consumer_apps", (0, 0, 0, 100), (50, 50, 0, 0),
                 training=False, holdout=True, cid="r_hold")
    profiles = estimate_source_profiles([train, hold])
    # Only the training case counts; the holdout case must not leak in.
    assert profiles["reddit"].case_count == 1
    assert profiles["reddit"].avg_mae_pp == 0.0  # the perfect training case


def test_source_warns_on_small_n_and_not_validated():
    c = _case("kickstarter", "crowdfunding_hardware", (0, 50, 0, 50), (30, 20, 25, 25))
    p = estimate_source_profiles([c])["kickstarter"]
    assert p.confidence_level == "insufficient"  # n=1
    assert p.validated is False
    w = minimum_case_warning(p)
    assert w is not None and "not validated" in w


def test_source_confidence_capped_by_comment_derived_evidence():
    # 4 HN cases (count would be 'moderate') but all comment-derived -> capped 'weak'
    cases = [
        _case("hacker_news", "developer_tools", (0, 60, 20, 20), (0, 40, 30, 30),
              denom="independent_voices", cid=f"hn{i}")
        for i in range(4)
    ]
    p = estimate_source_profiles(cases)["hacker_news"]
    assert p.confidence_level == "weak"


def test_summarize_source_bias_returns_dicts():
    cases = load_cases()
    s = summarize_source_bias(cases)
    assert "hacker_news" in s
    assert isinstance(s["hacker_news"], dict)
    assert s["hacker_news"]["validated"] is False


# --------------------------------------------------------------------------
# Category profiles
# --------------------------------------------------------------------------


def test_category_groups_and_averages():
    cases = [
        _case("hacker_news", "open_source_software", (0, 80, 0, 20), (0, 20, 30, 50), cid="a"),
        _case("hacker_news", "open_source_software", (0, 60, 0, 40), (0, 20, 20, 60), cid="b"),
    ]
    p = estimate_category_profiles(cases)["open_source_software"]
    assert p.case_count == 2
    assert p.predicted_avg["receptive"] == 70.0  # (80+60)/2
    assert p.observed_avg["receptive"] == 20.0
    assert p.avg_signed_bucket_error["receptive"] == 50.0  # 70 - 20


def test_category_warns_on_small_n():
    c = _case("product_hunt", "consumer_apps", (0, 50, 25, 25), (10, 40, 30, 20))
    p = estimate_category_profiles([c])["consumer_apps"]
    assert p.case_count == 1
    assert "diagnostic only" in (p.warning or "")


# --------------------------------------------------------------------------
# Diagnostics report (on the real 6-case seed ledger)
# --------------------------------------------------------------------------


def test_report_counts_and_validation_state():
    r = build_calibration_diagnostics_report()
    assert r["dataset_summary"]["n_cases"] == 6
    assert r["holdout_case_count"] == 0
    assert r["training_case_count"] == 6
    assert r["validated"] is False
    assert r["is_diagnostic_only"] is True
    assert r["applies_calibration"] is False
    assert r["changes_live_forecast"] is False


def test_report_required_warnings_present():
    warnings = build_calibration_diagnostics_report()["warnings"]
    joined = " | ".join(warnings)
    assert "0 holdout cases" in joined
    assert "diagnostic only" in joined
    assert "Tier-3" in joined or "comment-derived" in joined
    assert "Do not apply these profiles to live forecasts yet" in joined


def test_report_emits_no_market_forecast():
    """The diagnostics report must NOT contain a final market-proportion
    forecast applied to anything live."""
    r = build_calibration_diagnostics_report()
    assert r["changes_live_forecast"] is False
    # No top-level buyer/receptive/uncertain/skeptical forecast keys.
    assert set(_BUCKETS).isdisjoint(set(r.keys()))


def test_report_tier_coverage_zero_for_seed():
    r = build_calibration_diagnostics_report()
    # Seed cases carry no action_signals yet -> honest 0 action-tier coverage.
    assert r["tier1_case_count"] == 0
    assert r["tier2_case_count"] == 0
    assert r["tier3_case_count"] == 0


# --------------------------------------------------------------------------
# Safety — diagnostic modules are pure (no LLM/net/DB/Phase13/token/orchestration)
# --------------------------------------------------------------------------


def _diag_sources() -> list[Path]:
    return [_PKG_DIR / f for f in _DIAG_FILES]


def test_diagnostics_no_forbidden_imports():
    forbidden = {
        "anthropic", "openai", "httpx", "requests", "aiohttp", "sqlalchemy",
        "redis",
    }
    for p in _diag_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.add(node.names[0].name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not (mods & forbidden), f"{p.name} imports forbidden: {mods & forbidden}"


def test_diagnostics_no_phase13_behavioral_token_or_orchestration():
    for p in _diag_sources():
        src = p.read_text(encoding="utf-8").lower()
        for tok in (
            "behavioral_mind_layer", "assembly_behavioral", "phase_13",
            "token_", "credit_", "orchestration", "live_founder_brief",
            "artifact_paths",
        ):
            assert tok not in src, f"{p.name} must not reference {tok}"


def test_diagnostics_import_only_stdlib_pydantic_and_assembly():
    allowed = {"__future__", "typing", "collections", "pydantic", "assembly"}
    for p in _diag_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            top = None
            if isinstance(node, ast.Import):
                top = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
            if top is not None:
                assert top in allowed, f"{p.name} imports unexpected: {top}"
