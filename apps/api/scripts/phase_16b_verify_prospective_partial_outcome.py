"""Phase 16B-R — verify a prospective PARTIAL outcome record (READ-ONLY).

Checks a partial-outcome record under validation_cases/prospective_outcomes/ against
pending_cases.json: that it records a buyer/action ANCHOR ONLY (never a fabricated
four-bucket observed distribution), that the matching case is 'partial' with observed
still null and the buyer anchor in action_signals, that the locked prediction_hash is
unchanged AND still self-reproduces (from the immutable prospective_locks/ record),
that this is NOT a direct_observed_distribution and does NOT unlock Phase 15E, and
that other prospective locks (e.g. Tomo) stay blind/untouched.

Writes nothing; never runs a prediction, adds an outcome, or calibrates.

    cd apps/api && PYTHONPATH=src python scripts/phase_16b_verify_prospective_partial_outcome.py \
        --record validation_cases/prospective_outcomes/<run_id>.json

Exit codes: 0 = all checks pass, 1 = a check failed, 2 = file/lookup error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assembly.validation_ledger.prediction_lock import compute_prediction_hash

_CASES_DIR = Path(__file__).resolve().parent.parent / "validation_cases"
_LOCKS_DIR = _CASES_DIR / "prospective_locks"
_OUTCOMES_DIR = _CASES_DIR / "prospective_outcomes"
_MANIFEST = _CASES_DIR / "manifest.json"

PURPOSE = "prospective_partial_outcome_not_observed_distribution"
_SUPPORTED_MAPPING_TYPES = ("action_anchor_only",)
_NON_BUYER_BUCKETS = ("receptive", "uncertain_proof_needed", "skeptical_resistant")
_BUCKETS = ("buyer_action_positive", *_NON_BUYER_BUCKETS)


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")


def _find_fabricated_distribution(obj: object, *, allow_keys: tuple[str, ...]) -> bool:
    """Recursively walk a JSON value and return True if ANY nested dict carries the
    non-buyer OBSERVED buckets (a fabricated split). The allow-listed keys (the
    PREDICTION: ``locked_distribution`` on a record, ``predicted`` on a case) are
    skipped — they legitimately hold four buckets. This is structural (not
    key-name-/depth-limited), so a fabrication under any off-list or nested key is
    still caught."""
    if isinstance(obj, dict):
        if any(b in obj for b in _NON_BUYER_BUCKETS):
            return True
        return any(
            _find_fabricated_distribution(v, allow_keys=allow_keys)
            for k, v in obj.items()
            if k not in allow_keys
        )
    if isinstance(obj, list):
        return any(_find_fabricated_distribution(v, allow_keys=allow_keys) for v in obj)
    return False


def verify(record: dict, pending: list[dict], *, locks_dir: Path = _LOCKS_DIR) -> list[str]:
    """Return a list of failure messages (empty == all checks pass)."""
    fails: list[str] = []

    if record.get("purpose") != PURPOSE:
        fails.append(f"purpose marker must be {PURPOSE!r}")
    if record.get("mapping_type") not in _SUPPORTED_MAPPING_TYPES:
        fails.append(
            f"mapping_type must be one of {_SUPPORTED_MAPPING_TYPES} "
            f"(got {record.get('mapping_type')!r}); a direct_observed_distribution must go "
            "through the Phase 15L-C gated path with a full distribution, not this partial path"
        )
    if record.get("scoring_type") != "partial_buyer_anchor":
        fails.append("scoring_type must be 'partial_buyer_anchor'")

    rid = record.get("run_id")
    case_id = record.get("pending_case_id")
    case = next((c for c in pending if c.get("case_id") == case_id), None)
    if case is None:
        fails.append(f"pending_case_id {case_id!r} not found in pending_cases.json")
        return fails  # nothing else verifiable

    meta = case.get("metadata", {})
    pl = case.get("prediction_lock", {})

    # --- case state: partial, observed null, buyer anchor present ---
    if meta.get("validation_status") != "partial":
        fails.append(
            f"case validation_status must be 'partial' (got {meta.get('validation_status')!r})"
        )
    if "observed" in case and case.get("observed") is not None:
        fails.append("case must keep observed null/absent (no fabricated four-bucket distribution)")
    signals = case.get("action_signals") or []
    if not signals:
        fails.append("case must carry the buyer/action anchor in action_signals (none present)")
    else:
        anchor = signals[0]
        if not anchor.get("signal_type"):
            fails.append("the action signal must record a signal_type")
        if anchor.get("direction") not in ("positive", "negative"):
            fails.append("the action signal must record a buyer direction (positive/negative)")

    # --- lock unchanged + self-reproduces ---
    if pl.get("run_id") != rid:
        fails.append(f"run_id mismatch: record {rid!r} vs case {pl.get('run_id')!r}")
    if record.get("locked_prediction_hash") != pl.get("prediction_hash"):
        fails.append("locked_prediction_hash does not match the case prediction_lock (lock changed?)")
    lock_path = locks_dir / f"run_{rid}.json"
    if not lock_path.exists():
        fails.append(f"immutable lock-provenance record not found: {lock_path.name}")
    else:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        pred = lock.get("predicted_proportions") or {}
        if all(k in pred for k in _BUCKETS):
            recomputed = compute_prediction_hash(
                run_id=str(rid),
                predicted={k: pred[k] for k in _BUCKETS},
                simulation_id=lock.get("simulation_id_used_in_hash"),
                brief_hash=lock.get("brief_hash"),
                evidence_snapshot_id=lock.get("evidence_snapshot_id"),
                evidence_snapshot_hash=lock.get("snapshot_hash"),
                locked_prediction_created_at=lock.get("locked_at"),
                model_version={"report_schema_version": lock.get("report_schema_version")},
            )
            if recomputed != pl.get("prediction_hash"):
                fails.append(
                    "prediction_hash no longer self-reproduces from the immutable lock record "
                    f"(recomputed {recomputed})"
                )
        else:
            fails.append("lock-provenance record missing a canonical predicted bucket")

    # --- NO fabricated observed four-bucket data anywhere (structural, not key-name/
    # depth-limited). observed must be STRICTLY null; the only dict allowed to carry
    # four buckets is the PREDICTION (locked_distribution on the record, predicted on
    # the case). ---
    if record.get("observed") is not None:
        fails.append("record 'observed' must be null")
    if record.get("observed_remains_null") is not True:
        fails.append("record must assert observed_remains_null=true")
    for k in ("observed_proportions", "observed_distribution", "observed_buckets"):
        if k in record:
            fails.append(f"record carries a forbidden observed-distribution key: {k!r}")
    if _find_fabricated_distribution(record, allow_keys=("locked_distribution",)):
        fails.append(
            "record carries a fabricated observed four-bucket distribution (non-buyer "
            "buckets present outside the allow-listed locked_distribution)"
        )
    if _find_fabricated_distribution(case, allow_keys=("predicted",)):
        fails.append(
            "case carries a fabricated observed four-bucket distribution (non-buyer "
            "buckets present outside the allow-listed predicted)"
        )
    if record.get("not_direct_observed_distribution") is not True:
        fails.append("record must assert not_direct_observed_distribution=true")

    # --- isolation: prospective_outcomes must be absent from the manifest ---
    if _MANIFEST.exists():
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        paths = {f["path"] if isinstance(f, dict) else f for f in manifest.get("files", [])}
        if any("prospective_outcome" in str(p) for p in paths):
            fails.append("prospective_outcomes must be ABSENT from manifest.json (never loaded)")

    # --- every OTHER lock stays blind/untouched, REGARDLESS of status. A lock may be
    # non-blind (partial, carrying an action anchor) ONLY if it has its own approved
    # prospective_outcomes record; otherwise observed must be null AND action_signals
    # empty. This catches a tampered lock (e.g. Tomo) flipped to 'partial'. ---
    for c in pending:
        cid = c.get("case_id")
        if cid == case_id:
            continue
        has_outcome_record = bool(cid) and (_OUTCOMES_DIR / f"{cid}.json").exists()
        observed_present = c.get("observed") is not None
        signals_present = bool(c.get("action_signals") or [])
        if observed_present:
            fails.append(f"another lock {cid!r} carries an observed outcome (must stay null)")
        if not has_outcome_record and signals_present:
            fails.append(
                f"another lock {cid!r} carries action_signals but has no approved "
                "prospective_outcomes record — it must remain blind/untouched"
            )

    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify a prospective partial-outcome record (read-only).")
    ap.add_argument("--record", required=True, help="path to a prospective_outcomes JSON record")
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
    if record.get("purpose") != PURPOSE:
        print("ERROR: not a prospective partial-outcome record (purpose marker missing)", file=sys.stderr)
        return 2
    pending = json.loads(Path(args.pending).read_text(encoding="utf-8"))

    fails = verify(record, pending)
    if fails:
        print(f"REFUSED — partial-outcome record {rp.name} failed {len(fails)} check(s):", file=sys.stderr)
        for f in fails:
            _fail(f)
        return 1
    print(f"OK — {rp.name}: partial outcome verified (case 'partial'; observed=null; buyer anchor "
          "recorded; lock unchanged + self-reproduces; not direct_observed; Phase 15E still blocked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
