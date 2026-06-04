"""Phase 16A-P — prospective-lock provenance hardening tests.

The compact provenance records are AUDIT SUPPORT only: never loaded as validation
cases, never carry an observed outcome, and their prediction_hash matches (and
self-reproduces from) the committed pending case. Pure/deterministic.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from assembly.validation_ledger.loader import load_all_cases

_API = Path(__file__).resolve().parents[1]
_CASES = _API / "validation_cases"
_LOCKS = _CASES / "prospective_locks"
_RECORD = _LOCKS / "run_7ed43d56-566d-47f0-b7c3-3cee4c97ab1f.json"
_PURPOSE = "prospective_lock_provenance_not_observed_outcome"
_FORBIDDEN = ("observed_proportions", "observed_outcome", "observed_at", "metrics", "final_pledged")


def _record() -> dict:
    return json.loads(_RECORD.read_text())


def _pending() -> list[dict]:
    return json.loads((_CASES / "pending_cases.json").read_text())


def _load_helper():
    path = _API / "scripts" / "phase_16a_verify_lock_provenance.py"
    spec = importlib.util.spec_from_file_location("phase_16a_verify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Isolation: never loaded as validation data
# --------------------------------------------------------------------------


def test_prospective_locks_absent_from_manifest():
    manifest = json.loads((_CASES / "manifest.json").read_text())
    paths = {f["path"] if isinstance(f, dict) else f for f in manifest.get("files", [])}
    assert not any("prospective_lock" in p for p in paths)


def test_provenance_not_loaded_as_validation_case():
    # the loader is manifest-driven; provenance files are NOT loaded as cases, so the
    # ledger total is exactly seed-training (6) + the pending locks, never inflated by
    # the records in prospective_locks/ (robust to additional approved locks).
    cases = load_all_cases()
    case_ids = {c.case_id for c in cases}
    n_pending = sum(1 for c in cases if c.metadata.validation_status == "pending")
    assert len(cases) == 6 + n_pending
    # EVERY provenance record references an EXISTING case (support, not a new case)
    for rp in sorted(_LOCKS.glob("run_*.json")):
        rec = json.loads(rp.read_text())
        assert rec["pending_case_id"] in case_ids
        assert rec["observed"] is None


# --------------------------------------------------------------------------
# Provenance integrity
# --------------------------------------------------------------------------


def test_record_purpose_and_no_observed_outcome():
    rec = _record()
    assert rec["purpose"] == _PURPOSE
    assert rec["observed"] is None
    assert not any(f in rec for f in _FORBIDDEN)


def test_prediction_hash_matches_pending_case():
    rec = _record()
    case = next(c for c in _pending() if c["case_id"] == rec["pending_case_id"])
    assert rec["prediction_hash"] == case["prediction_lock"]["prediction_hash"]
    assert rec["predicted_proportions"] == case["predicted"]


def test_pending_case_remains_observed_free_and_blind():
    case = next(c for c in _pending() if c["case_id"] == _record()["pending_case_id"])
    assert "observed" not in case
    assert case["anti_overfit"]["used_for_holdout"] is True
    assert case["anti_overfit"]["used_for_training"] is False
    assert case.get("action_signals", []) == []


def test_no_training_data_changed():
    all_cases = load_all_cases()
    assert len([c for c in all_cases if c.anti_overfit.used_for_training]) == 6
    assert json.loads((_CASES / "training_cases.json").read_text()) == []


# --------------------------------------------------------------------------
# The read-only verify helper
# --------------------------------------------------------------------------


def test_helper_verifies_the_real_record():
    helper = _load_helper()
    assert helper.verify(_record(), _pending()) == []


def test_helper_recomputes_hash_self_auditing():
    # tamper the predicted proportions -> the recomputed hash must NOT match
    helper = _load_helper()
    rec = _record()
    rec["predicted_proportions"] = {**rec["predicted_proportions"], "buyer_action_positive": 99.0}
    fails = helper.verify(rec, _pending())
    assert any("prediction_hash" in f for f in fails)


def test_helper_rejects_smuggled_observed_outcome():
    helper = _load_helper()
    rec = _record()
    rec["observed_proportions"] = {"buyer_action_positive": 80}
    rec["observed"] = {"buyer_action_positive": 80}
    fails = helper.verify(rec, _pending())
    assert any("observed" in f for f in fails)
