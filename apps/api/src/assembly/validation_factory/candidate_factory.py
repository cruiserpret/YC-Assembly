"""Phase 15J — candidate factory: dedup, promotion gates, candidate→case bridge.

Composes the EXISTING ledger + calibration public APIs (it imports them, never
re-derives them) to turn a reviewed candidate into a validation-ledger payload —
WITHOUT inventing any outcome, changing any forecast, or applying any
calibration. Deterministic; no LLM, no network, no DB.

The hard gates (all enforced by ``evaluate_promotion_gates``):
  1. required fields present + at least one source_url,
  2. reviewer checklist COMPLETE + an explicit evidence_tier,
  3. evidence-tier anti-masquerade + per-signal tier consistency + Tier-1/2
     citation/count (``evidence_grading``),
  4. no unresolved CRITICAL uncertainty flags,
  5. no duplicate (fingerprint + name/date/source) vs other candidates or the
     live ledger (block unless explicitly overridden),
  6. observed-outcome discipline: pending carries NO observed; training/holdout
     REQUIRE a reviewer-mapped four-bucket outcome,
  7. clean-holdout ANTI-LEAKAGE: a known outcome with no prediction locked
     before it can never be a clean holdout (reuses the ledger's own
     leakage/lock validators).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

from assembly.validation_factory.candidate_schema import (
    CandidateCase,
    PromotionTarget,
)
from assembly.validation_factory.evidence_grading import (
    tier_composition,
    validate_evidence_tier,
    validate_tier1_evidence,
    validate_tier_consistency,
)
from assembly.validation_ledger.ingest import (
    is_clean_holdout,
    validate_no_outcome_leakage,
    validate_prediction_lock,
)
from assembly.validation_ledger.schema import ValidationCase

CASE_FACTORY_VERSION = "case_factory.v1"

_REQUIRED_CANDIDATE_FIELDS = (
    "product_or_company_name",
    "category",
    "market_type",
    "launch_or_test_date",
    "candidate_summary",
    "observed_outcome_summary",
)

# An uncertainty flag is "critical" (and blocks promotion) if it starts with this.
_CRITICAL_FLAG_PREFIX = "critical:"


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_url(url: str) -> str:
    s = (url or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("?")[0].split("#")[0]
    return s.rstrip("/")


def _observed_fingerprint(candidate: CandidateCase) -> str:
    cp = candidate.claimed_outcome_proportions
    if cp is None:
        return ""
    b = cp.to_buckets()
    return ",".join(f"{round(float(b[k]), 4):.4f}" for k in sorted(b))


def candidate_fingerprint(candidate: CandidateCase) -> str:
    """Deterministic ``sha256:`` fingerprint from normalized name + date +
    category + source_type + primary url + observed-outcome hash."""
    primary_url = normalize_url(candidate.source_urls[0]) if candidate.source_urls else ""
    payload = {
        "name": normalize_name(candidate.product_or_company_name),
        "date": (candidate.launch_or_test_date or "").strip(),
        "category": (candidate.category or "").strip().lower(),
        "source_type": candidate.source_type,
        "primary_url": primary_url,
        "observed": _observed_fingerprint(candidate),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def name_date_source_key(name: str, date: str, source_type: str) -> tuple[str, str, str]:
    return (normalize_name(name), (date or "").strip(), str(source_type))


def find_duplicates(
    candidate: CandidateCase,
    *,
    existing_candidates: Sequence[CandidateCase] = (),
    existing_cases: Sequence[ValidationCase] = (),
) -> list[str]:
    """Block likely duplicates by exact fingerprint or by the (name, date,
    source) composite key — across both other candidates and the live ledger."""
    issues: list[str] = []
    fp = candidate_fingerprint(candidate)
    key = name_date_source_key(
        candidate.product_or_company_name,
        candidate.launch_or_test_date,
        candidate.source_type,
    )
    for other in existing_candidates:
        if other.candidate_id == candidate.candidate_id:
            continue
        if candidate_fingerprint(other) == fp:
            issues.append(f"duplicate of candidate {other.candidate_id!r} (same fingerprint)")
        elif name_date_source_key(
            other.product_or_company_name, other.launch_or_test_date, other.source_type
        ) == key:
            issues.append(
                f"likely duplicate of candidate {other.candidate_id!r} "
                "(same product_name + date + source_type)"
            )
    for case in existing_cases:
        if name_date_source_key(
            case.metadata.product_name, case.metadata.date_run, case.metadata.source_type
        ) == key:
            issues.append(
                f"likely duplicate of ledger case {case.case_id!r} "
                "(same product_name + date + source_type)"
            )
    return issues


# --------------------------------------------------------------------------
# Completion + gate checks
# --------------------------------------------------------------------------


def required_fields_present(candidate: CandidateCase) -> list[str]:
    issues: list[str] = []
    for f in _REQUIRED_CANDIDATE_FIELDS:
        v = getattr(candidate, f, None)
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().lower() == "unknown")):
            issues.append(f"required field {f!r} is missing / empty / 'unknown'")
    if not candidate.source_urls:
        issues.append("at least one source_url is required")
    return issues


def critical_uncertainty_flags(candidate: CandidateCase) -> list[str]:
    flags = list(candidate.uncertainty_flags)
    if candidate.reviewer_checklist:
        flags += list(candidate.reviewer_checklist.uncertainty_flags)
    return [f for f in flags if str(f).strip().lower().startswith(_CRITICAL_FLAG_PREFIX)]


def checklist_complete(candidate: CandidateCase) -> list[str]:
    issues: list[str] = []
    rc = candidate.reviewer_checklist
    if rc is None:
        return ["reviewer_checklist is required and is not present"]
    if not rc.is_complete():
        un = rc.unanswered()
        if un:
            issues.append("reviewer_checklist has unanswered questions: " + ", ".join(un))
        if rc.suitable_for == "undecided":
            issues.append("reviewer_checklist.suitable_for is 'undecided'")
        if rc.suitable_for != "reject" and rc.evidence_tier is None:
            issues.append("reviewer_checklist.evidence_tier must be assigned")
    return issues


def evaluate_promotion_gates(
    candidate: CandidateCase,
    target: PromotionTarget,
    *,
    existing_candidates: Sequence[CandidateCase] = (),
    existing_cases: Sequence[ValidationCase] = (),
    allow_duplicate: bool = False,
) -> list[str]:
    """Return BLOCKING issues preventing promotion of ``candidate`` to ``target``.
    Empty list == the candidate may be promoted. Mutates nothing."""
    issues: list[str] = []
    issues += required_fields_present(candidate)
    issues += checklist_complete(candidate)
    if candidate.evidence_tier is None:
        issues.append("evidence_tier must be assigned before promotion")
    issues += validate_evidence_tier(candidate)
    issues += validate_tier_consistency(candidate)
    issues += validate_tier1_evidence(candidate)

    crit = critical_uncertainty_flags(candidate)
    if crit:
        issues.append("unresolved critical uncertainty flags: " + ", ".join(crit))

    if not allow_duplicate:
        issues += find_duplicates(
            candidate,
            existing_candidates=existing_candidates,
            existing_cases=existing_cases,
        )

    rc = candidate.reviewer_checklist
    if rc is not None and rc.suitable_for not in ("undecided",):
        if rc.suitable_for == "reject":
            issues.append("reviewer_checklist marks this candidate suitable_for='reject'")
        elif rc.suitable_for != target:
            issues.append(
                f"reviewer recommends suitable_for={rc.suitable_for!r}, not {target!r}"
            )

    has_observed = candidate.claimed_outcome_proportions is not None
    if target == "pending":
        if has_observed:
            issues.append(
                "a pending case must NOT carry an observed outcome — pending means the "
                "outcome is not yet recorded (no fake or early observed value)"
            )
    elif not has_observed:
        issues.append(
            f"target {target!r} requires claimed_outcome_proportions (a reviewer-mapped "
            "four-bucket observed outcome)"
        )

    if target == "holdout":
        issues += _holdout_anti_leakage_issues(candidate)

    return issues


def _holdout_anti_leakage_issues(candidate: CandidateCase) -> list[str]:
    """A clean holdout needs a prediction locked BEFORE the observed outcome.
    Reuses the ledger's own validators on the would-be case."""
    try:
        case = ValidationCase.model_validate(
            build_case_payload_from_candidate(candidate, "holdout")
        )
    except Exception as exc:  # noqa: BLE001 — surface as a gate issue, not a crash
        return [f"holdout anti-leakage: payload could not be built: {exc}"]
    lock_issues = validate_prediction_lock(case)
    leak_issues = validate_no_outcome_leakage(case)
    issues = ["holdout anti-leakage: " + i for i in (lock_issues + leak_issues)]
    if not lock_issues and not leak_issues and not is_clean_holdout(case):
        issues.append(
            "holdout anti-leakage: not a clean holdout — a prediction must be locked "
            "BEFORE the observed outcome. A retrospective case with a known outcome "
            "belongs in training, or stage it as pending and lock an Assembly "
            "prediction first."
        )
    return issues


# --------------------------------------------------------------------------
# Candidate -> ValidationCase payload
# --------------------------------------------------------------------------


def build_case_payload_from_candidate(
    candidate: CandidateCase,
    target: PromotionTarget,
    *,
    case_id: str | None = None,
    locked_at: str | None = None,
) -> dict:
    """Build a ValidationCase payload (dict) from a candidate. Pure: invents no
    outcome (only the reviewer-mapped ``claimed_outcome_proportions`` is used, and
    only for training/holdout), attaches NO Assembly prediction (predicted=None)."""
    cid = case_id or f"cand_{candidate.candidate_id}"
    has_observed = candidate.claimed_outcome_proportions is not None and target != "pending"

    if target == "pending":
        status, used_training, used_holdout, leakage_risk = "pending", False, False, "unknown"
    elif target == "training":
        # observed present, predicted absent (no Assembly run yet) => 'partial'.
        status, used_training, used_holdout, leakage_risk = "partial", True, False, "high"
    else:  # holdout
        status, used_training, used_holdout, leakage_risk = "partial", False, True, "low"

    date_run = candidate.launch_or_test_date if candidate.launch_or_test_date != "unknown" else "unknown"

    metadata = {
        "product_name": candidate.product_or_company_name,
        "source_type": candidate.source_type,
        "product_category": candidate.category,
        "launch_stage": candidate.market_type or "unknown",
        "date_run": date_run,
        "validation_status": status,
        "confidence": "medium",
        "notes": (
            f"Phase 15J candidate factory ({CASE_FACTORY_VERSION}); "
            f"candidate_id={candidate.candidate_id}; evidence_tier={candidate.evidence_tier}; "
            f"target={target}. Observed outcome (if present) is externally sourced and "
            "human-reviewed; no Assembly prediction is attached (predicted=None)."
        ),
    }
    prediction_lock = {
        "leakage_risk": leakage_risk,
        "locked_prediction_created_at": locked_at,
        "clean_room_notes": (
            f"{CASE_FACTORY_VERSION}: promoted from candidate {candidate.candidate_id}. "
            "No Assembly prediction was locked (retrospective external case); the "
            "observed outcome was reviewer-mapped from cited sources."
        ),
    }
    anti_overfit = {
        "used_for_training": used_training,
        "used_for_holdout": used_holdout,
        "notes": f"Phase 15J factory promotion (target={target}); candidate {candidate.candidate_id}.",
    }
    payload: dict = {
        "case_id": cid,
        "metadata": metadata,
        "prediction_lock": prediction_lock,
        "anti_overfit": anti_overfit,
        "action_signals": [
            s.model_dump(mode="json", exclude_none=True)
            for s in candidate.action_signal_candidates
        ],
    }
    if has_observed:
        b = candidate.claimed_outcome_proportions.to_buckets()
        payload["observed"] = {
            **b,
            "denominator_type": "unknown",
            "observation_confidence": "medium",
            "observation_notes": (
                f"Phase 15J factory: externally-sourced observed outcome for candidate "
                f"{candidate.candidate_id}, reviewer-mapped from raw evidence."
            ),
        }
    # predicted is intentionally omitted (None): no Assembly run is attached here.
    return payload


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


def factory_dashboard(
    candidates: Sequence[CandidateCase],
    *,
    ledger_cases: Sequence[ValidationCase] = (),
    target_case_count: int = 20,
) -> dict:
    """A lightweight, read-only factory + readiness report. Counts only — emits no
    market distribution and changes nothing."""
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_evidence_tier: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0, "unassigned": 0}
    signal_tier_totals = {"tier1": 0, "tier2": 0, "tier3": 0, "tier4": 0, "unclassified": 0}
    for c in candidates:
        by_status[c.status] = by_status.get(c.status, 0) + 1
        by_category[c.category] = by_category.get(c.category, 0) + 1
        by_source[c.source_type] = by_source.get(c.source_type, 0) + 1
        key = str(int(c.evidence_tier)) if c.evidence_tier is not None else "unassigned"
        by_evidence_tier[key] = by_evidence_tier.get(key, 0) + 1
        comp = tier_composition(c)
        for k in signal_tier_totals:
            signal_tier_totals[k] += comp[k]

    approved_for = {
        t: by_status.get(f"approved_for_{t}", 0) for t in ("pending", "training", "holdout")
    }

    # Phase 15E readiness is judged on the LIVE LEDGER (real validation data),
    # NOT on candidates: it needs >=target cases, >=1 clean holdout, and Tier-1/2
    # action outcomes.
    ledger = list(ledger_cases)
    clean_holdout = sum(1 for c in ledger if is_clean_holdout(c))
    tier1_2_outcome_cases = 0
    for c in ledger:
        tiers = {s.tier for s in c.action_signals if s.tier is not None}
        if tiers & {1, 2}:
            tier1_2_outcome_cases += 1
    ledger_total = len(ledger)
    unmet = []
    if ledger_total < target_case_count:
        unmet.append(f"need >={target_case_count} ledger cases (have {ledger_total})")
    if clean_holdout < 1:
        unmet.append("need >=1 clean holdout case (have 0)")
    if tier1_2_outcome_cases < 1:
        unmet.append("need >=1 case with Tier-1/Tier-2 action outcomes (have 0)")
    phase_15e_blocked = bool(unmet)

    return {
        "n_candidates": len(candidates),
        "by_status": by_status,
        "approved_for": approved_for,
        "by_category": by_category,
        "by_source_type": by_source,
        "by_evidence_tier": by_evidence_tier,
        "candidate_signal_tier_totals": signal_tier_totals,
        "ledger_total_cases": ledger_total,
        "ledger_clean_holdout": clean_holdout,
        "ledger_tier1_2_outcome_cases": tier1_2_outcome_cases,
        "readiness_target_case_count": target_case_count,
        "phase_15e_blocked": phase_15e_blocked,
        "phase_15e_unmet_requirements": unmet,
    }
