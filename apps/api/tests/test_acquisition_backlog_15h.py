"""Phase 15H — acquisition-backlog (planning-only) tests.

Covers the pure backlog helpers (load / validate / summary), the planning-only
discipline (do_not_ingest_yet, no smuggled case data, valid enums, unique ids),
and the SAFETY invariants: the backlog is never loaded by the ledger, the ledger
still holds exactly the 6 real seed cases, holdout/pending stay empty, and the
module is isolated from ledger scoring. Pure / deterministic: no LLM, no network,
no DB. Changes no forecast, adds no case.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from assembly.validation_ledger import load_all_cases, load_cases
from assembly.validation_ledger.acquisition_backlog import (
    backlog_summary,
    load_acquisition_backlog,
    validate_acquisition_backlog,
)

_APPS_API = Path(__file__).resolve().parent.parent
_CASES_DIR = _APPS_API / "validation_cases"
_BACKLOG_PATH = _CASES_DIR / "acquisition_backlog.json"
_MANIFEST_PATH = _CASES_DIR / "manifest.json"
_MODULE = _APPS_API / "src" / "assembly" / "validation_ledger" / "acquisition_backlog.py"


def _target(**over):
    t = {
        "target_id": "t-x",
        "candidate_name": "TBD — placeholder",
        "source_type": "github",
        "product_category": "developer_tools",
        "case_type": "retrospective_candidate",
        "priority": "high",
        "expected_outcome_tier": "tier1",
        "reason_for_inclusion": "fills a source gap",
        "leakage_risk_expected": "medium",
        "acquisition_status": "not_started",
        "do_not_ingest_yet": True,
    }
    t.update(over)
    return t


def _backlog(targets):
    return {
        "version": 1,
        "purpose": "planning_only_not_validation_data",
        "targets": targets,
    }


# --------------------------------------------------------------------------
# The shipped backlog
# --------------------------------------------------------------------------


def test_real_backlog_loads():
    b = load_acquisition_backlog()
    assert b["purpose"] == "planning_only_not_validation_data"
    assert isinstance(b["targets"], list) and len(b["targets"]) >= 1


def test_real_backlog_is_valid():
    assert validate_acquisition_backlog(load_acquisition_backlog()) == []


def test_real_backlog_targets_all_planning_only():
    b = load_acquisition_backlog()
    for t in b["targets"]:
        assert t["do_not_ingest_yet"] is True
        # planning targets carry NO observed/predicted outcomes
        assert "observed" not in t and "predicted" not in t


# --------------------------------------------------------------------------
# Validation discipline
# --------------------------------------------------------------------------


def test_missing_required_field_flagged():
    bad = _target()
    del bad["reason_for_inclusion"]
    issues = validate_acquisition_backlog(_backlog([bad]))
    assert any("reason_for_inclusion" in i for i in issues)


def test_do_not_ingest_yet_must_be_true():
    issues = validate_acquisition_backlog(_backlog([_target(do_not_ingest_yet=False)]))
    assert any("do_not_ingest_yet must be true" in i for i in issues)


def test_target_ids_must_be_unique():
    issues = validate_acquisition_backlog(
        _backlog([_target(target_id="dup"), _target(target_id="dup")])
    )
    assert any("duplicate target_id" in i for i in issues)


def test_invalid_acquisition_status_flagged():
    issues = validate_acquisition_backlog(_backlog([_target(acquisition_status="ingested")]))
    assert any("invalid acquisition_status" in i for i in issues)


def test_invalid_enums_flagged():
    issues = validate_acquisition_backlog(_backlog([
        _target(source_type="twitter", case_type="real_case",
                priority="urgent", expected_outcome_tier="tier5",
                leakage_risk_expected="none"),
    ]))
    assert any("invalid source_type" in i for i in issues)
    assert any("invalid case_type" in i for i in issues)
    assert any("invalid priority" in i for i in issues)
    assert any("invalid expected_outcome_tier" in i for i in issues)
    assert any("invalid leakage_risk_expected" in i for i in issues)


def test_smuggled_case_data_flagged():
    # a planning target must not carry real case data
    issues = validate_acquisition_backlog(_backlog([
        _target(observed={"buyer_action_positive": 50.0}),
    ]))
    assert any("must not carry 'observed'" in i for i in issues)


def test_wrong_purpose_flagged():
    issues = validate_acquisition_backlog(
        {"purpose": "validation_data", "targets": []}
    )
    assert any("purpose must be" in i for i in issues)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def test_backlog_summary_counts():
    b = load_acquisition_backlog()
    s = backlog_summary(b)
    assert s["n_targets"] == len(b["targets"])
    assert s["all_do_not_ingest_yet"] is True
    # counts partition the targets
    assert sum(s["by_source_type"].values()) == s["n_targets"]
    assert sum(s["by_priority"].values()) == s["n_targets"]
    assert sum(s["by_case_type"].values()) == s["n_targets"]
    for key in ("by_product_category", "by_expected_outcome_tier", "by_acquisition_status"):
        assert sum(s[key].values()) == s["n_targets"]


def test_summary_emits_no_market_distribution():
    s = backlog_summary(load_acquisition_backlog())
    buckets = {"buyer_action_positive", "receptive", "uncertain_proof_needed", "skeptical_resistant"}
    assert buckets.isdisjoint(set(s.keys()))


# --------------------------------------------------------------------------
# SAFETY — the backlog never becomes validation data
# --------------------------------------------------------------------------


def test_backlog_not_in_manifest():
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    paths = [
        (e["path"] if isinstance(e, dict) else e) for e in manifest.get("files", [])
    ]
    assert "acquisition_backlog.json" not in paths


def test_ledger_still_loads_six_real_cases_only():
    assert len(load_cases()) == 6  # seed frozen
    all_cases = load_all_cases()
    assert len([c for c in all_cases if c.anti_overfit.used_for_training]) == 6
    # any extra cases are blind Phase 16A prospective pending locks (observed=None)
    for c in all_cases:
        if c.metadata.validation_status == "pending":
            assert c.observed is None and not c.anti_overfit.used_for_training


def test_backlog_target_ids_are_not_ledger_case_ids():
    case_ids = {c.case_id for c in load_all_cases()}
    target_ids = {t["target_id"] for t in load_acquisition_backlog()["targets"]}
    assert case_ids.isdisjoint(target_ids)


def test_holdout_empty_and_pending_blind_locks_only():
    # holdout_cases.json stays empty; pending_cases.json may hold blind Phase 16A
    # prospective locks (prediction locked before outcome, observed=None).
    assert json.loads((_CASES_DIR / "holdout_cases.json").read_text()) == []
    for p in json.loads((_CASES_DIR / "pending_cases.json").read_text()):
        assert p.get("observed") is None
        assert p.get("anti_overfit", {}).get("used_for_holdout") is True
        assert p.get("anti_overfit", {}).get("used_for_training") in (False, None)


def test_acquisition_module_imports_only_stdlib():
    # The planning module must be ISOLATED from ledger scoring: no schema, no
    # loader, no metrics, no pydantic, no 'assembly' import at all.
    allowed = {"__future__", "json", "collections", "pathlib"}
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        top = None
        if isinstance(node, ast.Import):
            top = node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
        if top is not None:
            assert top in allowed, f"acquisition_backlog.py imports unexpected module: {top}"


def test_acquisition_module_has_no_forbidden_references():
    src = _MODULE.read_text(encoding="utf-8").lower()
    for tok in ("anthropic", "openai", "httpx", "requests", "sqlalchemy", "redis",
                "behavioral_mind_layer", "assembly_behavioral", "phase_13",
                "compute_case_metrics", "load_scored_ledger"):
        assert tok not in src, f"acquisition_backlog.py must not reference {tok}"
