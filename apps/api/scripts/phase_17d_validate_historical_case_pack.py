"""Phase 17D — validate a historical case pack (READ-ONLY).

Loads a pack metadata JSON (same shape the create CLI consumes, OR a written pack dir's
three artifacts) and re-checks consistency + leakage-freedom: case-id match, hash
reproduction, input/outcome SEPARATION, accepted-pack cleanliness, and the public-claim
tier rule. Writes nothing; no model calls.

    python scripts/phase_17d_validate_historical_case_pack.py --metadata path/to/case.json
    python scripts/phase_17d_validate_historical_case_pack.py --pack-dir path/to/<case_id>/
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
    validate_case_pack,
)
from assembly.benchmarks.market_fidelity.historical_cases.case_pack_schema import HistoricalCasePack


def _from_metadata(meta: dict):
    ib = InputBundle.model_validate(meta["input_bundle"])
    orr = OutcomeRecord.model_validate(meta["outcome_record"])
    cm = CandidateMetadata.model_validate(meta["candidate_metadata"])
    prov = ProvenanceInputs.model_validate(meta.get("provenance", {"subject": meta.get("product_name", "")}))
    report = build_case_pack(
        input_bundle=ib, outcome_record=orr, candidate_metadata=cm, provenance=prov,
        product_name=meta.get("product_name", ""), flagged_outcome_values=meta.get("flagged_outcome_values"),
    )
    return ib, orr, report.pack, meta.get("flagged_outcome_values")


def _from_dir(d: Path):
    ib = InputBundle.model_validate(json.loads((d / "input_bundle.json").read_text()))
    orr = OutcomeRecord.model_validate(json.loads((d / "outcome_record.json").read_text()))
    pack = HistoricalCasePack.model_validate(json.loads((d / "case_pack.json").read_text()))
    return ib, orr, pack, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a historical case pack (read-only).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--metadata", help="case metadata JSON")
    src.add_argument("--pack-dir", help="a written pack directory")
    args = ap.parse_args(argv)

    try:
        if args.metadata:
            ib, orr, pack, flagged = _from_metadata(json.loads(Path(args.metadata).read_text(encoding="utf-8")))
        else:
            ib, orr, pack, flagged = _from_dir(Path(args.pack_dir))
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not load/parse pack: {e}", file=sys.stderr)
        return 2

    issues = validate_case_pack(input_bundle=ib, outcome_record=orr, pack=pack, flagged_outcome_values=flagged)
    if issues:
        print(f"REFUSED — pack {pack.case_id} failed {len(issues)} check(s):", file=sys.stderr)
        for i in issues:
            print(f"  - {i}", file=sys.stderr)
        return 1
    print(f"OK — {pack.case_id}: valid (case_id consistent; hashes reproduce; input/outcome separated; "
          f"status={pack.case_status}; tier={pack.blindness_tier}; "
          f"public_claim={pack.eligible_for_public_claim}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
