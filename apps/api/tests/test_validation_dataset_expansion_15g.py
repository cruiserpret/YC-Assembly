"""Phase 15G — validation dataset expansion tests.

Covers the split-file structure + manifest loader, the ingest helpers, the
leakage discipline, coverage summaries, both CLIs, and safety. Pure /
deterministic: no LLM, no network, no DB. Changes no forecast.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.validation_ledger import (
    action_signal_coverage_summary,
    build_validation_case_from_payload,
    case_split_summary,
    is_clean_holdout,
    load_all_cases,
    load_cases,
    required_fields_for_status,
    tier_coverage_summary,
    validate_no_outcome_leakage,
    validate_prediction_lock,
)
from assembly.validation_ledger.ingest import append_case_to_ledger
from assembly.validation_ledger.loader import load_all_cases as _lac

_APPS_API = Path(__file__).resolve().parent.parent
_INGEST = _APPS_API / "src" / "assembly" / "validation_ledger" / "ingest.py"
_BUCKETS = ("buyer_action_positive", "receptive", "uncertain_proof_needed",
            "skeptical_resistant")


def _payload(cid, *, status="scored", source="github", category="developer_tools",
             pred=(10, 40, 25, 25), obs=(10, 30, 30, 30), training=False, holdout=True,
             locked="2026-06-01", observed_at="2026-06-10", leakage="low",
             action_signals=None, with_obs=True):
    p = {
        "case_id": cid,
        "metadata": {
            "product_name": "P", "source_type": source, "product_category": category,
            "launch_stage": "launched", "date_run": "2026-06-01",
            "validation_status": status,
        },
        "prediction_lock": {"locked_prediction_created_at": locked, "leakage_risk": leakage},
        "anti_overfit": {"used_for_training": training, "used_for_holdout": holdout},
    }
    if pred is not None:
        p["predicted"] = dict(zip(_BUCKETS, pred, strict=True))
    if with_obs and obs is not None:
        d = dict(zip(_BUCKETS, obs, strict=True))
        d["observed_at"] = observed_at
        p["observed"] = d
    if action_signals is not None:
        p["action_signals"] = action_signals
    return p


# --------------------------------------------------------------------------
# Loading + split files
# --------------------------------------------------------------------------


def test_existing_seed_still_loads():
    assert len(load_cases()) == 6  # seed frozen
    # seed training stays 6; Phase 16A may add blind prospective pending locks.
    assert len([c for c in load_all_cases() if c.anti_overfit.used_for_training]) == 6


def test_loader_merges_split_files(tmp_path):
    (tmp_path / "seed.json").write_text(json.dumps([_payload("s1", training=True, holdout=False)]))
    (tmp_path / "holdout.json").write_text(json.dumps([_payload("h1")]))
    (tmp_path / "pending.json").write_text(json.dumps([
        _payload("p1", status="pending", pred=None, with_obs=False, holdout=False)
    ]))
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"files": ["seed.json", "holdout.json", "pending.json"]}
    ))
    cases = _lac(tmp_path / "manifest.json")
    assert {c.case_id for c in cases} == {"s1", "h1", "p1"}


def test_loader_dedupes_and_rejects_duplicate_ids(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([_payload("dup")]))
    (tmp_path / "b.json").write_text(json.dumps([_payload("dup")]))
    (tmp_path / "manifest.json").write_text(json.dumps({"files": ["a.json", "b.json"]}))
    with pytest.raises(ValueError):
        _lac(tmp_path / "manifest.json")


# --------------------------------------------------------------------------
# Split / status discipline
# --------------------------------------------------------------------------


def test_training_and_holdout_non_overlapping():
    # schema forbids a single case being both
    with pytest.raises(ValidationError):
        build_validation_case_from_payload(_payload("x", training=True, holdout=True))


def test_pending_case_can_omit_observed():
    c = build_validation_case_from_payload(
        _payload("pend", status="pending", pred=None, with_obs=False, holdout=False)
    )
    assert not c.is_scorable()
    assert c.observed is None


def test_scored_case_requires_observed():
    with pytest.raises(ValidationError):
        build_validation_case_from_payload(
            _payload("bad", status="scored", with_obs=False)
        )


def test_required_fields_for_status():
    assert "observed" in required_fields_for_status("scored")
    assert required_fields_for_status("pending") == ["prediction_lock"]


# --------------------------------------------------------------------------
# Leakage discipline
# --------------------------------------------------------------------------


def test_leakage_rejects_prediction_locked_after_outcome():
    leaky = build_validation_case_from_payload(
        _payload("leak", locked="2026-06-20", observed_at="2026-06-10")
    )
    issues = validate_no_outcome_leakage(leaky)
    assert any("earlier than the locked prediction" in i for i in issues)


def test_clean_case_has_no_leakage_issues():
    clean = build_validation_case_from_payload(
        _payload("clean", locked="2026-06-01", observed_at="2026-06-30")
    )
    assert validate_no_outcome_leakage(clean) == []
    assert validate_prediction_lock(clean) == []
    assert is_clean_holdout(clean) is True


def test_high_leakage_risk_excluded_from_clean_holdout():
    c = build_validation_case_from_payload(_payload("risky", leakage="high"))
    assert c.anti_overfit.used_for_holdout is True
    assert is_clean_holdout(c) is False  # stored, but not clean holdout


def test_scored_holdout_requires_explicit_leakage_risk():
    c = build_validation_case_from_payload(_payload("noexp", leakage="unknown"))
    assert any("leakage_risk must be set explicitly" in i
               for i in validate_no_outcome_leakage(c))


def test_missing_lock_flagged_for_holdout():
    c = build_validation_case_from_payload(_payload("nolock", locked=None))
    assert validate_prediction_lock(c)  # non-empty -> flagged


# --------------------------------------------------------------------------
# Action signals + coverage
# --------------------------------------------------------------------------


def test_action_signals_validate_and_tier_autofills():
    c = build_validation_case_from_payload(_payload("sig", action_signals=[
        {"signal_type": "kickstarter_pledge", "count": 900, "direction": "positive"},
        {"signal_type": "github_star", "count": 200, "direction": "positive"},
    ]))
    assert [s.tier for s in c.action_signals] == [1, 2]


def test_coverage_summaries():
    cases = [
        build_validation_case_from_payload(_payload("c1", action_signals=[
            {"signal_type": "purchase", "count": 10}])),
        build_validation_case_from_payload(_payload("c2")),  # no signals
    ]
    tiers = tier_coverage_summary(cases)
    assert tiers["tier1_case_count"] == 1
    action = action_signal_coverage_summary(cases)
    assert action["cases_with_action_signals"] == 1
    assert action["cases_without_action_signals"] == 1


def test_split_summary_on_seed():
    s = case_split_summary(load_all_cases())
    # seed training frozen at 6; ZERO train/holdout overlap (the core leakage
    # guard). Phase 16A may add blind prospective holdout/pending cases.
    assert s["training"] == 6
    assert s["train_holdout_overlap"] == 0
    assert s["n_cases"] >= 6


def test_append_rejects_duplicate(tmp_path):
    f = tmp_path / "h.json"
    f.write_text("[]")
    c = build_validation_case_from_payload(_payload("a1"))
    append_case_to_ledger(c, f)
    assert len(json.loads(f.read_text())) == 1
    with pytest.raises(ValueError):
        append_case_to_ledger(c, f)  # duplicate case_id


# --------------------------------------------------------------------------
# Output discipline (no forecast emitted)
# --------------------------------------------------------------------------


def test_summaries_emit_no_market_distribution():
    cases = load_all_cases()
    for summary in (case_split_summary(cases), tier_coverage_summary(cases),
                    action_signal_coverage_summary(cases)):
        assert set(_BUCKETS).isdisjoint(set(summary.keys()))


# --------------------------------------------------------------------------
# CLIs (subprocess — real behavior)
# --------------------------------------------------------------------------


def _run(script: str, *args: str):
    return subprocess.run(
        [sys.executable, f"scripts/{script}", *args],
        cwd=_APPS_API, capture_output=True, text=True,
    )


def test_summary_cli_runs_and_warns():
    r = _run("phase_15g_validation_dataset_summary.py")
    assert r.returncode == 0
    # still under the 20-case threshold -> readiness warning fires (15E blocked),
    # regardless of the first Phase 16A prospective holdout lock.
    assert "fewer than 20 cases" in r.stdout


def test_add_case_cli_accepts_valid_and_refuses_leaky(tmp_path):
    holdout = tmp_path / "holdout.json"
    holdout.write_text("[]")
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(_payload("cli_valid")))
    r = _run("phase_15g_add_validation_case.py", str(valid), "--to", str(holdout))
    assert r.returncode == 0, r.stderr
    assert len(json.loads(holdout.read_text())) == 1

    leaky = tmp_path / "leaky.json"
    leaky.write_text(json.dumps(_payload("cli_leaky", locked="2026-06-20", observed_at="2026-06-10")))
    r2 = _run("phase_15g_add_validation_case.py", str(leaky), "--to", str(holdout))
    assert r2.returncode == 1
    assert "REFUSED" in r2.stderr
    assert len(json.loads(holdout.read_text())) == 1  # leaky NOT appended


def test_add_case_cli_refuses_malformed_schema(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"case_id": "x", "metadata": {"source_type": "twitter"}}))
    r = _run("phase_15g_add_validation_case.py", str(bad), "--to", str(tmp_path / "h.json"))
    assert r.returncode == 1
    assert "REFUSED" in r.stderr


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_ingest_no_forbidden_imports_or_references():
    src = _INGEST.read_text(encoding="utf-8").lower()
    for tok in ("anthropic", "openai", "httpx", "requests", "aiohttp", "sqlalchemy",
                "redis", "behavioral_mind_layer", "assembly_behavioral", "phase_13",
                "token_", "credit_", "live_founder_brief", "artifact_paths"):
        assert tok not in src, f"ingest.py must not reference {tok}"


def test_ingest_imports_only_stdlib_and_self():
    allowed = {"__future__", "json", "pathlib", "typing", "collections", "pydantic",
               "assembly"}
    tree = ast.parse(_INGEST.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        top = None
        if isinstance(node, ast.Import):
            top = node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
        if top is not None:
            assert top in allowed, f"ingest.py imports unexpected module: {top}"
