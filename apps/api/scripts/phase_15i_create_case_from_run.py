"""Phase 15I — create a validation-ledger PENDING case from a completed run.

Reads a completed run's artifacts (founder_report.json + evidence_snapshot.json
under the durable artifact root), extracts the locked four-bucket prediction,
computes a deterministic prediction_hash, and appends a ``pending`` validation
case to the ledger's pending split file — WITHOUT inventing any observed
outcome.

    cd apps/api && python scripts/phase_15i_create_case_from_run.py \
        --run-id <run-id> [--source-type product_hunt] [--product-category dev_tools] \
        [--case-id ...] [--output validation_cases/pending_cases.json] [--allow-partial]

Deterministic and side-effect-light: reads run artifacts only (no DB), NEVER
calls an LLM or network, NEVER marks the case scored, NEVER marks it training,
and NEVER writes an observed outcome.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from assembly.validation_ledger.ingest import (
    append_case_to_ledger,
    validate_no_outcome_leakage,
    validate_prediction_lock,
)
from assembly.validation_ledger.run_to_case import (
    RunArtifactsMissingError,
    RunPredictionUnusableError,
    build_pending_case_from_run,
)

_CASES_DIR = Path(__file__).resolve().parent.parent / "validation_cases"
_DEFAULT_OUTPUT = _CASES_DIR / "pending_cases.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Create a pending validation case from a completed run (Phase 15I)."
    )
    ap.add_argument("--run-id", required=True, help="completed run id")
    ap.add_argument(
        "--output", default=None,
        help=f"target ledger file (default {_DEFAULT_OUTPUT.name})",
    )
    ap.add_argument("--source-type", default="unknown", help="SourceType (default unknown)")
    ap.add_argument("--product-category", default="unknown")
    ap.add_argument("--product-name", default=None, help="override (default: from report)")
    ap.add_argument("--launch-stage", default="unknown")
    ap.add_argument("--case-id", default=None, help="override (default: run_<run-id>)")
    ap.add_argument("--locked-at", default=None, help="ISO lock timestamp override")
    ap.add_argument("--date-run", default=None)
    ap.add_argument("--leakage-risk", default="low", choices=["low", "medium", "high", "unknown"])
    ap.add_argument("--run-dir", default=None, help="override the run artifact dir")
    ap.add_argument(
        "--allow-partial", action="store_true",
        help="store a flagged partial skeleton if artifacts/prediction are missing",
    )
    ap.add_argument(
        "--print-only", action="store_true",
        help="print the built case and do NOT append it to the ledger",
    )
    args = ap.parse_args(argv)

    try:
        case, warnings = build_pending_case_from_run(
            args.run_id,
            source_type=args.source_type,
            product_category=args.product_category,
            product_name=args.product_name,
            launch_stage=args.launch_stage,
            case_id=args.case_id,
            locked_at=args.locked_at,
            date_run=args.date_run,
            leakage_risk=args.leakage_risk,
            allow_partial=args.allow_partial,
            run_dir=args.run_dir,
        )
    except (RunArtifactsMissingError, RunPredictionUnusableError) as exc:
        print(f"REFUSED — {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print("REFUSED — built case failed schema validation:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    issues = validate_prediction_lock(case) + validate_no_outcome_leakage(case)
    if issues and not args.allow_partial:
        print(f"REFUSED — case {case.case_id!r} has lock/leakage issues:", file=sys.stderr)
        for i in issues:
            print(f"  - {i}", file=sys.stderr)
        print("(re-run with --allow-partial to store it anyway, flagged)", file=sys.stderr)
        return 1

    # Safety invariant: this path must never produce an observed outcome.
    assert case.observed is None, "Phase 15I must never write an observed outcome"

    if args.print_only:
        print(json.dumps(case.model_dump(mode="json", exclude_none=True), indent=2))
        return 0

    target = Path(args.output) if args.output else _DEFAULT_OUTPUT
    try:
        append_case_to_ledger(case, target)
    except ValueError as exc:
        print(f"REFUSED — {exc}", file=sys.stderr)
        return 1

    print(f"ADDED pending case {case.case_id!r} -> {target.name}")
    print(
        f"  status={case.metadata.validation_status} "
        f"source={case.metadata.source_type} category={case.metadata.product_category}"
    )
    pl = case.prediction_lock
    print(
        f"  run_id={pl.run_id} prediction_hash={pl.prediction_hash} "
        f"locked_at={pl.locked_prediction_created_at} leakage_risk={pl.leakage_risk}"
    )
    print(
        f"  holdout={case.anti_overfit.used_for_holdout} "
        f"training={case.anti_overfit.used_for_training} observed={case.observed}"
    )
    if warnings:
        print("  warnings:")
        for w in warnings:
            print(f"     - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
