"""Phase 17B — benchmark harness tests.

Schema + bucket-sum + schema_failure, canonicalization determinism, hash
reproducibility/sensitivity, naive baselines, the manual-lock CLI (dry-run vs
write), metrics on known values, provider stubs disabled (no paid calls), and
runtime ISOLATION (the package never touches the validation ledger / forecast
runtime / config; load_all_cases stays 8). Pure/deterministic; no network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.benchmarks.market_fidelity import (
    NAIVE_BASELINE_IDS,
    BenchmarkPrediction,
    canonical_bytes,
    compute_prediction_hash,
    input_bundle_hash,
    metrics,
    naive_baseline,
    validate_prediction,
)
from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    default_records_dir,
    load_records,
    write_record,
)
from assembly.benchmarks.market_fidelity.providers import (
    LIVE_PROVIDER_CALLS_ENABLED,
    OpenAIBaselineStub,
    ProviderCallDisabledError,
    assert_live_calls_disabled,
)
from assembly.benchmarks.market_fidelity.validators import assert_mode_is_offline

_API = Path(__file__).resolve().parents[1]
_PKG = _API / "src" / "assembly" / "benchmarks" / "market_fidelity"

# --------------------------------------------------------------------------
# Fixtures (synthetic)
# --------------------------------------------------------------------------
_VALID = {
    "buyer_action_positive": 10.0, "receptive": 50.0,
    "uncertain_proof_needed": 30.0, "skeptical_resistant": 10.0, "confidence": 0.6,
}
_BAD_SUM = {**_VALID, "buyer_action_positive": 40.0}  # sums to 130
_SCHEMA_FAIL = {"confidence": 0.0, "schema_failure": True,
                "schema_failure_reason": "tool outputs only a qualitative report"}
_BUNDLE = {"case_id": "demo", "evidence": ["a", "b"], "sources": [{"url": "x", "retrieved_at": "2026-06-01"}]}


# --------------------------------------------------------------------------
# Schema + bucket sum + schema_failure
# --------------------------------------------------------------------------
def test_valid_prediction_parses():
    p = validate_prediction(_VALID)
    assert p.buckets()["receptive"] == 50.0
    assert abs(sum(p.buckets_as_fractions().values()) - 1.0) < 1e-9


def test_bucket_sum_enforced():
    with pytest.raises(ValidationError):
        validate_prediction(_BAD_SUM)


def test_missing_bucket_rejected_when_not_schema_failure():
    with pytest.raises(ValidationError):
        validate_prediction({"receptive": 50.0, "confidence": 0.5})


def test_schema_failure_bypasses_buckets_but_requires_reason():
    p = validate_prediction(_SCHEMA_FAIL)
    assert p.schema_failure is True
    with pytest.raises(ValidationError):
        validate_prediction({"confidence": 0.0, "schema_failure": True})  # no reason


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        validate_prediction({**_VALID, "sneaky": 1})


def test_confidence_required_and_ranged():
    with pytest.raises(ValidationError):
        validate_prediction({k: v for k, v in _VALID.items() if k != "confidence"})
    with pytest.raises(ValidationError):
        validate_prediction({**_VALID, "confidence": 1.5})


# --------------------------------------------------------------------------
# Canonicalization determinism + hash reproducibility/sensitivity
# --------------------------------------------------------------------------
def test_canonicalization_is_key_order_independent():
    a = {"b": 1, "a": {"y": 2, "x": 1.0000001}}
    b = {"a": {"x": 1.0000001, "y": 2}, "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b)


def test_hash_reproducible_with_fixed_locked_at():
    kw = dict(method_id="m", method_version="v1", input_bundle_hash="sha256:ib",
              prediction_payload=_VALID, locked_at="2026-06-10T00:00:00+00:00")
    assert compute_prediction_hash(**kw) == compute_prediction_hash(**kw)


def test_hash_changes_when_prediction_changes():
    base = dict(method_id="m", method_version="v1", input_bundle_hash="sha256:ib",
                locked_at="2026-06-10T00:00:00+00:00")
    h1 = compute_prediction_hash(prediction_payload=_VALID, **base)
    h2 = compute_prediction_hash(prediction_payload={**_VALID, "receptive": 49.0, "uncertain_proof_needed": 31.0}, **base)
    assert h1 != h2


def test_hash_changes_when_inputs_change():
    base = dict(method_version="v1", input_bundle_hash="sha256:ib",
                prediction_payload=_VALID, locked_at="2026-06-10T00:00:00+00:00")
    assert compute_prediction_hash(method_id="a", **base) != compute_prediction_hash(method_id="b", **base)


def test_input_bundle_hash_stable():
    assert input_bundle_hash(_BUNDLE) == input_bundle_hash(dict(reversed(list(_BUNDLE.items()))))


# --------------------------------------------------------------------------
# Naive baselines
# --------------------------------------------------------------------------
def test_all_naive_baselines_valid_and_lockable():
    for name in NAIVE_BASELINE_IDS:
        p = naive_baseline(name, _BUNDLE)
        assert isinstance(p, BenchmarkPrediction)
        # category/crowdfunding placeholders schema_failure when their inputs are absent
        if not p.schema_failure:
            assert abs(sum(p.buckets().values()) - 100.0) < 1.5
        h = compute_prediction_hash(method_id=name, method_version="v1",
                                    input_bundle_hash="sha256:ib", prediction_payload=p.to_payload(),
                                    locked_at="2026-06-10T00:00:00+00:00")
        assert h.startswith("sha256:")


def test_always_zero_buyer_is_zero_buyer():
    assert naive_baseline("always_zero_buyer").buckets()["buyer_action_positive"] == 0.0


def test_category_prior_placeholder_schema_failure_without_prior():
    assert naive_baseline("category_prior_placeholder", {}).schema_failure is True
    p = naive_baseline("category_prior_placeholder", {"category_prior": _VALID})
    assert p.schema_failure is False


def test_crowdfunding_placeholder_uses_only_pre_lock_progress():
    assert naive_baseline("crowdfunding_goal_progress_placeholder", {}).schema_failure is True
    p = naive_baseline("crowdfunding_goal_progress_placeholder",
                       {"crowdfunding_progress": {"pct_of_goal_at_lock": 80.0, "frac_time_elapsed": 0.5}})
    assert p.schema_failure is False
    assert abs(sum(p.buckets().values()) - 100.0) < 1.5


def test_unknown_naive_raises():
    with pytest.raises(KeyError):
        naive_baseline("does_not_exist")


# --------------------------------------------------------------------------
# Metrics on known values
# --------------------------------------------------------------------------
def test_metrics_known_values():
    pred = {"buyer_action_positive": 0.0, "receptive": 50.0, "uncertain_proof_needed": 40.0, "skeptical_resistant": 10.0}
    obs = {"buyer_action_positive": 20.0, "receptive": 50.0, "uncertain_proof_needed": 20.0, "skeptical_resistant": 10.0}
    assert metrics.bucket_mae(pred, obs) == pytest.approx((20 + 0 + 20 + 0) / 4)
    assert metrics.tvd(pred, obs) == pytest.approx(0.5 * (0.2 + 0 + 0.2 + 0))
    assert metrics.brier_binary(0.0, True) == 1.0
    assert metrics.brier_binary(1.0, True) == 0.0
    assert metrics.directional_hit(0.0, material_action=True) == "miss"
    assert metrics.directional_hit(0.0, material_action=False) == "hit"
    acc = metrics.schema_failure_accounting([True, False, False, False])
    assert acc["schema_failure_rate"] == pytest.approx(0.25)


# --------------------------------------------------------------------------
# Provider stubs disabled — no paid calls possible
# --------------------------------------------------------------------------
def test_provider_calls_disabled():
    assert LIVE_PROVIDER_CALLS_ENABLED is False
    assert_live_calls_disabled()  # does not raise while disabled
    with pytest.raises(ProviderCallDisabledError):
        OpenAIBaselineStub().lock_prediction(case_id="x")


def test_future_provider_call_mode_refused():
    with pytest.raises(RuntimeError):
        assert_mode_is_offline("future_provider_call")
    for ok in ("manual_output", "dry_run", "naive"):
        assert_mode_is_offline(ok)  # do not raise


# --------------------------------------------------------------------------
# CLI: dry-run does not write; --write writes only to the benchmark dir
# --------------------------------------------------------------------------
def _load_cli():
    path = _API / "scripts" / "phase_17b_lock_baseline_prediction.py"
    spec = importlib.util.spec_from_file_location("phase_17b_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_BUNDLE))
    rec_dir = tmp_path / "records"
    rc = _load_cli().main([
        "--case-id", "tomo_demo", "--method-id", "naive_uniform", "--method-class", "naive_baseline",
        "--method-version", "v1", "--input-bundle", str(bundle), "--naive", "uniform_distribution",
        "--records-dir", str(rec_dir),
    ])
    assert rc == 0
    assert not rec_dir.exists() or not list(rec_dir.glob("*.json"))


def test_cli_write_persists_only_to_records_dir(tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_BUNDLE))
    pred = tmp_path / "pred.json"
    pred.write_text(json.dumps(_VALID))
    rec_dir = tmp_path / "records"
    rc = _load_cli().main([
        "--case-id", "tomo_demo", "--method-id", "gpt_manual", "--method-class", "plain_llm",
        "--method-version", "manual", "--input-bundle", str(bundle), "--prediction-json", str(pred),
        "--records-dir", str(rec_dir), "--write",
    ])
    assert rc == 0
    written = list(rec_dir.glob("*.json"))
    assert len(written) == 1
    rec = json.loads(written[0].read_text())
    assert rec["purpose"] == "benchmark_baseline_prediction_not_validation_data"
    assert rec["observed"] is None and rec["mode"] == "manual_output"


def test_cli_rejects_invalid_prediction(tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_BUNDLE))
    pred = tmp_path / "pred.json"
    pred.write_text(json.dumps(_BAD_SUM))
    rc = _load_cli().main([
        "--case-id", "c", "--method-id", "m", "--method-class", "plain_llm", "--method-version", "v",
        "--input-bundle", str(bundle), "--prediction-json", str(pred),
    ])
    assert rc == 1  # schema validation refusal


def test_record_write_is_immutable(tmp_path):
    rec = BaselinePredictionRecord(
        benchmark_case_id="c", method_class="naive_baseline", method_id="m", method_version="v",
        input_bundle_hash="sha256:ib", prediction_payload=_VALID,
        prediction_hash=compute_prediction_hash(
            method_id="m", method_version="v", input_bundle_hash="sha256:ib",
            prediction_payload=_VALID, locked_at="2026-06-10T00:00:00+00:00",
        ),
        locked_at="2026-06-10T00:00:00+00:00", mode="naive",
    )
    write_record(rec, allow_write=True, records_dir=tmp_path)
    with pytest.raises(ValueError):
        write_record(rec, allow_write=True, records_dir=tmp_path)  # no overwrite
    assert len(load_records(tmp_path)) == 1


# --------------------------------------------------------------------------
# Runtime isolation
# --------------------------------------------------------------------------
def test_package_does_not_import_ledger_or_runtime():
    forbidden = ("assembly.validation_ledger", "assembly.config", "assembly.orchestration",
                 "assembly.llm", "assembly.market_calibration", "assembly.pipeline")
    for py in _PKG.glob("*.py"):
        src = py.read_text()
        for f in forbidden:
            assert f not in src, f"{py.name} must not import {f} (benchmark isolation)"


def test_records_dir_is_under_benchmarks_not_validation_cases():
    d = str(default_records_dir())
    assert "benchmarks/market_fidelity/baseline_predictions" in d
    assert "validation_cases" not in d


def test_validation_ledger_unchanged_and_15e_blocked():
    from assembly.validation_factory.outcome_mapping_protocol import mapping_readiness
    from assembly.validation_ledger.ingest import case_split_summary
    from assembly.validation_ledger.loader import load_all_cases
    cases = load_all_cases()
    assert len(cases) == 8
    assert case_split_summary(cases)["training"] == 6
    assert mapping_readiness(ledger_cases=cases)["phase_15e_blocked"] is True


# --------------------------------------------------------------------------
# Hardening regressions (from the Phase 17B adversarial review)
# --------------------------------------------------------------------------
def test_schema_failure_must_not_carry_buckets():
    # a schema_failure prediction that smuggles a full bucket set is REJECTED
    with pytest.raises(ValidationError):
        validate_prediction({**_VALID, "schema_failure": True, "schema_failure_reason": "hedged"})


def test_record_payload_must_conform_to_schema():
    # a record constructed directly (bypassing the CLI) cannot embed a fabricated
    # outcome / off-schema key in prediction_payload
    with pytest.raises(ValidationError):
        BaselinePredictionRecord(
            benchmark_case_id="c", method_class="plain_llm", method_id="m", method_version="v",
            input_bundle_hash="sha256:ib",
            prediction_payload={**_VALID, "observed": {"buyer_action_positive": 80}},
            prediction_hash="sha256:" + "a" * 64, locked_at="2026-06-10T00:00:00+00:00", mode="manual_output",
        )


def test_leakage_check_full_datetime():
    from assembly.benchmarks.market_fidelity.validators import check_no_post_lock_sources
    lock = "2026-06-10T12:00:00+00:00"
    assert check_no_post_lock_sources({"sources": [{"url": "a", "retrieved_at": "2026-06-09"}]}, lock) == []
    # same-day-but-LATER instant is leakage (day-granularity would have missed it)
    later = check_no_post_lock_sources({"sources": [{"url": "a", "retrieved_at": "2026-06-10T23:59:59+00:00"}]}, lock)
    assert later and "leakage" in later[0]
    # missing/unparseable retrieved_at is flagged (cannot verify)
    assert check_no_post_lock_sources({"sources": [{"url": "a"}]}, lock)
    # unparseable lock is flagged
    assert check_no_post_lock_sources({"sources": []}, "not-a-date")


def test_cli_refuses_post_lock_source(tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"sources": [{"url": "x", "retrieved_at": "2999-01-01T00:00:00+00:00"}]}))
    rc = _load_cli().main([
        "--case-id", "c", "--method-id", "naive_u", "--method-class", "naive_baseline",
        "--method-version", "v", "--input-bundle", str(bundle), "--naive", "uniform_distribution",
        "--locked-at", "2026-06-10T00:00:00+00:00",
    ])
    assert rc == 1  # leakage refusal (source retrieved far after the lock)


def test_category_prior_non_summing_is_schema_failure():
    bad = {"buyer_action_positive": 10.0, "receptive": 10.0, "uncertain_proof_needed": 10.0, "skeptical_resistant": 10.0}
    p = naive_baseline("category_prior_placeholder", {"category_prior": bad})  # sums to 40
    assert p.schema_failure is True


def test_category_prior_valid_path_uses_supplied_prior():
    p = naive_baseline("category_prior_placeholder", {"category_prior": _VALID})
    assert p.schema_failure is False
    assert p.buckets()["receptive"] == 50.0  # exactly the supplied prior


def test_metrics_boundaries():
    assert metrics.directional_hit(10.0, material_action=True) == "hit"  # exactly at threshold
    assert metrics.directional_hit(9.99, material_action=True) == "miss"
    # brier_multiclass is 0 when pred == obs (same un-normalized shape)
    same = {"buyer_action_positive": 10.0, "receptive": 50.0, "uncertain_proof_needed": 30.0, "skeptical_resistant": 10.0}
    assert metrics.brier_multiclass(same, same) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        metrics.brier_binary(1.5, True)  # out-of-range probability
