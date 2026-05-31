"""Phase 15G — validate + append a single validation case from a JSON payload.

Deterministic, interactive-free. Reads a payload file, validates it against the
ledger schema, runs the prediction-lock + leakage checks, and (only if clean,
or --allow-partial) appends it to a split ledger file.

    cd apps/api && python scripts/phase_15g_add_validation_case.py \
        path/to/payload.json [--to validation_cases/holdout_cases.json] [--allow-partial]

NEVER calls an LLM, network, or DB. Adds no invented data — it only ingests the
payload you provide.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from assembly.validation_ledger.ingest import (
    append_case_to_ledger,
    build_validation_case_from_payload,
    validate_no_outcome_leakage,
    validate_prediction_lock,
)

_CASES_DIR = Path(__file__).resolve().parent.parent / "validation_cases"


def _default_target(status: str) -> Path:
    # Pending cases (no observed outcome yet) go to pending; everything else
    # defaults to the holdout file (new scored cases should be BLIND holdout).
    return _CASES_DIR / ("pending_cases.json" if status == "pending" else "holdout_cases.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Add a validation case (Phase 15G).")
    ap.add_argument("payload", help="path to a JSON case payload")
    ap.add_argument("--to", default=None, help="target ledger file (default by status)")
    ap.add_argument(
        "--allow-partial", action="store_true",
        help="store the case even if it has lock/leakage issues (flagged)",
    )
    args = ap.parse_args(argv)

    payload_path = Path(args.payload)
    if not payload_path.exists():
        print(f"ERROR: payload not found: {payload_path}", file=sys.stderr)
        return 2
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    try:
        case = build_validation_case_from_payload(payload)
    except ValidationError as exc:
        print("REFUSED — payload failed schema validation:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    issues = validate_prediction_lock(case) + validate_no_outcome_leakage(case)
    if issues and not args.allow_partial:
        print(f"REFUSED — case {case.case_id!r} has lock/leakage issues:", file=sys.stderr)
        for i in issues:
            print(f"  - {i}", file=sys.stderr)
        print("(re-run with --allow-partial to store it anyway, flagged)", file=sys.stderr)
        return 1

    target = Path(args.to) if args.to else _default_target(case.metadata.validation_status)
    try:
        append_case_to_ledger(case, target)
    except ValueError as exc:
        print(f"REFUSED — {exc}", file=sys.stderr)
        return 1

    print(f"ADDED case {case.case_id!r} -> {target.name}")
    print(f"  status={case.metadata.validation_status} source={case.metadata.source_type} "
          f"category={case.metadata.product_category}")
    print(f"  training={case.anti_overfit.used_for_training} holdout={case.anti_overfit.used_for_holdout} "
          f"leakage_risk={case.prediction_lock.leakage_risk} action_signals={len(case.action_signals)}")
    if issues:
        print("  ⚠ stored WITH issues (--allow-partial):")
        for i in issues:
            print(f"     - {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
