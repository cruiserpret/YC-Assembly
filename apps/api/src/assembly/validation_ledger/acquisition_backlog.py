"""Phase 15H — acquisition-backlog helpers (PLANNING ONLY, not validation data).

Pure, deterministic helpers to load + validate + summarize the Phase 15H
acquisition backlog (``validation_cases/acquisition_backlog.json``). The backlog
is a *planning* list of candidate targets to research and acquire later — it is
NOT a validation-ledger file, carries NO observed outcomes, and is NEVER loaded
by the ledger (it is intentionally absent from ``manifest.json``).

This module is deliberately ISOLATED from ledger scoring: it imports no schema,
no loader, no metrics. It cannot add a case, change a forecast, or apply
calibration. No LLM, no network, no DB.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# apps/api/validation_cases/acquisition_backlog.json
#   this file: apps/api/src/assembly/validation_ledger/acquisition_backlog.py
#   parents[3] -> apps/api
DEFAULT_BACKLOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "validation_cases"
    / "acquisition_backlog.json"
)

_SOURCE_TYPES = {
    "hacker_news", "product_hunt", "kickstarter", "reddit", "github",
    "app_store", "b2b", "mixed", "unknown",
}
_CASE_TYPES = {"retrospective_candidate", "prospective_candidate"}
_PRIORITIES = {"high", "medium", "low"}
_OUTCOME_TIERS = {"tier1", "tier2", "tier3", "mixed"}
_LEAKAGE_RISKS = {"low", "medium", "high", "unknown"}
_STATUSES = {"not_started", "needs_review", "ready_to_ingest", "rejected"}

_REQUIRED_TARGET_FIELDS = (
    "target_id", "candidate_name", "source_type", "product_category",
    "case_type", "priority", "expected_outcome_tier", "reason_for_inclusion",
    "leakage_risk_expected", "acquisition_status", "do_not_ingest_yet",
)

# A planning target must never carry real case data — these keys belong to a
# ValidationCase, not to an acquisition candidate.
_FORBIDDEN_CASE_KEYS = ("observed", "predicted", "metrics", "anti_overfit")


def load_acquisition_backlog(path: str | Path | None = None) -> dict:
    """Load the planning backlog JSON (a dict). PLANNING ONLY — the result is
    never a validation case and is never scored."""
    p = Path(path) if path is not None else DEFAULT_BACKLOG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def validate_acquisition_backlog(backlog: dict) -> list[str]:
    """Return a list of issues (empty == ok).

    Enforces planning-only discipline: the planning purpose marker, required
    fields, valid enum values, unique target_ids, ``do_not_ingest_yet=True`` on
    every target, and that no target smuggles in real case data.
    """
    issues: list[str] = []
    if backlog.get("purpose") != "planning_only_not_validation_data":
        issues.append(
            "backlog.purpose must be 'planning_only_not_validation_data'"
        )
    targets = backlog.get("targets")
    if not isinstance(targets, list):
        issues.append("backlog.targets must be a list")
        return issues

    seen_ids: set[str] = set()
    for i, t in enumerate(targets):
        where = t.get("target_id", f"index {i}") if isinstance(t, dict) else f"index {i}"
        if not isinstance(t, dict):
            issues.append(f"target {where}: must be an object")
            continue
        for f in _REQUIRED_TARGET_FIELDS:
            if f not in t:
                issues.append(f"target {where}: missing required field {f!r}")
        tid = t.get("target_id")
        if tid is not None:
            if tid in seen_ids:
                issues.append(f"duplicate target_id: {tid!r}")
            seen_ids.add(tid)
        if t.get("do_not_ingest_yet") is not True:
            issues.append(
                f"target {where}: do_not_ingest_yet must be true (planning only)"
            )
        if t.get("source_type") not in _SOURCE_TYPES:
            issues.append(f"target {where}: invalid source_type {t.get('source_type')!r}")
        if t.get("case_type") not in _CASE_TYPES:
            issues.append(f"target {where}: invalid case_type {t.get('case_type')!r}")
        if t.get("priority") not in _PRIORITIES:
            issues.append(f"target {where}: invalid priority {t.get('priority')!r}")
        if t.get("expected_outcome_tier") not in _OUTCOME_TIERS:
            issues.append(
                f"target {where}: invalid expected_outcome_tier "
                f"{t.get('expected_outcome_tier')!r}"
            )
        if t.get("leakage_risk_expected") not in _LEAKAGE_RISKS:
            issues.append(
                f"target {where}: invalid leakage_risk_expected "
                f"{t.get('leakage_risk_expected')!r}"
            )
        if t.get("acquisition_status") not in _STATUSES:
            issues.append(
                f"target {where}: invalid acquisition_status "
                f"{t.get('acquisition_status')!r}"
            )
        for forbidden in _FORBIDDEN_CASE_KEYS:
            if forbidden in t:
                issues.append(
                    f"target {where}: must not carry {forbidden!r} — this is a "
                    "planning backlog, not a validation case"
                )
    return issues


def backlog_summary(backlog: dict) -> dict:
    """Counts of targets by source / category / priority / tier / status /
    case_type. Emits NO market distribution — planning metadata only."""
    targets = [t for t in backlog.get("targets", []) if isinstance(t, dict)]
    return {
        "n_targets": len(targets),
        "all_do_not_ingest_yet": all(
            t.get("do_not_ingest_yet") is True for t in targets
        ),
        "by_source_type": dict(Counter(t.get("source_type") for t in targets)),
        "by_product_category": dict(
            Counter(t.get("product_category") for t in targets)
        ),
        "by_priority": dict(Counter(t.get("priority") for t in targets)),
        "by_expected_outcome_tier": dict(
            Counter(t.get("expected_outcome_tier") for t in targets)
        ),
        "by_acquisition_status": dict(
            Counter(t.get("acquisition_status") for t in targets)
        ),
        "by_case_type": dict(Counter(t.get("case_type") for t in targets)),
    }
