"""Phase 17B-L — LLM baseline lock PREFLIGHT for a frozen input bundle (Tomo).

Prepares everything needed to later lock paid GPT/Claude/Gemini baselines against the
SAME frozen input bundle Assembly used — WITHOUT calling any provider. It:
  * loads the frozen bundle and recomputes its input_bundle_hash,
  * builds the single strict baseline prompt + its prompt_hash,
  * runs the leakage guard (sources predate the lock) + a prompt-cleanliness check,
  * shows, per provider, the point-in-time model id (RE-VERIFY at run time), the API
    structured-output mode, and the would-be output record path,
  * evaluates the explicit live-call gate.

DEFAULT = PREPARED_NOT_RUN: with no approval it prints the exact command to lock later
and exits without any paid call. Even WITH approval it does not spend here — this
isolated package imports no SDK; the paid executor is a separate, gate-guarded step.

    # preflight (writes nothing, calls nothing)
    PYTHONPATH=src .venv/bin/python scripts/phase_17b_l_baseline_preflight.py \
        --input-bundle benchmarks/market_fidelity/prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json \
        --providers openai anthropic google --locked-at 2026-06-04T03:23:13.481724+00:00
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assembly.benchmarks.market_fidelity.baseline_prompt import (
    assert_prompt_is_clean,
    build_baseline_prompt,
    prompt_hash,
)
from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    record_filename,
)
from assembly.benchmarks.market_fidelity.hash_lock import input_bundle_hash
from assembly.benchmarks.market_fidelity.live_call_gate import gate_from_env
from assembly.benchmarks.market_fidelity.providers import PROVIDER_STUBS
from assembly.benchmarks.market_fidelity.validators import check_no_post_lock_sources

_METHOD_ID = {"openai": "gpt_raw_baseline", "anthropic": "claude_raw_baseline", "google": "gemini_raw_baseline"}


def _candidate_lock_paths(bundle_path: Path, lock_rel: str) -> list[Path]:
    """Best-effort resolutions of an 'apps/api/...'-style lock-record path from either the
    repo root or apps/api (cwd) or the bundle's apps/api ancestor."""
    stripped = lock_rel.removeprefix("apps/api/")
    out = [Path(lock_rel), Path(stripped)]
    for parent in bundle_path.resolve().parents:
        if parent.name == "api" and parent.parent.name == "apps":
            out.append(parent / stripped)
            break
    return out


def _forbidden_values(bundle_path: Path) -> list[str]:
    """Assembly's prediction hash (+ hex) and locked proportion digits, pulled from the
    sibling provenance.json (+ the referenced lock record), so the prompt-cleanliness gate
    can catch a numeric/hash leak. Best-effort; never displays these values."""
    prov_path = bundle_path.with_name("provenance.json")
    if not prov_path.exists():
        return []
    try:
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    vals: list[str] = []
    ph = prov.get("assembly_prediction_hash")
    if isinstance(ph, str) and ph:
        vals += [ph, ph.split(":", 1)[-1]]
    lock_rel = prov.get("original_lock_record_path")
    if isinstance(lock_rel, str) and lock_rel:
        for cand in _candidate_lock_paths(bundle_path, lock_rel):
            if cand.exists():
                try:
                    pp = (json.loads(cand.read_text(encoding="utf-8")).get("predicted_proportions") or {})
                    for v in pp.values():
                        s = str(v)
                        if float(v) != 0.0 and len(s) >= 4:  # skip 0.0 / trivial values (false-positive prone)
                            vals.append(s)
                except (ValueError, OSError, TypeError, AttributeError):
                    pass
                break
    return [v for v in vals if v]


def _would_be_record_path(case_id: str, method_id: str) -> str:
    # mirror baseline_records.record_filename without a real prediction hash yet
    safe_case = "".join(c if c.isalnum() or c in "-_" else "_" for c in case_id)
    safe_method = "".join(c if c.isalnum() or c in "-_" else "_" for c in method_id)
    return f"benchmarks/market_fidelity/baseline_predictions/{safe_case}__{safe_method}__<prediction_hash[:12]>.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 17B-L LLM baseline preflight (PREPARED_NOT_RUN by default).")
    ap.add_argument("--input-bundle", required=True, help="path to the frozen shared input bundle JSON")
    ap.add_argument("--providers", nargs="+", default=["openai", "anthropic", "google"],
                    choices=["openai", "anthropic", "google"])
    ap.add_argument("--locked-at", default=None, help="ISO-8601 UTC lock instant for the leakage check")
    ap.add_argument("--max-total-usd", type=float, default=None, help="global cost cap (required to approve)")
    ap.add_argument("--max-per-provider-usd", type=float, default=None, help="per-provider cost cap (required to approve)")
    ap.add_argument("--i-understand-this-costs-real-money", dest="cli_approval", action="store_true",
                    help="explicit CLI approval (still also needs cost caps)")
    args = ap.parse_args(argv)

    bundle_path = Path(args.input_bundle)
    if not bundle_path.exists():
        print(f"ERROR: input bundle not found: {bundle_path}", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"ERROR: input bundle is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(bundle, dict):
        print("ERROR: input bundle must be a JSON object", file=sys.stderr)
        return 2
    case_id = bundle.get("benchmark_case_id", "<unknown>")

    ib_hash = input_bundle_hash(bundle)
    # fail-closed: the recomputed hash must match the committed input_bundle_hash.txt, so a
    # silently-edited bundle (different frozen input) can never be locked against.
    committed_path = bundle_path.with_name("input_bundle_hash.txt")
    committed_hash = committed_path.read_text(encoding="utf-8").strip() if committed_path.exists() else None
    hash_drift = committed_hash is not None and committed_hash != ib_hash

    prompt = build_baseline_prompt(bundle)
    p_hash = prompt_hash(prompt)

    # leakage + prompt-cleanliness (fail-closed); forbidden_values catch an Assembly numeric/hash leak
    locked_at = args.locked_at or bundle.get("frozen_at") or ""
    leak = check_no_post_lock_sources(bundle, locked_at) if locked_at else ["no locked_at to check leakage against"]
    prompt_issues = assert_prompt_is_clean(prompt, forbidden_values=_forbidden_values(bundle_path))

    print("=== Phase 17B-L baseline preflight ===")
    print(json.dumps({
        "benchmark_case_id": case_id,
        "input_bundle_hash": ib_hash,
        "prompt_hash": p_hash,
        "locked_at_for_leakage_check": locked_at,
        "leakage_guard": "CLEAN" if not leak else leak,
        "prompt_cleanliness": "CLEAN" if not prompt_issues else prompt_issues,
        "input_bundle_hash_matches_committed": (committed_hash is None) or (not hash_drift),
        "providers": [
            {
                "provider": p,
                "method_id": _METHOD_ID[p],
                "method_class": "plain_llm",
                "point_in_time_model_id": PROVIDER_STUBS[p].default_model_id,
                "RE_VERIFY_AT_RUN_TIME": True,
                "structured_output_method": PROVIDER_STUBS[p].structured_output_method,
                "would_be_output_path": _would_be_record_path(case_id, _METHOD_ID[p]),
            }
            for p in args.providers
        ],
    }, indent=2))

    if leak or prompt_issues or hash_drift:
        if hash_drift:
            print(f"\nREFUSED: input_bundle_hash {ib_hash} != committed {committed_hash} — the frozen "
                  "bundle was edited; all methods must lock against the SAME committed hash.", file=sys.stderr)
        print("REFUSED: bundle/prompt failed the pre-lock safety checks above — fix before any lock.",
              file=sys.stderr)
        return 1

    gate = gate_from_env(
        providers_requested=args.providers,
        global_cost_cap_usd=args.max_total_usd,
        per_provider_cost_cap_usd=args.max_per_provider_usd,
        cli_approval=args.cli_approval,
    )
    print("\n=== live-call gate ===")
    print(json.dumps(gate.model_dump(mode="json"), indent=2))

    if not gate.approved:
        print("\nPREPARED_NOT_RUN: no paid provider call was made.", file=sys.stderr)
        print("To lock paid LLM baselines later (before 2026-06-21), re-run WITH approval + caps:", file=sys.stderr)
        print(
            "  ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true PYTHONPATH=src .venv/bin/python "
            "scripts/phase_17b_l_baseline_preflight.py \\\n"
            f"    --input-bundle {args.input_bundle} --providers {' '.join(args.providers)} \\\n"
            "    --max-total-usd 6 --max-per-provider-usd 2 --i-understand-this-costs-real-money",
            file=sys.stderr,
        )
        return 0

    # Gate APPROVED — but this isolated package never spends. The paid executor is a
    # separate, deliberately-unwired step. We stop here without any call.
    print("\nGATE APPROVED — but the paid executor is intentionally NOT wired in this phase "
          "(this package imports no SDK). No call made. Build the gate-guarded executor to spend.",
          file=sys.stderr)
    # Silence unused-import lint for the record helpers kept for the future executor.
    _ = (BaselinePredictionRecord, record_filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
