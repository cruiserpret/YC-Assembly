"""Phase 15I — prediction-lock bridge tests.

Covers building a pending validation case from a completed run's artifacts, the
deterministic/path-free prediction_hash, the leakage/holdout defaults, the
missing-artifact and flat-prior refusals, the CLI, and import safety. Pure /
deterministic: no LLM, no network, no DB. Adds no observed outcome; changes no
forecast.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from assembly.validation_ledger.ingest import append_case_to_ledger
from assembly.validation_ledger.loader import load_all_cases
from assembly.validation_ledger.prediction_lock import (
    PREDICTION_HASH_SCHEMA_VERSION,
    compute_prediction_hash,
)
from assembly.validation_ledger.run_to_case import (
    RunArtifactsMissingError,
    RunPredictionUnusableError,
    build_pending_case_from_run,
)
from assembly.validation_ledger.schema import ValidationCase

_APPS_API = Path(__file__).resolve().parent.parent
_RUN_TO_CASE = _APPS_API / "src" / "assembly" / "validation_ledger" / "run_to_case.py"
_PRED_LOCK = _APPS_API / "src" / "assembly" / "validation_ledger" / "prediction_lock.py"
_BUCKETS = ("buyer_action_positive", "receptive", "uncertain_proof_needed", "skeptical_resistant")

# 10 buyer / 40 receptive / 25 uncertain / 25 skeptical -> 10/40/25/25 pp
_INTENT = {"would_buy_now": 10, "would_join_waitlist": 40, "unsure": 25, "would_reject": 25}


def _report(run_id="run-abc", intent=None, product_name="Acme Tool"):
    return {
        "schema_version": "10A.3.live.v1",
        "run_id": run_id,
        "product_brief": {"product_name": product_name},
        "synthetic_intent_snapshot": {"intent_distribution": intent if intent is not None else _INTENT},
    }


def _snapshot(completed="2026-05-20T10:00:00+00:00"):
    return {
        "evidence_snapshot_id": "evsnap_abc_123456",
        "snapshot_hash": "sha256:snaphash",
        "brief_hash": "sha256:briefhash",
        "normalized_brief_hash": "sha256:normbrief",
        "completed_at": completed,
    }


def _write_run(base: Path, run_id="run-abc", report=_report, snapshot=_snapshot):
    d = base / "live_runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    if report is not None:
        rep = report() if callable(report) else report
        (d / "founder_report.json").write_text(json.dumps(rep), encoding="utf-8")
    if snapshot is not None:
        snap = snapshot() if callable(snapshot) else snapshot
        (d / "evidence_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    return d


# --------------------------------------------------------------------------
# Building a pending case
# --------------------------------------------------------------------------


def test_build_pending_case_basic(tmp_path):
    d = _write_run(tmp_path)
    case, warnings = build_pending_case_from_run(
        "run-abc", source_type="product_hunt", product_category="developer_tools", run_dir=d
    )
    assert case.metadata.validation_status == "pending"
    assert case.metadata.product_name == "Acme Tool"
    assert case.metadata.source_type == "product_hunt"
    assert case.predicted is not None
    assert case.predicted.buyer_action_positive == 10.0
    assert case.predicted.receptive == 40.0
    assert case.predicted.uncertain_proof_needed == 25.0
    assert case.predicted.skeptical_resistant == 25.0
    assert case.prediction_lock.run_id == "run-abc"
    assert case.prediction_lock.prediction_hash.startswith("sha256:")
    assert case.prediction_lock.locked_prediction_created_at == "2026-05-20T10:00:00+00:00"
    assert case.prediction_lock.brief_hash == "sha256:briefhash"
    assert case.prediction_lock.evidence_snapshot_id == "evsnap_abc_123456"


def test_pending_has_predicted_but_no_observed(tmp_path):
    d = _write_run(tmp_path)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d)
    assert case.predicted is not None
    assert case.observed is None  # NEVER invent an outcome


def test_holdout_default_training_false(tmp_path):
    d = _write_run(tmp_path)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d)
    assert case.anti_overfit.used_for_holdout is True
    assert case.anti_overfit.used_for_training is False


def test_action_signals_empty(tmp_path):
    d = _write_run(tmp_path)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d)
    assert case.action_signals == []


# --------------------------------------------------------------------------
# prediction_hash: deterministic, drift-proof, path-free
# --------------------------------------------------------------------------


def test_prediction_hash_deterministic(tmp_path):
    d = _write_run(tmp_path)
    c1, _ = build_pending_case_from_run("run-abc", run_dir=d)
    c2, _ = build_pending_case_from_run("run-abc", run_dir=d)
    assert c1.prediction_lock.prediction_hash == c2.prediction_lock.prediction_hash


def test_changing_predicted_changes_hash(tmp_path):
    d1 = _write_run(tmp_path / "a", report=_report())
    d2 = _write_run(
        tmp_path / "b",
        report=_report(intent={"would_buy_now": 50, "would_join_waitlist": 20, "unsure": 15, "would_reject": 15}),
    )
    c1, _ = build_pending_case_from_run("run-abc", run_dir=d1)
    c2, _ = build_pending_case_from_run("run-abc", run_dir=d2)
    assert c1.prediction_lock.prediction_hash != c2.prediction_lock.prediction_hash


def test_filesystem_path_does_not_change_hash(tmp_path):
    # identical content under two different directories -> identical hash
    d1 = _write_run(tmp_path / "loc1")
    d2 = _write_run(tmp_path / "loc2")
    c1, _ = build_pending_case_from_run("run-abc", run_dir=d1)
    c2, _ = build_pending_case_from_run("run-abc", run_dir=d2)
    assert c1.prediction_lock.prediction_hash == c2.prediction_lock.prediction_hash


def test_compute_prediction_hash_float_drift_stable():
    a = compute_prediction_hash(
        run_id="r", predicted={"buyer_action_positive": 20.0, "receptive": 40.0,
                               "uncertain_proof_needed": 25.0, "skeptical_resistant": 15.0},
        locked_prediction_created_at="2026-05-20",
    )
    b = compute_prediction_hash(
        run_id="r", predicted={"buyer_action_positive": 20.00000001, "receptive": 40.0,
                               "uncertain_proof_needed": 25.0, "skeptical_resistant": 14.99999999},
        locked_prediction_created_at="2026-05-20",
    )
    assert a == b  # 4-decimal formatting absorbs drift
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 64


def test_compute_prediction_hash_distinct_on_real_change():
    a = compute_prediction_hash(
        run_id="r", predicted={"buyer_action_positive": 20.0, "receptive": 40.0,
                               "uncertain_proof_needed": 25.0, "skeptical_resistant": 15.0},
        locked_prediction_created_at="2026-05-20",
    )
    b = compute_prediction_hash(
        run_id="r", predicted={"buyer_action_positive": 25.0, "receptive": 35.0,
                               "uncertain_proof_needed": 25.0, "skeptical_resistant": 15.0},
        locked_prediction_created_at="2026-05-20",
    )
    assert a != b
    assert PREDICTION_HASH_SCHEMA_VERSION == "prediction_hash.v1"


def test_compute_prediction_hash_negative_zero_normalized():
    base = {"buyer_action_positive": 0.0, "receptive": 50.0,
            "uncertain_proof_needed": 25.0, "skeptical_resistant": 25.0}
    neg = {**base, "buyer_action_positive": -0.0}
    a = compute_prediction_hash(run_id="r", predicted=base, locked_prediction_created_at="2026-05-20")
    b = compute_prediction_hash(run_id="r", predicted=neg, locked_prediction_created_at="2026-05-20")
    assert a == b  # -0.0 and 0.0 must hash identically


def test_compute_prediction_hash_requires_all_buckets():
    with pytest.raises(ValueError):
        compute_prediction_hash(run_id="r", predicted={"buyer_action_positive": 100.0})


# --------------------------------------------------------------------------
# Refusals: missing artifacts, flat prior, no lock
# --------------------------------------------------------------------------


def test_missing_report_refuses(tmp_path):
    d = tmp_path / "live_runs" / "run-empty"
    d.mkdir(parents=True)
    with pytest.raises(RunArtifactsMissingError):
        build_pending_case_from_run("run-empty", run_dir=d)


def test_missing_report_allow_partial(tmp_path):
    d = tmp_path / "live_runs" / "run-empty"
    d.mkdir(parents=True)
    case, warnings = build_pending_case_from_run("run-empty", run_dir=d, allow_partial=True)
    assert case.metadata.validation_status == "partial"
    assert case.predicted is None
    assert case.prediction_lock.prediction_hash is None
    assert case.observed is None
    assert any("missing" in w for w in warnings)


def test_flat_prior_refused(tmp_path):
    d = _write_run(tmp_path, report=_report(intent={"would_buy_now": 0}))
    with pytest.raises(RunPredictionUnusableError):
        build_pending_case_from_run("run-abc", run_dir=d)


def test_no_lock_timestamp_refused(tmp_path):
    # report present (usable prediction) but no snapshot and no explicit lock ts
    d = _write_run(tmp_path, snapshot=None)
    with pytest.raises(RunPredictionUnusableError):
        build_pending_case_from_run("run-abc", run_dir=d)


def test_no_lock_timestamp_explicit_locked_at_ok(tmp_path):
    d = _write_run(tmp_path, snapshot=None)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d, locked_at="2026-05-21")
    assert case.prediction_lock.locked_prediction_created_at == "2026-05-21"
    assert case.predicted is not None


# --------------------------------------------------------------------------
# Schema / ledger integration
# --------------------------------------------------------------------------


def test_case_validates_and_round_trips(tmp_path):
    d = _write_run(tmp_path)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d)
    dumped = case.model_dump(mode="json", exclude_none=True)
    assert "observed" not in dumped  # never serialized
    reparsed = ValidationCase.model_validate(dumped)
    assert reparsed.case_id == case.case_id


def test_pending_case_appears_in_load_all_cases(tmp_path):
    d = _write_run(tmp_path)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d, case_id="run-abc-case")
    pending = tmp_path / "pending.json"
    pending.write_text("[]")
    append_case_to_ledger(case, pending)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": ["pending.json"]}))
    loaded = load_all_cases(manifest)
    assert "run-abc-case" in {c.case_id for c in loaded}


def test_append_rejects_duplicate(tmp_path):
    d = _write_run(tmp_path)
    case, _ = build_pending_case_from_run("run-abc", run_dir=d)
    f = tmp_path / "pending.json"
    f.write_text("[]")
    append_case_to_ledger(case, f)
    with pytest.raises(ValueError):
        append_case_to_ledger(case, f)


def test_env_artifact_root_resolution(tmp_path, monkeypatch):
    # end-to-end with the Phase 14C durable resolver (run_dir not passed)
    _write_run(tmp_path)
    monkeypatch.setenv("ASSEMBLY_ARTIFACT_ROOT", str(tmp_path))
    case, _ = build_pending_case_from_run("run-abc")
    assert case.prediction_lock.run_id == "run-abc"
    assert case.predicted is not None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _run(*args: str):
    return subprocess.run(
        [sys.executable, "scripts/phase_15i_create_case_from_run.py", *args],
        cwd=_APPS_API, capture_output=True, text=True,
    )


def test_cli_creates_pending_case(tmp_path):
    d = _write_run(tmp_path)
    out = tmp_path / "pending.json"
    out.write_text("[]")
    r = _run("--run-id", "run-abc", "--run-dir", str(d), "--output", str(out),
             "--source-type", "product_hunt", "--product-category", "developer_tools")
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())
    assert len(data) == 1
    assert data[0]["metadata"]["validation_status"] == "pending"
    assert "observed" not in data[0]


def test_cli_refuses_missing_artifacts(tmp_path):
    d = tmp_path / "live_runs" / "run-empty"
    d.mkdir(parents=True)
    out = tmp_path / "pending.json"
    out.write_text("[]")
    r = _run("--run-id", "run-empty", "--run-dir", str(d), "--output", str(out))
    assert r.returncode == 1
    assert "REFUSED" in r.stderr
    assert json.loads(out.read_text()) == []


def test_cli_allow_partial(tmp_path):
    d = tmp_path / "live_runs" / "run-empty"
    d.mkdir(parents=True)
    out = tmp_path / "pending.json"
    out.write_text("[]")
    r = _run("--run-id", "run-empty", "--run-dir", str(d), "--output", str(out), "--allow-partial")
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())[0]["metadata"]["validation_status"] == "partial"


def test_cli_refuses_duplicate(tmp_path):
    d = _write_run(tmp_path)
    out = tmp_path / "pending.json"
    out.write_text("[]")
    a = _run("--run-id", "run-abc", "--run-dir", str(d), "--output", str(out))
    assert a.returncode == 0, a.stderr
    b = _run("--run-id", "run-abc", "--run-dir", str(d), "--output", str(out))
    assert b.returncode == 1
    assert "REFUSED" in b.stderr
    assert len(json.loads(out.read_text())) == 1


def test_cli_print_only_does_not_append(tmp_path):
    d = _write_run(tmp_path)
    out = tmp_path / "pending.json"
    out.write_text("[]")
    r = _run("--run-id", "run-abc", "--run-dir", str(d), "--output", str(out), "--print-only")
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text()) == []  # nothing appended
    assert '"validation_status": "pending"' in r.stdout


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_modules_have_no_llm_network_db_refs():
    for path in (_RUN_TO_CASE, _PRED_LOCK):
        src = path.read_text(encoding="utf-8").lower()
        for tok in ("anthropic", "openai", "httpx", "requests", "aiohttp", "sqlalchemy",
                    "redis", "behavioral_mind_layer", "assembly_behavioral", "phase_13",
                    "token_", "credit_"):
            assert tok not in src, f"{path.name} must not reference {tok}"


def test_run_to_case_imports_are_safe():
    allowed = {"__future__", "json", "pathlib", "typing", "assembly"}
    tree = ast.parse(_RUN_TO_CASE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        top = None
        if isinstance(node, ast.Import):
            top = node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
        if top is not None:
            assert top in allowed, f"run_to_case imports unexpected module: {top}"


def test_prediction_lock_imports_only_stdlib_and_metrics():
    tree = ast.parse(_PRED_LOCK.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.add(node.names[0].name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert mods <= {"__future__", "hashlib", "json", "collections.abc",
                    "assembly.validation_ledger.metrics"}
