"""Phase 16A-P — verify a prospective-lock provenance record (READ-ONLY).

Checks a compact provenance record under validation_cases/prospective_locks/
against pending_cases.json, and recomputes the prediction_hash from the record to
prove the lock is self-auditing from git alone. Writes nothing; never runs a
prediction, adds an outcome, or calibrates.

    cd apps/api && PYTHONPATH=src python scripts/phase_16a_verify_lock_provenance.py \
        --record validation_cases/prospective_locks/<run_id>.json

Exit codes: 0 = all checks pass, 1 = a check failed, 2 = file/lookup error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assembly.validation_ledger.prediction_lock import compute_prediction_hash

_CASES_DIR = Path(__file__).resolve().parent.parent / "validation_cases"
# Fields that would indicate an observed outcome smuggled into a lock record.
_FORBIDDEN_OUTCOME_FIELDS = (
    "observed_proportions",
    "observed_outcome",
    "observed_at",
    "final_pledged",
    "final_backers",
    "metrics",
    "mae_pp",
)
_BUCKETS = (
    "buyer_action_positive",
    "receptive",
    "uncertain_proof_needed",
    "skeptical_resistant",
)


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")


def verify(record: dict, pending: list[dict]) -> list[str]:
    """Return a list of failure messages (empty == all checks pass)."""
    fails: list[str] = []
    rid = record.get("run_id")
    case_id = record.get("pending_case_id")

    # the matching pending case
    case = next((c for c in pending if c.get("case_id") == case_id), None)
    if case is None:
        fails.append(f"run_id {rid!r} / case {case_id!r} not found in pending_cases.json")
        return fails  # nothing else verifiable

    pl = case.get("prediction_lock", {})
    ao = case.get("anti_overfit", {})

    if pl.get("run_id") != rid:
        fails.append(f"run_id mismatch: record {rid!r} vs pending case {pl.get('run_id')!r}")
    if record.get("prediction_hash") != pl.get("prediction_hash"):
        fails.append("prediction_hash does not match the pending case")
    if record.get("observed") is not None:
        fails.append("record 'observed' is not null")
    if "observed" in case:
        fails.append("pending case carries an observed outcome (must be absent)")
    if not (record.get("used_for_holdout") is True and ao.get("used_for_holdout") is True):
        fails.append("used_for_holdout must be true (record + pending case)")
    if record.get("used_for_training") is True or ao.get("used_for_training") is True:
        fails.append("used_for_training must be false (record + pending case)")
    if record.get("action_signals") or case.get("action_signals"):
        fails.append("action_signals must be empty (record + pending case)")
    present_forbidden = [f for f in _FORBIDDEN_OUTCOME_FIELDS if f in record]
    if present_forbidden:
        fails.append("record contains forbidden observed-outcome fields: " + ", ".join(present_forbidden))

    # self-auditing: recompute the prediction_hash from the record's own fields
    pred = record.get("predicted_proportions") or {}
    if all(k in pred for k in _BUCKETS):
        recomputed = compute_prediction_hash(
            run_id=str(rid),
            predicted={k: pred[k] for k in _BUCKETS},
            simulation_id=record.get("simulation_id_used_in_hash"),
            brief_hash=record.get("brief_hash"),
            evidence_snapshot_id=record.get("evidence_snapshot_id"),
            evidence_snapshot_hash=record.get("snapshot_hash"),
            locked_prediction_created_at=record.get("locked_at"),
            model_version={"report_schema_version": record.get("report_schema_version")},
        )
        if recomputed != record.get("prediction_hash"):
            fails.append(
                "prediction_hash is NOT reproducible from the record fields "
                f"(recomputed {recomputed})"
            )
    else:
        fails.append("predicted_proportions missing a canonical bucket")
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify a prospective-lock provenance record (read-only).")
    ap.add_argument("--record", required=True, help="path to a provenance JSON record")
    ap.add_argument(
        "--pending", default=str(_CASES_DIR / "pending_cases.json"),
        help="pending_cases.json (default: official)",
    )
    args = ap.parse_args(argv)

    rp = Path(args.record)
    if not rp.exists():
        print(f"ERROR: record not found: {rp}", file=sys.stderr)
        return 2
    record = json.loads(rp.read_text(encoding="utf-8"))
    if record.get("purpose") != "prospective_lock_provenance_not_observed_outcome":
        print("ERROR: not a prospective-lock provenance record (purpose marker missing)", file=sys.stderr)
        return 2
    pending = json.loads(Path(args.pending).read_text(encoding="utf-8"))

    fails = verify(record, pending)
    if fails:
        print(f"REFUSED — provenance record {rp.name} failed {len(fails)} check(s):", file=sys.stderr)
        for f in fails:
            _fail(f)
        return 1
    print(f"OK — {rp.name}: lock provenance verified (hash matches pending case + "
          "self-reproduces; observed=null; holdout/blind; no outcome fields).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
