"""Phase 17B — lock a benchmark baseline prediction (manual LLM output OR naive).

Validates a prediction against the AMFB-v1 schema, canonicalizes it, hashes the
frozen input bundle + the prediction, and prints an immutable lock record. DRY-RUN
by default (writes nothing); pass ``--write`` to persist under
``apps/api/benchmarks/market_fidelity/baseline_predictions/``.

NO live provider calls exist in Phase 17B: the prediction is either a NAIVE baseline
(``--naive``) or a manually-pasted/file LLM output (``--prediction-json``). Live
GPT/Claude/Gemini baseline locking arrives in Phase 17B-L behind an explicit flag +
cost gate + approval.

Examples:
    # naive baseline, dry-run (prints, writes nothing)
    python scripts/phase_17b_lock_baseline_prediction.py \
        --case-id tomo_endless_blue_onibi_ks_2026 \
        --method-id naive_uniform --method-class naive_baseline \
        --method-version v1 --input-bundle path/to/bundle.json --naive uniform_distribution

    # manual LLM output, persist
    python scripts/phase_17b_lock_baseline_prediction.py \
        --case-id tomo_endless_blue_onibi_ks_2026 \
        --method-id gpt_manual_baseline --method-class plain_llm \
        --method-version "manual_placeholder" --input-bundle path/to/bundle.json \
        --prediction-json path/to/prediction.json --write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    write_record,
)
from assembly.benchmarks.market_fidelity.hash_lock import (
    compute_prediction_hash,
    input_bundle_hash,
)
from assembly.benchmarks.market_fidelity.naive_baselines import naive_baseline
from assembly.benchmarks.market_fidelity.validators import (
    assert_mode_is_offline,
    check_no_post_lock_sources,
    validate_prediction_payload,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lock an AMFB-v1 benchmark baseline prediction (dry-run by default).")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--method-id", required=True)
    ap.add_argument("--method-class", required=True,
                    choices=["assembly", "plain_llm", "validation_tool", "survey_platform", "human_panel", "naive_baseline"])
    ap.add_argument("--method-version", required=True)
    ap.add_argument("--provider", default="")
    ap.add_argument("--input-bundle", required=True, help="path to the FROZEN shared input/evidence bundle JSON")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prediction-json", help="path to a manually-produced prediction JSON")
    src.add_argument("--naive", help="generate a naive baseline by id (e.g. uniform_distribution)")
    ap.add_argument("--cost-usd", type=float, default=0.0)
    ap.add_argument("--runtime-seconds", type=float, default=0.0)
    ap.add_argument("--locked-at", default=None, help="ISO-8601 UTC; default = now")
    ap.add_argument("--records-dir", default=None, help="override the output dir (default: official benchmark dir)")
    ap.add_argument("--write", action="store_true", help="persist the record (default: dry-run, writes nothing)")
    ap.add_argument("--dry-run", action="store_true", help="explicit dry-run (default behavior; never writes)")
    args = ap.parse_args(argv)

    bundle_path = Path(args.input_bundle)
    if not bundle_path.exists():
        print(f"ERROR: input bundle not found: {bundle_path}", file=sys.stderr)
        return 2
    input_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    # mode: naive vs manual_output (NEVER a live provider call in 17B)
    mode = "naive" if args.naive else "manual_output"
    try:
        assert_mode_is_offline(mode)
    except RuntimeError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    # get the prediction
    if args.naive:
        try:
            prediction = naive_baseline(args.naive, input_bundle)
        except KeyError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    else:
        pj = Path(args.prediction_json)
        if not pj.exists():
            print(f"ERROR: prediction JSON not found: {pj}", file=sys.stderr)
            return 2
        try:
            prediction = validate_prediction_payload(json.loads(pj.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001 - surface validation errors to the operator
            print(f"REFUSED: prediction failed schema validation: {e}", file=sys.stderr)
            return 1

    locked_at = args.locked_at or datetime.now(UTC).isoformat()

    # leakage guard (search-assisted sources must predate the lock instant)
    leak = check_no_post_lock_sources(input_bundle, locked_at)
    if leak:
        print("REFUSED: leakage detected:", file=sys.stderr)
        for i in leak:
            print(f"  - {i}", file=sys.stderr)
        return 1
    n_sources = len([s for s in (input_bundle.get("sources") or []) if isinstance(s, dict)])
    leakage_status = f"verified_clean_pre_outcome:{n_sources}_sources"

    payload = prediction.to_payload()
    ib_hash = input_bundle_hash(input_bundle)
    pred_hash = compute_prediction_hash(
        method_id=args.method_id,
        method_version=args.method_version,
        input_bundle_hash=ib_hash,
        prediction_payload=payload,
        locked_at=locked_at,
    )
    record = BaselinePredictionRecord(
        benchmark_case_id=args.case_id,
        method_class=args.method_class,
        method_id=args.method_id,
        method_version=args.method_version,
        provider=args.provider,
        input_bundle_hash=ib_hash,
        prediction_payload=payload,
        prediction_hash=pred_hash,
        locked_at=locked_at,
        cost_usd=args.cost_usd,
        runtime_seconds=args.runtime_seconds,
        mode=mode,
        leakage_status=leakage_status,
        schema_failure=prediction.schema_failure,
        notes=("naive baseline" if args.naive else "manual LLM output"),
    )

    will_write = args.write and not args.dry_run
    print(json.dumps(record.model_dump(mode="json"), indent=2))
    if will_write:
        try:
            out = write_record(record, allow_write=True, records_dir=args.records_dir)
        except ValueError as e:
            print(f"\nREFUSED: {e}", file=sys.stderr)
            return 1
        print(f"\nWROTE: {out}", file=sys.stderr)
    else:
        print("\nDRY-RUN: nothing written (pass --write to persist).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
