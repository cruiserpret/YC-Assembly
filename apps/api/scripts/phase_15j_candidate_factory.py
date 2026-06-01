"""Phase 15J — Validation Case Factory CLI.

A repeatable, auditable workflow for turning REAL external market-outcome leads
into validation-ledger cases — WITHOUT inventing data, changing forecasts, or
applying calibration. Every write command supports ``--dry-run`` (no filesystem
change). Candidates live under ``validation_cases/candidates/`` and are NEVER
loaded as validation cases.

Subcommands::

    create       --from <case.json|.yaml|.md> [--id ID] [--dry-run]
    validate     (--id ID | --from <file>)
    show         --id ID
    needs-review --id ID [--dry-run]
    reject       --id ID --reason TEXT [--dry-run]
    approve      --id ID --target {pending,training,holdout} [--allow-duplicate] [--dry-run]
    ingest       --id ID [--to <ledger.json>] [--allow-duplicate] [--locked-at TS] [--dry-run]
    dashboard    [--format text|json]

Exit codes: 0 = success, 1 = REFUSED (validation / gate failure), 2 = file/lookup error.
NEVER calls an LLM, network, or DB.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from assembly.validation_factory.candidate_factory import (
    build_case_payload_from_candidate,
    evaluate_promotion_gates,
    factory_dashboard,
)
from assembly.validation_factory.candidate_schema import CandidateCase
from assembly.validation_factory.candidate_store import (
    DEFAULT_CANDIDATES_DIR,
    load_all_candidates,
    load_candidate,
    save_candidate,
)
from assembly.validation_ledger.ingest import (
    append_case_to_ledger,
    build_validation_case_from_payload,
    validate_no_outcome_leakage,
    validate_prediction_lock,
)
from assembly.validation_ledger.loader import load_all_cases
from assembly.validation_ledger.schema import ValidationCase

_CASES_DIR = Path(__file__).resolve().parent.parent / "validation_cases"
_TARGET_FILE = {
    "pending": _CASES_DIR / "pending_cases.json",
    "training": _CASES_DIR / "training_cases.json",
    "holdout": _CASES_DIR / "holdout_cases.json",
}
_STATUS_TARGET = {
    "approved_for_pending": "pending",
    "approved_for_training": "training",
    "approved_for_holdout": "holdout",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _refuse(msg: str, issues: list[str] | None = None) -> int:
    print(f"REFUSED — {msg}", file=sys.stderr)
    for i in issues or []:
        print(f"  - {i}", file=sys.stderr)
    return 1


def _load_payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    suf = path.suffix.lower()
    if suf in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise SystemExit("YAML input requires pyyaml (pip install pyyaml)") from exc
        return yaml.safe_load(text)
    if suf in (".md", ".markdown"):
        m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
        if not m:
            raise ValueError("a markdown candidate must embed a ```json ... ``` block")
        return json.loads(m.group(1))
    return json.loads(text)  # .json (or anything else: try JSON)


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def cmd_create(args: argparse.Namespace) -> int:
    src = Path(args.src)
    if not src.exists():
        print(f"ERROR: candidate file not found: {src}", file=sys.stderr)
        return 2
    try:
        payload = _load_payload(src)
    except Exception as exc:  # noqa: BLE001
        return _refuse(f"could not parse {src.name}: {exc}")
    if args.id:
        payload["candidate_id"] = args.id
    payload.setdefault("created_at", args.created_at or _now_iso())
    payload["updated_at"] = args.created_at or _now_iso()
    try:
        cand = CandidateCase.model_validate(payload)
    except ValidationError as exc:
        return _refuse("candidate failed schema validation:", [str(exc)])

    existing = {c.candidate_id for c in load_all_candidates(args.candidates_dir)}
    if cand.candidate_id in existing and not args.overwrite:
        return _refuse(
            f"candidate {cand.candidate_id!r} already exists "
            "(use --overwrite to replace, or choose a new --id)"
        )
    path = save_candidate(cand, args.candidates_dir, dry_run=args.dry_run)
    verb = "DRY-RUN (no write):" if args.dry_run else "CREATED candidate"
    print(f"{verb} {cand.candidate_id!r} -> {path}")
    print(f"  status={cand.status} source={cand.source_type} category={cand.category}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if args.src:
        src = Path(args.src)
        if not src.exists():
            print(f"ERROR: candidate file not found: {src}", file=sys.stderr)
            return 2
        try:
            CandidateCase.model_validate(_load_payload(src))
        except (ValidationError, ValueError) as exc:
            return _refuse("candidate failed schema validation:", [str(exc)])
        print(f"OK — {src.name} is a schema-valid candidate")
        return 0
    try:
        load_candidate(args.id, args.candidates_dir)
    except FileNotFoundError:
        print(f"ERROR: candidate {args.id!r} not found", file=sys.stderr)
        return 2
    except (ValidationError, ValueError) as exc:
        return _refuse("candidate failed schema validation:", [str(exc)])
    print(f"OK — candidate {args.id!r} is schema-valid")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        cand = load_candidate(args.id, args.candidates_dir)
    except FileNotFoundError:
        print(f"ERROR: candidate {args.id!r} not found", file=sys.stderr)
        return 2
    print(json.dumps(cand.model_dump(mode="json", exclude_none=True), indent=2))
    return 0


def _transition(args: argparse.Namespace, status: str, reason: str | None = None) -> int:
    try:
        cand = load_candidate(args.id, args.candidates_dir)
    except FileNotFoundError:
        print(f"ERROR: candidate {args.id!r} not found", file=sys.stderr)
        return 2
    update: dict = {"status": status, "updated_at": _now_iso()}
    if reason is not None:
        update["rejection_reason"] = reason
    try:
        new = cand.model_copy(update=update)
        CandidateCase.model_validate(new.model_dump())  # re-validate invariants
    except ValidationError as exc:
        return _refuse("transition produced an invalid candidate:", [str(exc)])
    path = save_candidate(new, args.candidates_dir, dry_run=args.dry_run)
    verb = "DRY-RUN (no write):" if args.dry_run else "UPDATED"
    print(f"{verb} {cand.candidate_id!r} status={status} -> {path}")
    return 0


def cmd_needs_review(args: argparse.Namespace) -> int:
    return _transition(args, "needs_review")


def cmd_reject(args: argparse.Namespace) -> int:
    return _transition(args, "rejected", reason=args.reason)


def _gate_context(args: argparse.Namespace) -> tuple[list, list]:
    others = [c for c in load_all_candidates(args.candidates_dir)]
    return others, load_all_cases()


def cmd_approve(args: argparse.Namespace) -> int:
    try:
        cand = load_candidate(args.id, args.candidates_dir)
    except FileNotFoundError:
        print(f"ERROR: candidate {args.id!r} not found", file=sys.stderr)
        return 2
    if cand.status == "rejected":
        return _refuse(f"candidate {args.id!r} is rejected and cannot be approved")
    others, cases = _gate_context(args)
    others = [c for c in others if c.candidate_id != cand.candidate_id]
    issues = evaluate_promotion_gates(
        cand, args.target,
        existing_candidates=others, existing_cases=cases,
        allow_duplicate=args.allow_duplicate,
    )
    if issues:
        return _refuse(
            f"candidate {args.id!r} cannot be approved for {args.target!r}:", issues
        )
    new = cand.model_copy(update={
        "status": f"approved_for_{args.target}", "updated_at": _now_iso()
    })
    path = save_candidate(new, args.candidates_dir, dry_run=args.dry_run)
    verb = "DRY-RUN (no write):" if args.dry_run else "APPROVED"
    print(f"{verb} {cand.candidate_id!r} -> status=approved_for_{args.target} ({path})")
    print("  gates: all clear")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    try:
        cand = load_candidate(args.id, args.candidates_dir)
    except FileNotFoundError:
        print(f"ERROR: candidate {args.id!r} not found", file=sys.stderr)
        return 2
    target = _STATUS_TARGET.get(cand.status)
    if target is None:
        return _refuse(
            f"candidate {args.id!r} has status {cand.status!r}; ingest requires an "
            "approved_for_{pending,training,holdout} status (run 'approve' first)"
        )
    others, cases = _gate_context(args)
    others = [c for c in others if c.candidate_id != cand.candidate_id]
    issues = evaluate_promotion_gates(
        cand, target,
        existing_candidates=others, existing_cases=cases,
        allow_duplicate=args.allow_duplicate,
    )
    if issues:
        return _refuse(f"candidate {args.id!r} failed the promotion gates:", issues)

    payload = build_case_payload_from_candidate(
        cand, target, case_id=args.case_id, locked_at=args.locked_at
    )
    try:
        case: ValidationCase = build_validation_case_from_payload(payload)
    except ValidationError as exc:
        return _refuse("built case failed schema validation:", [str(exc)])

    # Belt-and-suspenders: holdout must remain clean (no leakage bypass).
    if target == "holdout":
        lock_leak = validate_prediction_lock(case) + validate_no_outcome_leakage(case)
        if lock_leak:
            return _refuse("holdout case has lock/leakage issues:", lock_leak)

    target_file = Path(args.to) if args.to else _TARGET_FILE[target]
    if args.dry_run:
        print(f"DRY-RUN (no write): would append case {case.case_id!r} -> {target_file.name}")
        print(json.dumps(case.model_dump(mode="json", exclude_none=True), indent=2))
        return 0
    try:
        append_case_to_ledger(case, target_file)
    except ValueError as exc:
        return _refuse(str(exc))
    print(f"INGESTED case {case.case_id!r} -> {target_file.name}")
    print(f"  status={case.metadata.validation_status} target={target} "
          f"training={case.anti_overfit.used_for_training} holdout={case.anti_overfit.used_for_holdout} "
          f"observed={case.observed is not None} predicted={case.predicted is not None}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    candidates = load_all_candidates(args.candidates_dir)
    cases = load_all_cases()
    board = factory_dashboard(candidates, ledger_cases=cases, target_case_count=args.target_count)
    if args.format == "json":
        print(json.dumps(board, indent=2))
        return 0
    print("=== Phase 15J Validation Case Factory — dashboard ===")
    print(f"candidates: {board['n_candidates']}  by_status={board['by_status']}")
    print(f"approved_for: {board['approved_for']}")
    print(f"by_evidence_tier: {board['by_evidence_tier']}")
    print(f"by_category: {board['by_category']}")
    print(f"by_source_type: {board['by_source_type']}")
    print(f"candidate signal tiers: {board['candidate_signal_tier_totals']}")
    print("--- ledger readiness (real validation data) ---")
    print(f"ledger cases: {board['ledger_total_cases']} / target {board['readiness_target_case_count']}")
    print(f"clean holdout: {board['ledger_clean_holdout']}   "
          f"Tier-1/2 outcome cases: {board['ledger_tier1_2_outcome_cases']}")
    if board["phase_15e_blocked"]:
        print("Phase 15E: BLOCKED")
        for r in board["phase_15e_unmet_requirements"]:
            print(f"  - {r}")
    else:
        print("Phase 15E: requirements met (still requires explicit sign-off)")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Phase 15J — Validation Case Factory.")
    ap.add_argument(
        "--candidates-dir", default=None,
        help=f"candidate store dir (default {DEFAULT_CANDIDATES_DIR})",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="create a candidate from JSON/YAML/Markdown")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--id", default=None, help="override candidate_id")
    p.add_argument("--created-at", default=None, help="ISO timestamp override")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("validate", help="schema-validate a candidate (no write)")
    p.add_argument("--id", default=None)
    p.add_argument("--from", dest="src", default=None)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("show", help="print a candidate")
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("needs-review", help="mark a candidate needs_review")
    p.add_argument("--id", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_needs_review)

    p = sub.add_parser("reject", help="mark a candidate rejected")
    p.add_argument("--id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("approve", help="run gates + approve a candidate for a target")
    p.add_argument("--id", required=True)
    p.add_argument("--target", required=True, choices=["pending", "training", "holdout"])
    p.add_argument("--allow-duplicate", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("ingest", help="promote an approved candidate into the ledger")
    p.add_argument("--id", required=True)
    p.add_argument("--to", default=None, help="override target ledger file")
    p.add_argument("--case-id", default=None)
    p.add_argument("--locked-at", default=None, help="ISO lock timestamp (prospective only)")
    p.add_argument("--allow-duplicate", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("dashboard", help="factory + readiness report")
    p.add_argument("--format", default="text", choices=["text", "json"])
    p.add_argument("--target-count", type=int, default=20)
    p.set_defaults(func=cmd_dashboard)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
