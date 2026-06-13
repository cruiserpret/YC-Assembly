"""Phase 17D — create a historical case pack from a metadata JSON (dry-run by default).

Reads ONE JSON file describing the case (separate ``input_bundle`` + ``outcome_record``
+ ``candidate_metadata`` + ``provenance``), runs the source manifest, leakage audit,
hashes, and the 17C eligibility gate, classifies the pack, prints the report, and —
ONLY with ``--write`` — persists it under
``apps/api/benchmarks/market_fidelity/historical_case_packs/<status>/<case_id>/``.

NO model is run, downloaded, or called. The outcome record is never mixed into the
input bundle.

    python scripts/phase_17d_create_historical_case_pack.py --metadata path/to/case.json
    python scripts/phase_17d_create_historical_case_pack.py --metadata path/to/case.json --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assembly.benchmarks.market_fidelity.historical_cases import (
    CandidateMetadata,
    InputBundle,
    OutcomeRecord,
    ProvenanceInputs,
    build_case_pack,
    write_case_pack,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create a historical case pack (dry-run by default).")
    ap.add_argument("--metadata", required=True, help="path to the case metadata JSON")
    ap.add_argument("--packs-dir", default=None, help="override output dir (default: official)")
    ap.add_argument("--write", action="store_true", help="persist the pack (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit dry-run (default; never writes)")
    args = ap.parse_args(argv)

    p = Path(args.metadata)
    if not p.exists():
        print(f"ERROR: metadata not found: {p}", file=sys.stderr)
        return 2
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"REFUSED: metadata is not valid JSON: {e}", file=sys.stderr)
        return 1

    try:
        input_bundle = InputBundle.model_validate(meta["input_bundle"])
        outcome_record = OutcomeRecord.model_validate(meta["outcome_record"])
        candidate_metadata = CandidateMetadata.model_validate(meta["candidate_metadata"])
        provenance = ProvenanceInputs.model_validate(meta.get("provenance", {"subject": meta.get("product_name", "")}))
    except Exception as e:  # noqa: BLE001 - surface validation errors to the operator
        print(f"REFUSED: metadata failed schema validation: {e}", file=sys.stderr)
        return 1

    report = build_case_pack(
        input_bundle=input_bundle,
        outcome_record=outcome_record,
        candidate_metadata=candidate_metadata,
        provenance=provenance,
        product_name=meta.get("product_name", ""),
        company_or_creator=meta.get("company_or_creator", ""),
        geography=meta.get("geography", ""),
        flagged_outcome_values=meta.get("flagged_outcome_values"),
        notes=meta.get("notes", ""),
    )

    print(json.dumps({
        "case_id": report.pack.case_id,
        "case_status": report.pack.case_status,
        "case_classification": report.case_classification,
        "blindness_tier": report.pack.blindness_tier,
        "eligible_for_public_claim": report.pack.eligible_for_public_claim,
        "input_bundle_clean": report.leakage_audit["input_bundle_clean"],
        "excluded_sources": report.leakage_audit["excluded_sources"],
        "full_case_pack_hash": report.pack.full_case_pack_hash,
        "reasons": report.reasons,
    }, indent=2))

    will_write = args.write and not args.dry_run
    if will_write:
        try:
            out = write_case_pack(report, input_bundle, outcome_record, allow_write=True, packs_dir=args.packs_dir)
        except ValueError as e:
            print(f"\nREFUSED: {e}", file=sys.stderr)
            return 1
        print(f"\nWROTE: {out}", file=sys.stderr)
    else:
        print("\nDRY-RUN: nothing written (pass --write to persist).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
