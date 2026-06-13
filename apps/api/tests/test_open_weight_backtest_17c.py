"""Phase 17C — open-weight blind backtest architecture tests.

Offline-mode enforcement, blindness-tier eligibility, retrieval leakage filtering,
knowledge-probe downgrades, raw-vs-Assembly pairing + lift, disabled local adapters
(no model deps / no calls), audit-record isolation, and runtime isolation. Pure /
deterministic; no model is loaded or called.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.benchmarks.market_fidelity import (
    BacktestAuditRecord,
    RetrospectiveCaseEligibilityInput,
    RunMetadata,
    assembly_lift,
    assess_probe,
    default_audits_dir,
    evaluate_eligibility,
    filter_pre_outcome_evidence,
    is_public_claim_grade,
    probe_blocks_public_claim,
    validate_offline_blind_run_config,
    verify_pairing,
)
from assembly.benchmarks.market_fidelity.adapters import (
    ADAPTERS,
    AdapterDisabledError,
    LocalModelAdapter,
    OllamaAdapter,
)
from assembly.benchmarks.market_fidelity.audit_records import write_audit_record

_API = Path(__file__).resolve().parents[1]
_PKG = _API / "src" / "assembly" / "benchmarks" / "market_fidelity"


def _offline_cfg(**over) -> dict:
    cfg = {
        "web_enabled": False, "live_retrieval": False, "tools_enabled": False, "rag_enabled": False,
        "prediction_timestamp": "2024-01-01", "outcome_date": "2024-03-01",
        "model_metadata": {"base_model_checkpoint": "qwen2.5-7b"},
        "input_bundle": {"sources": [{"id": "s1", "published_at": "2023-12-01"}]},
    }
    cfg.update(over)
    return cfg


# --------------------------------------------------------------------------
# Offline-mode enforcement
# --------------------------------------------------------------------------
def test_offline_config_ok():
    assert validate_offline_blind_run_config(_offline_cfg()) == []


def test_offline_rejects_web_tools_live_retrieval():
    assert validate_offline_blind_run_config(_offline_cfg(web_enabled=True))
    assert validate_offline_blind_run_config(_offline_cfg(tools_enabled=True))
    assert validate_offline_blind_run_config(_offline_cfg(live_retrieval=True))


def test_offline_rejects_rag_without_frozen_bundle():
    assert validate_offline_blind_run_config(_offline_cfg(rag_enabled=True))
    assert validate_offline_blind_run_config(_offline_cfg(rag_enabled=True, frozen_evidence_bundle_only=True)) == []


def test_offline_requires_timestamps_and_metadata():
    assert validate_offline_blind_run_config(_offline_cfg(prediction_timestamp=""))
    assert validate_offline_blind_run_config(_offline_cfg(outcome_date=""))
    assert validate_offline_blind_run_config(_offline_cfg(model_metadata={}))
    assert validate_offline_blind_run_config(_offline_cfg(input_bundle={"sources": [{"id": "s1"}]}))  # no ts


# --------------------------------------------------------------------------
# Blindness-tier eligibility
# --------------------------------------------------------------------------
def _elig(**over) -> RetrospectiveCaseEligibilityInput:
    base = dict(
        case_id="c", subject="Acme", prediction_timestamp="2024-01-01", outcome_timestamp="2024-03-01",
        model_checkpoint="qwen2.5-7b", offline=True,
    )
    base.update(over)
    return RetrospectiveCaseEligibilityInput(**base)


def test_tier0_prospective_is_claim_grade():
    r = evaluate_eligibility(_elig(is_prospective=True))
    assert r.blindness_tier == 0 and r.eligible_for_public_claim is True


def test_tier1_time_frozen_needs_justified_provenance():
    common = dict(model_release_date="2023-06-01", training_cutoff="2023-06-01",
                  has_pre_outcome_source_timestamps=True, has_temporal_proof=True)
    r = evaluate_eligibility(_elig(**common))
    assert r.blindness_tier == 1 and r.eligible_for_public_claim is False  # not justified yet
    r2 = evaluate_eligibility(_elig(**common, tier1_provenance_justified=True))
    assert r2.blindness_tier == 1 and r2.eligible_for_public_claim is True


def test_tier2_open_weight_uncertain_cutoff_internal_only():
    r = evaluate_eligibility(_elig(is_open_weight=True, has_pre_outcome_source_timestamps=True, has_temporal_proof=True))
    assert r.blindness_tier == 2 and r.eligible_for_public_claim is False


def test_tier3_model_after_outcome():
    r = evaluate_eligibility(_elig(model_release_date="2024-06-01", has_temporal_proof=True, has_pre_outcome_source_timestamps=True))
    assert r.blindness_tier == 3 and r.eligible_for_public_claim is False


def test_tier4_live_web_after_outcome():
    r = evaluate_eligibility(_elig(live_web_after_outcome=True))
    assert r.blindness_tier == 4 and r.eligible_for_public_claim is False


def test_probe_detection_downgrades_to_tier4():
    r = evaluate_eligibility(_elig(model_release_date="2023-06-01", training_cutoff="2023-06-01",
                                   has_pre_outcome_source_timestamps=True, has_temporal_proof=True,
                                   knowledge_probe_blocks_claim=True))
    assert r.blindness_tier == 4 and r.eligible_for_public_claim is False


def test_public_claim_tiers():
    assert is_public_claim_grade(0) is True
    assert is_public_claim_grade(1) is False
    assert is_public_claim_grade(1, tier1_provenance_justified=True) is True
    assert is_public_claim_grade(2) is False and is_public_claim_grade(4) is False


# --------------------------------------------------------------------------
# Retrieval leakage filter
# --------------------------------------------------------------------------
def test_retrieval_filter_excludes_post_outcome_and_postmortem():
    sources = [
        {"id": "pre", "published_at": "2023-12-01", "text": "a new campaign launches"},
        {"id": "after_pred", "published_at": "2024-02-01", "text": "still going"},
        {"id": "after_outcome", "published_at": "2024-04-01", "text": "wrap-up"},
        {"id": "postmortem", "published_at": "2023-12-15", "text": "the project raised $420,000 from 1,200 backers"},
        {"id": "no_ts", "text": "mystery source"},
    ]
    rep = filter_pre_outcome_evidence(
        case_id="c", prediction_timestamp="2024-01-15", outcome_date="2024-03-01", sources=sources,
    )
    assert rep["approved_source_ids"] == ["pre"]
    assert set(rep["excluded_source_ids"]) == {"after_pred", "after_outcome", "postmortem", "no_ts"}
    for sid in rep["excluded_source_ids"]:
        assert rep["retrieval_weight_overrides"][sid] == 0.0
    assert rep["retrieval_weight_overrides"]["pre"] == 1.0
    assert rep["evidence_bundle_hash"].startswith("sha256:")


def test_retrieval_filter_flagged_value():
    rep = filter_pre_outcome_evidence(
        case_id="c", prediction_timestamp="2024-01-15", outcome_date="2024-03-01",
        sources=[{"id": "x", "published_at": "2023-12-01", "text": "early coverage mentions ABC123"}],
        flagged_outcome_values=["ABC123"],
    )
    assert rep["excluded_source_ids"] == ["x"] and rep["retrieval_weight_overrides"]["x"] == 0.0


# --------------------------------------------------------------------------
# Knowledge probe
# --------------------------------------------------------------------------
def test_probe_detects_and_blocks():
    res = assess_probe(model_id="qwen", case_id="c", subject="Acme",
                       model_answers=["Acme raised $420,000 and was fully funded."],
                       outcome_markers=["$420,000"])
    assert res.outcome_knowledge_detected is True and res.memorization_risk == "high"
    assert probe_blocks_public_claim(res) is True
    assert res.probe_hash.startswith("sha256:")


def test_probe_disclaim_is_clean():
    res = assess_probe(model_id="qwen", case_id="c", subject="Acme",
                       model_answers=["I don't know.", "I have no information about that."],
                       outcome_markers=["$420,000"])
    assert res.outcome_knowledge_detected is False and res.memorization_risk == "low"
    assert probe_blocks_public_claim(res) is False


# --------------------------------------------------------------------------
# Raw-vs-Assembly pairing + lift
# --------------------------------------------------------------------------
def _run(mode, **over) -> RunMetadata:
    base = dict(mode=mode, base_model_family="qwen", base_model_checkpoint="qwen2.5-7b",
                model_provider="local", local_or_remote="local", input_bundle_hash="sha256:ib",
                assembly_protocol_enabled=(mode == "assembly_protocol"))
    base.update(over)
    return RunMetadata(**base)


def test_pairing_requires_same_bundle_and_model():
    raw, asm = _run("raw_baseline"), _run("assembly_protocol")
    assert verify_pairing(raw, asm) == []
    assert verify_pairing(raw, _run("assembly_protocol", input_bundle_hash="sha256:other"))
    assert verify_pairing(raw, _run("assembly_protocol", base_model_checkpoint="llama-3-8b"))


def test_assembly_lift_lower_is_better():
    # raw Brier 0.30, assembly 0.18 -> lift +0.12 (assembly improved)
    assert assembly_lift(0.30, 0.18) == pytest.approx(0.12)
    assert assembly_lift(0.30, 0.18, lower_is_better=False) == pytest.approx(-0.12)


# --------------------------------------------------------------------------
# Disabled adapters — no model deps / no calls
# --------------------------------------------------------------------------
def test_adapters_disabled_no_generation():
    for runner, cls in ADAPTERS.items():
        a = cls()
        assert a.runner == runner
        with pytest.raises(AdapterDisabledError):
            a.generate_prediction(prompt="x")
    # load_model_config records metadata WITHOUT loading weights
    meta = OllamaAdapter().load_model_config({"base_model_family": "qwen", "base_model_checkpoint": "q"})
    assert meta["loaded"] is False


def test_adapter_offline_validation_delegates():
    issues = LocalModelAdapter().validate_offline_mode(_offline_cfg(web_enabled=True))
    assert issues  # web on -> rejected


def test_no_heavy_model_imports_in_package():
    banned = ("import torch", "import vllm", "import ollama", "from llama_cpp", "import transformers",
              "import openai", "import anthropic", "google.generativeai")
    for py in _PKG.glob("*.py"):
        src = py.read_text()
        for b in banned:
            assert b not in src, f"{py.name} must not import heavy/model/provider deps ({b})"


# --------------------------------------------------------------------------
# Audit records — isolation + immutability + observed-free
# --------------------------------------------------------------------------
def _audit(**over) -> BacktestAuditRecord:
    base = dict(case_id="c", baseline_record_id="raw_qwen_1", model_metadata={"base_model_checkpoint": "q"},
                blindness_tier=1, contamination_checks={"web": False}, input_bundle_hash="sha256:ib",
                eligible_for_public_claim=False, reasons_if_not_eligible=["tier1 unjustified"])
    base.update(over)
    return BacktestAuditRecord(**base)


def test_audit_record_observed_free_and_purpose():
    rec = _audit()
    assert rec.observed is None
    assert rec.purpose == "benchmark_backtest_audit_not_validation_data"


def test_audit_write_dry_run_and_immutable(tmp_path):
    rec = _audit()
    assert write_audit_record(rec, allow_write=False, audits_dir=tmp_path) is None  # dry-run
    assert not list(tmp_path.glob("*.json"))
    out = write_audit_record(rec, allow_write=True, audits_dir=tmp_path)
    assert out is not None and out.exists()
    with pytest.raises(ValueError):
        write_audit_record(rec, allow_write=True, audits_dir=tmp_path)  # immutable


def test_audits_dir_not_under_validation_cases():
    d = str(default_audits_dir())
    assert "benchmarks/market_fidelity/backtest_audits" in d and "validation_cases" not in d


def test_audit_record_is_not_a_validation_case(tmp_path):
    # a backtest audit record must NOT parse as a ValidationCase
    from assembly.validation_ledger.schema import ValidationCase
    rec = _audit()
    with pytest.raises(ValidationError):
        ValidationCase.model_validate(json.loads(json.dumps(rec.model_dump(mode="json"))))


# --------------------------------------------------------------------------
# Runtime isolation
# --------------------------------------------------------------------------
def test_package_isolated_and_ledger_unchanged():
    forbidden = ("assembly.validation_ledger", "assembly.config", "assembly.orchestration",
                 "assembly.llm", "assembly.market_calibration", "assembly.pipeline")
    for py in _PKG.glob("*.py"):
        src = py.read_text()
        for f in forbidden:
            assert f not in src, f"{py.name} must not import {f}"
    from assembly.validation_factory.outcome_mapping_protocol import mapping_readiness
    from assembly.validation_ledger.loader import load_all_cases
    cases = load_all_cases()
    assert len(cases) == 8
    assert mapping_readiness(ledger_cases=cases)["phase_15e_blocked"] is True


# --------------------------------------------------------------------------
# Hardening regressions (from the Phase 17C adversarial review)
# --------------------------------------------------------------------------
def test_offline_frozen_flag_must_be_strict_true():
    # a truthy-but-negating string for the protective flag must NOT disable the gate
    assert validate_offline_blind_run_config(_offline_cfg(rag_enabled=True, frozen_evidence_bundle_only="false"))
    assert validate_offline_blind_run_config(_offline_cfg(rag_enabled=True, frozen_evidence_bundle_only="no"))
    assert validate_offline_blind_run_config(_offline_cfg(rag_enabled=True, frozen_evidence_bundle_only=True)) == []


def test_offline_non_mapping_source_does_not_crash():
    issues = validate_offline_blind_run_config(_offline_cfg(input_bundle={"sources": [None, "x", 123]}))
    assert isinstance(issues, list) and issues  # rejected cleanly, no exception


def test_retrieval_filter_rejects_coarse_tz_and_missing_anchor():
    # coarse year-only / whitespace-prefixed / negative-tz post-outcome must be EXCLUDED
    sources = [
        {"id": "year_only", "published_at": "2024", "text": "x"},
        {"id": "ws", "published_at": "  2024-12-31", "text": "x"},
        {"id": "tz", "published_at": "2024-03-01T01:00:00-12:00", "text": "x"},  # = 13:00Z, after outcome
    ]
    rep = filter_pre_outcome_evidence(case_id="c", prediction_timestamp="2024-01-15",
                                      outcome_date="2024-03-01T00:00:00+00:00", sources=sources)
    assert rep["approved_source_ids"] == []
    # missing/unparseable anchor -> fail closed (everything excluded)
    rep2 = filter_pre_outcome_evidence(case_id="c", prediction_timestamp="", outcome_date="2024-03-01",
                                       sources=[{"id": "a", "published_at": "2023-01-01"}])
    assert rep2["approved_source_ids"] == [] and rep2["excluded_source_ids"] == ["a"]


def test_retrieval_filter_flagged_value_spacing_normalized():
    rep = filter_pre_outcome_evidence(
        case_id="c", prediction_timestamp="2024-01-15", outcome_date="2024-03-01",
        sources=[{"id": "x", "published_at": "2023-12-01", "text": "raised   $420,000\n total"}],
        flagged_outcome_values=["$420,000 total"],
    )
    assert rep["excluded_source_ids"] == ["x"]


def test_prospective_cannot_bypass_probe_or_post_outcome_model():
    # a self-attested 'prospective' case with a probe hit must NOT get Tier 0
    r = evaluate_eligibility(_elig(is_prospective=True, knowledge_probe_blocks_claim=True))
    assert r.blindness_tier == 4 and r.eligible_for_public_claim is False
    # 'prospective' but model release post-dates the outcome -> downgraded
    r2 = evaluate_eligibility(_elig(is_prospective=True, model_release_date="2024-06-01"))
    assert r2.blindness_tier == 3 and r2.eligible_for_public_claim is False


def test_audit_record_is_frozen():
    rec = _audit()
    with pytest.raises(ValidationError):
        rec.eligible_for_public_claim = True  # frozen


def test_assembly_lift_rejects_non_finite():
    with pytest.raises(ValueError):
        assembly_lift(float("nan"), 0.1)
    with pytest.raises(ValueError):
        assembly_lift(0.3, float("inf"))


def test_paired_comparison_scored_requires_verified_and_consistent():
    from assembly.benchmarks.market_fidelity.lift import PairedComparison
    common = dict(case_id="c", base_model_family="qwen", base_model_checkpoint="q",
                  input_bundle_hash="sha256:ib", raw_baseline_id="r", assembly_run_id="a")
    # scored but unverified pairing -> rejected
    with pytest.raises(ValidationError):
        PairedComparison(**common, same_input_bundle_verified=False, same_base_model_verified=True,
                         raw_score=0.3, assembly_score=0.18, lift=0.12)
    # lift inconsistent with scores -> rejected
    with pytest.raises(ValidationError):
        PairedComparison(**common, same_input_bundle_verified=True, same_base_model_verified=True,
                         raw_score=0.3, assembly_score=0.18, lift=0.99)
    # consistent + verified -> ok
    ok = PairedComparison(**common, same_input_bundle_verified=True, same_base_model_verified=True,
                          raw_score=0.3, assembly_score=0.18, lift=0.12)
    assert ok.lift == pytest.approx(0.12)
