"""Phase 16B-R — prospective PARTIAL outcome recording convention tests.

A prospective lock whose outcome is a buyer/action ANCHOR ONLY is recorded WITHOUT
fabricating the non-buyer buckets: observed stays null, validation_status -> 'partial',
the buyer anchor goes in action_signals, the lock (prediction_hash) is unchanged, and a
separate audit-only record lives under prospective_outcomes/ (absent from the manifest).
This must NOT become a direct_observed_distribution and must NOT unlock Phase 15E.
Pure/deterministic: no LLM, no network, no DB.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from assembly.market_calibration.calibration_diagnostics import (
    build_calibration_diagnostics_report,  # noqa: F401
)
from assembly.validation_factory.outcome_mapping_protocol import mapping_readiness
from assembly.validation_factory.promotion_bridge import ledger_direct_observed_count
from assembly.validation_ledger.ingest import (
    action_signal_coverage_summary,
    case_split_summary,
    is_clean_holdout,
    tier_coverage_summary,
)
from assembly.validation_ledger.loader import load_all_cases

_API = Path(__file__).resolve().parents[1]
_CASES = _API / "validation_cases"
_OUTCOMES = _CASES / "prospective_outcomes"
_RECORD = _OUTCOMES / "run_7ed43d56-566d-47f0-b7c3-3cee4c97ab1f.json"
_HO_ID = "run_7ed43d56-566d-47f0-b7c3-3cee4c97ab1f"
_TOMO_ID = "run_4fcc4cbf-64d5-478f-a4a1-88df1a5c6ea9"
_PURPOSE = "prospective_partial_outcome_not_observed_distribution"


def _record() -> dict:
    return json.loads(_RECORD.read_text())


def _pending() -> list[dict]:
    return json.loads((_CASES / "pending_cases.json").read_text())


def _ho_case() -> dict:
    return next(c for c in _pending() if c["case_id"] == _HO_ID)


def _load_verifier():
    path = _API / "scripts" / "phase_16b_verify_prospective_partial_outcome.py"
    spec = importlib.util.spec_from_file_location("phase_16b_verify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Isolation: never loaded as validation data
# --------------------------------------------------------------------------


def test_prospective_outcomes_absent_from_manifest():
    manifest = json.loads((_CASES / "manifest.json").read_text())
    paths = {f["path"] if isinstance(f, dict) else f for f in manifest.get("files", [])}
    assert not any("prospective_outcome" in str(p) for p in paths)


def test_outcome_record_not_loaded_as_a_case():
    case_ids = {c.case_id for c in load_all_cases()}
    # the record's run-id stem references an EXISTING case, never a NEW one
    assert _record()["pending_case_id"] in case_ids
    # load total is unchanged by the presence of prospective_outcomes/
    assert len(load_all_cases()) == 8


# --------------------------------------------------------------------------
# observed stays null + no fabricated buckets
# --------------------------------------------------------------------------


def test_observed_stays_null_no_fabricated_buckets():
    ho = next(c for c in load_all_cases() if c.case_id == _HO_ID)
    assert ho.observed is None
    # the record itself carries no fabricated four-bucket observed distribution
    rec = _record()
    assert rec.get("observed") is None
    assert "observed_proportions" not in rec
    assert rec["observed_remains_null"] is True
    assert rec["not_direct_observed_distribution"] is True
    # the 'outcome' block is a buyer anchor only — no non-buyer buckets
    for b in ("receptive", "uncertain_proof_needed", "skeptical_resistant"):
        assert b not in rec["outcome"]


# --------------------------------------------------------------------------
# status partial + buyer anchor in action_signals
# --------------------------------------------------------------------------


def test_hollowed_oath_status_partial_with_tier1_anchor():
    ho = next(c for c in load_all_cases() if c.case_id == _HO_ID)
    assert ho.metadata.validation_status == "partial"
    assert len(ho.action_signals) == 1
    sig = ho.action_signals[0]
    assert sig.signal_type == "kickstarter_pledge"
    assert sig.tier == 1  # auto-classified
    assert sig.direction == "positive"
    assert sig.count == 698
    assert sig.denominator is None  # self-selected; no representative denominator


def test_tomo_remains_untouched_and_pending():
    tomo = next(c for c in load_all_cases() if c.case_id == _TOMO_ID)
    assert tomo.metadata.validation_status == "pending"
    assert tomo.observed is None
    assert tomo.action_signals == []


# --------------------------------------------------------------------------
# lock immutability — prediction_hash unchanged after the action-signal insertion
# --------------------------------------------------------------------------


def test_prediction_hash_unchanged_after_partial_scoring():
    ho = next(c for c in load_all_cases() if c.case_id == _HO_ID)
    expected = "sha256:e1fcfb59904d0ab0cd9a9a645b7903a0a93c5e09d8fb87fe84fcf12479b36e0e"
    assert ho.prediction_lock.prediction_hash == expected
    assert _record()["locked_prediction_hash"] == expected
    # the lock-provenance record (prospective_locks/) stays blind/immutable
    lock = json.loads((_CASES / "prospective_locks" / f"run_{_HO_ID.split('run_')[-1]}.json").read_text())
    assert lock["observed"] is None
    assert "action_signals" not in lock or not lock["action_signals"]


# --------------------------------------------------------------------------
# dataset semantics — separated metrics, 15E still blocked
# --------------------------------------------------------------------------


def test_dataset_split_after_partial_scoring():
    s = case_split_summary(load_all_cases())
    assert s["n_cases"] == 8
    assert s["training"] == 6
    assert s["pending"] == 1
    assert s["partial"] == 1
    assert s["clean_holdout"] == 2  # is_clean_holdout keys on holdout+lock, not status
    assert s["train_holdout_overlap"] == 0


def test_action_signal_and_tier_coverage_separated_from_direct_observed():
    cases = load_all_cases()
    assert action_signal_coverage_summary(cases)["cases_with_action_signals"] == 1
    assert tier_coverage_summary(cases)["tier1_case_count"] == 1
    # the buyer anchor is NOT a measured distribution: direct-observed count stays 0
    assert ledger_direct_observed_count(cases) == 0


def test_partial_scoring_keeps_clean_holdout():
    ho = next(c for c in load_all_cases() if c.case_id == _HO_ID)
    assert is_clean_holdout(ho) is True


def test_phase_15e_remains_blocked():
    r = mapping_readiness(ledger_cases=load_all_cases())
    assert r["phase_15e_blocked"] is True
    assert r["n_direct_observed_distribution_cases"] == 0
    # a real Tier-1 action outcome now exists, but the direct-observed bar is unmet
    assert r["ledger_tier1_2_outcome_cases"] >= 1


# --------------------------------------------------------------------------
# the read-only verifier
# --------------------------------------------------------------------------


def test_verifier_passes_on_the_real_record():
    assert _load_verifier().verify(_record(), _pending()) == []


def test_verifier_rejects_fabricated_observed_buckets():
    v = _load_verifier()
    rec = _record()
    rec["observed"] = {"buyer_action_positive": 60, "receptive": 20,
                       "uncertain_proof_needed": 10, "skeptical_resistant": 10}
    fails = v.verify(rec, _pending())
    assert any("observed" in f for f in fails)


def test_verifier_rejects_observed_in_outcome_block():
    v = _load_verifier()
    rec = _record()
    rec["outcome"] = {**rec["outcome"], "receptive": 25.0}
    fails = v.verify(rec, _pending())
    assert any("fabricated" in f for f in fails)


def test_verifier_rejects_direct_observed_marking():
    v = _load_verifier()
    rec = _record()
    rec["mapping_type"] = "direct_observed_distribution"
    fails = v.verify(rec, _pending())
    assert any("mapping_type" in f for f in fails)


def test_verifier_rejects_changed_lock_hash():
    v = _load_verifier()
    rec = _record()
    rec["locked_prediction_hash"] = "sha256:" + "0" * 64
    fails = v.verify(rec, _pending())
    assert any("locked_prediction_hash" in f or "self-reproduce" in f for f in fails)


# --------------------------------------------------------------------------
# Hardening regressions (from the Phase 16B-R adversarial review)
# --------------------------------------------------------------------------

_FAB = {"buyer_action_positive": 60, "receptive": 20, "uncertain_proof_needed": 10,
        "skeptical_resistant": 10}


def test_verifier_rejects_offkey_fabricated_distribution():
    # a four-bucket split smuggled under a NON-canonical top-level key
    v = _load_verifier()
    rec = {**_record(), "measured_distribution": dict(_FAB)}
    assert any("fabricated" in f for f in v.verify(rec, _pending()))


def test_verifier_rejects_nested_fabricated_distribution():
    # a four-bucket split nested under outcome.distribution
    v = _load_verifier()
    rec = _record()
    rec["outcome"] = {**rec["outcome"], "distribution": dict(_FAB)}
    assert any("fabricated" in f for f in v.verify(rec, _pending()))


def test_verifier_rejects_observed_true_marker():
    # observed must be strictly null — a bool True must NOT be accepted
    v = _load_verifier()
    rec = {**_record(), "observed": True}
    assert any("observed" in f for f in v.verify(rec, _pending()))


def test_verifier_rejects_case_side_fabrication():
    # a fabricated four-bucket dict on the CASE (not just the record) is caught
    v = _load_verifier()
    pend = _pending()
    next(c for c in pend if c["case_id"] == _HO_ID)["fabricated_observed"] = dict(_FAB)
    assert any("case carries a fabricated" in f for f in v.verify(_record(), pend))


def test_verifier_rejects_tampered_other_lock_flipped_to_partial():
    # Tomo (no approved outcome record) flipped to 'partial' + given action_signals
    # must NOT escape the blind-other-locks check (status-agnostic).
    v = _load_verifier()
    pend = _pending()
    tomo = next(c for c in pend if c["case_id"] == _TOMO_ID)
    tomo["metadata"]["validation_status"] = "partial"
    tomo["action_signals"] = [{"signal_type": "kickstarter_pledge", "direction": "positive"}]
    fails = v.verify(_record(), pend)
    assert any(_TOMO_ID in f and "blind" in f for f in fails)


def test_16ap_verifier_rejects_flip_to_partial_without_outcome_record():
    # the 16A-P lock verifier must reject a lock case flipped to a non-pending status
    # that has no backing prospective_outcomes record.
    path = _API / "scripts" / "phase_16a_verify_lock_provenance.py"
    spec = importlib.util.spec_from_file_location("phase_16a_verify_h", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lock = json.loads((_CASES / "prospective_locks" / f"{_TOMO_ID}.json").read_text())
    pend = _pending()
    next(c for c in pend if c["case_id"] == _TOMO_ID)["metadata"]["validation_status"] = "partial"
    fails = mod.verify(lock, pend)
    assert any("prospective_outcomes record" in f for f in fails)
