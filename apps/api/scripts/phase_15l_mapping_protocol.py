"""Phase 15L-B — Observed Outcome Mapping Protocol CLI.

Read-only / dry-run tooling for the mapping protocol: classify a candidate's
maximal-honest mapping type, emit a blank proposal template for a human to fill,
validate a proposed mapping against the hard gates, and report mapping-quality-
aware Phase 15E readiness. It NEVER ingests a case, approves a candidate, fills a
proportion, or applies calibration. The only write-capable command
(``mapping-template --out``) honors ``--dry-run``; every other command is
read-only.

Subcommands::

    classify           [--candidate-id ID]            # maximal-honest type (all if omitted)
    mapping-template   --candidate-id ID [--out FILE] [--dry-run]
    validate-mapping   --from PROPOSAL.json
    inspect-readiness  [--candidate-id ID]            # per-candidate classification + gate status
    dashboard          [--format text|json]           # mapping-readiness report

Exit codes: 0 = success / mapping OK, 1 = mapping REFUSED (gate failure),
2 = file/lookup error. NEVER calls an LLM, network, or DB.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from assembly.validation_factory.candidate_store import (
    DEFAULT_CANDIDATES_DIR,
    load_all_candidates,
    load_candidate,
)
from assembly.validation_factory.outcome_mapping_protocol import (
    OutcomeMappingProtocol,
    ProposedOutcomeMapping,
    classify_candidate,
    mapping_proposal_template,
    validate_mapping,
)
from assembly.validation_ledger.loader import load_all_cases


def _refuse(msg: str, issues: list[str] | None = None) -> int:
    print(f"REFUSED — {msg}", file=sys.stderr)
    for i in issues or []:
        print(f"  - {i}", file=sys.stderr)
    return 1


def cmd_classify(args: argparse.Namespace) -> int:
    if args.candidate_id:
        try:
            cands = [load_candidate(args.candidate_id, args.candidates_dir)]
        except FileNotFoundError:
            print(f"ERROR: candidate {args.candidate_id!r} not found", file=sys.stderr)
            return 2
    else:
        cands = sorted(load_all_candidates(args.candidates_dir), key=lambda c: c.candidate_id)
    for c in cands:
        mt, reasons = classify_candidate(c)
        print(f"{c.candidate_id}: {mt}")
        for r in reasons:
            print(f"    - {r}")
    return 0


def cmd_mapping_template(args: argparse.Namespace) -> int:
    try:
        cand = load_candidate(args.candidate_id, args.candidates_dir)
    except FileNotFoundError:
        print(f"ERROR: candidate {args.candidate_id!r} not found", file=sys.stderr)
        return 2
    template = mapping_proposal_template(cand)
    text = json.dumps(template, indent=2)
    if not args.out:
        print(text)
        return 0
    out = Path(args.out)
    if args.dry_run:
        print(f"DRY-RUN (no write): would write proposal template -> {out}")
        print(text)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"WROTE proposal template -> {out} (PROPOSAL ONLY — not approved/ingested)")
    return 0


def cmd_validate_mapping(args: argparse.Namespace) -> int:
    src = Path(args.src)
    if not src.exists():
        print(f"ERROR: proposal file not found: {src}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _refuse(f"could not parse {src.name}: {exc}")
    try:
        proposed = ProposedOutcomeMapping.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        return _refuse("proposal failed schema validation:", [str(exc)])
    cand = None
    try:
        cand = load_candidate(proposed.candidate_id, args.candidates_dir)
    except FileNotFoundError:
        print(
            f"NOTE: candidate {proposed.candidate_id!r} not found; "
            "validating proposal in isolation",
            file=sys.stderr,
        )
    result = validate_mapping(proposed, cand)
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    if not result.ok:
        return _refuse(
            f"proposed {result.mapping_type} mapping for {proposed.candidate_id!r} "
            "is BLOCKED by the protocol gates",
            result.issues,
        )
    print(
        f"OK — proposed {result.mapping_type} mapping passes the gates "
        "(PROPOSAL ONLY — still requires the factory reviewer_checklist + approval)"
    )
    return 0


def _classified(candidates_dir: str | None):
    cands = sorted(load_all_candidates(candidates_dir), key=lambda c: c.candidate_id)
    return [(c, classify_candidate(c)[0]) for c in cands]


def cmd_inspect_readiness(args: argparse.Namespace) -> int:
    proto = OutcomeMappingProtocol()
    if args.candidate_id:
        try:
            cand = load_candidate(args.candidate_id, args.candidates_dir)
        except FileNotFoundError:
            print(f"ERROR: candidate {args.candidate_id!r} not found", file=sys.stderr)
            return 2
        mt, reasons = classify_candidate(cand)
        print(f"{cand.candidate_id}: maximal-honest mapping type = {mt}")
        for r in reasons:
            print(f"    - {r}")
        print(
            "    promotable to training/holdout now? NO — "
            "claimed_outcome_proportions is null and a retrospective case cannot be "
            "a clean holdout; supply a gate-passing proposed mapping first."
        )
        return 0
    classifications = _classified(args.candidates_dir)
    report = proto.readiness(classifications, ledger_cases=load_all_cases())
    print(json.dumps(report, indent=2))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    proto = OutcomeMappingProtocol()
    classifications = _classified(args.candidates_dir)
    report = proto.readiness(classifications, ledger_cases=load_all_cases())
    if args.format == "json":
        print(json.dumps(report, indent=2))
        return 0
    print("=== Phase 15L-B — Observed Outcome Mapping readiness ===")
    print(f"protocol: {report['protocol_version']}")
    print(f"classified candidates: {report['n_classified']}")
    print(f"mapping_type_breakdown: {report['mapping_type_breakdown']}")
    print("--- distribution-quality counts ---")
    print(f"direct_observed: {report['n_direct_observed_distribution_cases']}  "
          f"assumption_labeled: {report['n_assumption_labeled_cases']}  "
          f"action_anchor_only: {report['n_action_anchor_only_cases']}  "
          f"evidence_only: {report['n_evidence_only_cases']}  "
          f"rejected: {report['n_rejected_cases']}")
    print("--- ledger readiness (real validation data) ---")
    print(f"ledger cases: {report['ledger_total_cases']}  "
          f"clean holdout: {report['ledger_clean_holdout']}  "
          f"Tier-1/2 outcome cases: {report['ledger_tier1_2_outcome_cases']}")
    print(f"DIRECT cases short of target ({report['readiness_target_case_count']}): "
          f"{report['direct_cases_short_of_target']}")
    print(f"assumption_labeled cap: {report['assumption_labeled_cap']}  "
          f"over_cap: {report['assumption_labeled_over_cap']}")
    if report["non_independent_entity_clusters"]:
        print(f"non-independent clusters: {report['non_independent_entity_clusters']}")
    if report["weak_mapping_warning"]:
        print("WEAK MAPPING WARNING:")
        for r in report["weak_mapping_warning_reasons"]:
            print(f"  - {r}")
    if report["phase_15e_blocked"]:
        print("Phase 15E: BLOCKED")
        for r in report["phase_15e_unmet_requirements"]:
            print(f"  - {r}")
    else:
        print("Phase 15E: numeric requirements met (still requires explicit human sign-off)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Phase 15L-B — Observed Outcome Mapping Protocol (read-only/dry-run)."
    )
    ap.add_argument(
        "--candidates-dir", default=None,
        help=f"candidate store dir (default {DEFAULT_CANDIDATES_DIR})",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("classify", help="maximal-honest mapping type for a candidate")
    p.add_argument("--candidate-id", default=None)
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("mapping-template", help="emit a blank proposal template")
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--out", default=None, help="write to FILE (honors --dry-run)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_mapping_template)

    p = sub.add_parser("validate-mapping", help="validate a proposed mapping JSON")
    p.add_argument("--from", dest="src", required=True)
    p.set_defaults(func=cmd_validate_mapping)

    p = sub.add_parser("inspect-readiness", help="per-candidate / overall readiness")
    p.add_argument("--candidate-id", default=None)
    p.set_defaults(func=cmd_inspect_readiness)

    p = sub.add_parser("dashboard", help="mapping-readiness report")
    p.add_argument("--format", default="text", choices=["text", "json"])
    p.set_defaults(func=cmd_dashboard)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
