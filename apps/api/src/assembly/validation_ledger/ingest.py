"""Phase 15G — validation-case ingest + dataset-discipline helpers.

Pure, deterministic helpers to grow the validation ledger SAFELY: build a case
from a JSON payload, validate its prediction lock, check for outcome leakage,
append it to a split ledger file, and summarize coverage. No LLM, no network,
no DB, no forecast/calibration change, no product-name logic. Observed outcomes
are touched only to validate locking discipline — never as a model input.

Leakage discipline:
  - scored / holdout cases must carry ``prediction_lock.locked_prediction_created_at``,
  - an observed-outcome date, if present, must NOT be earlier than the locked
    prediction date (a prediction must be locked before the outcome is seen),
  - ``leakage_risk`` must be explicit for scored/holdout cases,
  - a high-leakage-risk case may be STORED but is not a clean-holdout case.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from assembly.validation_ledger.schema import ValidationCase

_BUCKETS = ("buyer_action_positive", "receptive", "uncertain_proof_needed",
            "skeptical_resistant")


def _date_key(s: str | None) -> str | None:
    """Normalize an ISO date/datetime string to its YYYY-MM-DD prefix for a
    chronologically-correct lexicographic compare. None passes through."""
    return s[:10] if isinstance(s, str) and len(s) >= 10 else s


def required_fields_for_status(status: str) -> list[str]:
    """The fields a case must carry for a given validation_status."""
    return {
        "scored": [
            "predicted", "observed",
            "prediction_lock.locked_prediction_created_at",
        ],
        "partial": ["predicted_or_observed"],
        "pending": ["prediction_lock"],
        "excluded": [],
    }.get(status, [])


def build_validation_case_from_payload(payload: dict) -> ValidationCase:
    """Parse + validate a raw payload dict into a ValidationCase. Raises
    pydantic ValidationError on a malformed payload. No outcomes invented."""
    return ValidationCase.model_validate(payload)


def validate_prediction_lock(case: ValidationCase) -> list[str]:
    """Return a list of prediction-lock issues (empty == ok)."""
    issues: list[str] = []
    needs_lock = (
        case.metadata.validation_status == "scored"
        or case.anti_overfit.used_for_holdout
    )
    if needs_lock and not case.prediction_lock.locked_prediction_created_at:
        issues.append(
            "scored/holdout case is missing "
            "prediction_lock.locked_prediction_created_at"
        )
    return issues


def validate_no_outcome_leakage(case: ValidationCase) -> list[str]:
    """Return a list of leakage issues (empty == ok)."""
    issues: list[str] = []
    locked = _date_key(case.prediction_lock.locked_prediction_created_at)
    observed_at = _date_key(case.observed.observed_at if case.observed else None)
    if locked and observed_at and observed_at < locked:
        issues.append(
            "observed outcome date is earlier than the locked prediction date "
            "— prediction may have seen the outcome (leakage)"
        )
    needs_explicit = (
        case.metadata.validation_status == "scored"
        or case.anti_overfit.used_for_holdout
    )
    if needs_explicit and case.prediction_lock.leakage_risk == "unknown":
        issues.append(
            "leakage_risk must be set explicitly (not 'unknown') for "
            "scored/holdout cases"
        )
    return issues


def is_clean_holdout(case: ValidationCase) -> bool:
    """A clean-holdout case is used_for_holdout, has no leakage issues, and is
    not flagged high-leakage-risk. High-risk cases may be stored but are
    excluded from clean holdout by default."""
    return (
        case.anti_overfit.used_for_holdout
        and case.prediction_lock.leakage_risk != "high"
        and not validate_prediction_lock(case)
        and not validate_no_outcome_leakage(case)
    )


def append_case_to_ledger(case: ValidationCase, path: str | Path) -> None:
    """Append a validated case to a split ledger JSON file (a JSON list).
    Raises ValueError on a duplicate case_id. Deterministic, no network."""
    p = Path(path)
    existing: list[dict] = []
    if p.exists():
        text = p.read_text(encoding="utf-8").strip()
        if text:
            raw = json.loads(text)
            existing = raw["cases"] if isinstance(raw, dict) else raw
    if any(item.get("case_id") == case.case_id for item in existing):
        raise ValueError(f"case_id {case.case_id!r} already present in {p.name}")
    existing.append(case.model_dump(mode="json", exclude_none=True))
    p.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def case_split_summary(cases: Sequence[ValidationCase]) -> dict[str, int]:
    """Counts by status and train/holdout split, plus leakage flags."""
    by_status = {"scored": 0, "partial": 0, "pending": 0, "excluded": 0}
    for c in cases:
        by_status[c.metadata.validation_status] = (
            by_status.get(c.metadata.validation_status, 0) + 1
        )
    training = [c for c in cases if c.anti_overfit.used_for_training]
    holdout = [c for c in cases if c.anti_overfit.used_for_holdout]
    overlap = [
        c for c in cases
        if c.anti_overfit.used_for_training and c.anti_overfit.used_for_holdout
    ]
    high_leakage = [c for c in cases if c.prediction_lock.leakage_risk == "high"]
    return {
        "n_cases": len(cases),
        "scored": by_status["scored"],
        "partial": by_status["partial"],
        "pending": by_status["pending"],
        "excluded": by_status["excluded"],
        "training": len(training),
        "holdout": len(holdout),
        "clean_holdout": sum(1 for c in cases if is_clean_holdout(c)),
        "train_holdout_overlap": len(overlap),
        "high_leakage_risk": len(high_leakage),
    }


def action_signal_coverage_summary(
    cases: Sequence[ValidationCase],
) -> dict[str, int]:
    """How many cases carry action_signals, and the total signal count."""
    with_signals = [c for c in cases if c.action_signals]
    return {
        "cases_with_action_signals": len(with_signals),
        "cases_without_action_signals": len(cases) - len(with_signals),
        "total_action_signals": sum(len(c.action_signals) for c in cases),
    }


def tier_coverage_summary(cases: Sequence[ValidationCase]) -> dict[str, int]:
    """Per-tier coverage: how many cases carry a Tier-N action signal, plus the
    total number of signals at each tier. Uses each signal's auto-filled tier."""
    case_tier = {1: 0, 2: 0, 3: 0, 4: 0}
    signals_by_tier = {1: 0, 2: 0, 3: 0, 4: 0}
    for c in cases:
        tiers = {s.tier for s in c.action_signals if s.tier is not None}
        for t in (1, 2, 3, 4):
            if t in tiers:
                case_tier[t] += 1
        for s in c.action_signals:
            if s.tier in signals_by_tier:
                signals_by_tier[s.tier] += 1
    return {
        "tier1_case_count": case_tier[1],
        "tier2_case_count": case_tier[2],
        "tier3_case_count": case_tier[3],
        "tier4_case_count": case_tier[4],
        "signals_tier1": signals_by_tier[1],
        "signals_tier2": signals_by_tier[2],
        "signals_tier3": signals_by_tier[3],
        "signals_tier4": signals_by_tier[4],
    }
