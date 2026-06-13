"""Phase 17B-L2 — approval-gated LIVE GPT/Claude/Gemini Tomo baseline executor.

Locks ONE raw baseline prediction from each of GPT / Claude / Gemini against the EXACT
frozen Tomo input bundle (input_bundle_hash sha256:f29e8a46…), with NO web/search, NO
current-campaign data, immutable self-verifying records, and strict cost caps.

FAIL-CLOSED. The default is PREPARED_NOT_RUN — it makes NO provider call unless EVERY one
of these holds: env ``ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true`` AND
``--i-understand-this-costs-real-money`` AND ``--max-total-usd`` AND ``--max-per-provider-usd``
AND ``--confirm-input-bundle-hash`` == the bundle's actual hash AND the REAL current date is
BEFORE the Tomo outcome window (2026-06-21) AND the records dir is the benchmark
baseline-predictions dir AND the relevant provider API key is present.

The cost cap is enforced by WORST-CASE RESERVATION (these SDKs do not return a reliable
per-call cost): each called provider reserves the full ``--max-per-provider-usd`` against
``--max-total-usd``, so at most floor(max_total / max_per_provider) providers ever run. To
run all three providers, set ``--max-total-usd`` >= 3 × ``--max-per-provider-usd``
(suggested: ``--max-total-usd 6 --max-per-provider-usd 2``).

Provider SDKs are imported LAZILY inside each adapter (never at module import), no API key
is read at import, and NO search/tool/grounding is enabled on any call. The pre-outcome date
gate uses the REAL clock (not an operator flag) and cannot be spoofed. Adapters are
INJECTABLE so tests use fakes — no real provider call ever happens in tests. This script is
NOT imported by Assembly runtime.

    # preflight (default): writes nothing, calls nothing
    PYTHONPATH=src .venv/bin/python scripts/phase_17b_l2_lock_live_llm_tomo_baselines.py \
        --input-bundle benchmarks/market_fidelity/prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from assembly.benchmarks.market_fidelity.baseline_prompt import (
    assert_prompt_is_clean,
    build_baseline_prompt,
    prompt_hash,
)
from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    default_records_dir,
    write_record,
)
from assembly.benchmarks.market_fidelity.hash_lock import compute_prediction_hash, input_bundle_hash
from assembly.benchmarks.market_fidelity.live_call_gate import (
    APPROVAL_ENV_VAR,
    evaluate_live_call_gate,
)
from assembly.benchmarks.market_fidelity.schema import validate_prediction
from assembly.benchmarks.market_fidelity.validators import check_no_post_lock_sources

EXPECTED_INPUT_BUNDLE_HASH = "sha256:f29e8a46e0a677e0985e606f643e49fbc63822402d3dbf2c0570be5be2dd5d01"
TOMO_OUTCOME_DATE = "2026-06-21"  # do NOT lock a pre-outcome baseline on/after this date

# provider -> static descriptor. point-in-time model id is a HINT; the adapter records the
# EXACT model id returned at run time when the SDK echoes one. model names are not permanent truth.
PROVIDERS: dict[str, dict] = {
    "openai": {"method_id": "gpt_raw_baseline", "key_env": "OPENAI_API_KEY",
               "model_hint": "gpt-5.5", "schema_mode": "response_format=json_schema(strict)"},
    "anthropic": {"method_id": "claude_raw_baseline", "key_env": "ANTHROPIC_API_KEY",
                  "model_hint": "claude-opus-4-8", "schema_mode": "forced tool_use (emit_forecast)"},
    "google": {"method_id": "gemini_raw_baseline", "key_env": "GOOGLE_API_KEY",
               "model_hint": "gemini-3.5-flash", "schema_mode": "responseSchema (no grounding)"},
}


# --------------------------------------------------------------------------- adapters
# Each adapter: (prompt, model_hint, api_key, timeout_s) -> dict with raw_text, model_id,
# model_id_verified (did the SDK echo a resolved id?), cost_usd|None, runtime_s. SDKs are
# imported LAZILY here; NO tools/search/grounding argument is ever passed.
def _openai_adapter(prompt: str, model_hint: str, api_key: str, timeout_s: float) -> dict:
    import openai  # lazy — never imported unless a real, approved call runs
    client = openai.OpenAI(api_key=api_key, timeout=timeout_s)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model_hint, temperature=0.2, max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],  # NO tools / NO web / NO grounding
    )
    rid = getattr(resp, "model", None)
    return {"raw_text": resp.choices[0].message.content or "", "model_id": rid or model_hint,
            "model_id_verified": rid is not None, "cost_usd": None, "runtime_s": time.monotonic() - t0}


def _anthropic_adapter(prompt: str, model_hint: str, api_key: str, timeout_s: float) -> dict:
    import anthropic  # lazy
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
    t0 = time.monotonic()
    resp = client.messages.create(
        model=model_hint, max_tokens=1500, temperature=0.2,
        messages=[{"role": "user", "content": prompt}],  # NO tools / NO web
    )
    rid = getattr(resp, "model", None)
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return {"raw_text": text, "model_id": rid or model_hint, "model_id_verified": rid is not None,
            "cost_usd": None, "runtime_s": time.monotonic() - t0}


def _google_adapter(prompt: str, model_hint: str, api_key: str, timeout_s: float) -> dict:
    import google.generativeai as genai  # lazy
    genai.configure(api_key=api_key)  # process-global; the executor process exits after the run
    model = genai.GenerativeModel(model_hint)
    t0 = time.monotonic()
    resp = model.generate_content(prompt)  # NO tools / NO search grounding
    rid = getattr(resp, "model_version", None)  # SDK may not echo a resolved id
    return {"raw_text": getattr(resp, "text", "") or "", "model_id": rid or model_hint,
            "model_id_verified": rid is not None, "cost_usd": None, "runtime_s": time.monotonic() - t0}


DEFAULT_ADAPTERS = {"openai": _openai_adapter, "anthropic": _anthropic_adapter, "google": _google_adapter}


# ----------------------------------------------------------------------- safety helpers
def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _forbidden_values(bundle_path: Path) -> list[str]:
    """Assembly hash (+ hex) + locked proportions from the sibling provenance.json / lock
    record, so the prompt-cleanliness gate catches a numeric/hash leak. Never displayed.

    FAIL-CLOSED: a provenance.json that EXISTS but cannot be parsed raises ValueError (the
    caller turns that into a hard block) — the leak-guard inputs must never be silently
    disabled. A legitimately absent provenance returns []."""
    prov_path = bundle_path.with_name("provenance.json")
    if not prov_path.exists():
        return []
    try:
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise ValueError(f"provenance.json present but unreadable — cannot build the leak guard: {e}") from e
    vals: list[str] = []
    ph = prov.get("assembly_prediction_hash")
    if isinstance(ph, str) and ph:
        vals += [ph, ph.split(":", 1)[-1]]
    lock_rel = prov.get("original_lock_record_path")
    if isinstance(lock_rel, str) and lock_rel:
        stripped = lock_rel.removeprefix("apps/api/")
        cands = [Path(lock_rel), Path(stripped)]
        for parent in bundle_path.resolve().parents:
            if parent.name == "api" and parent.parent.name == "apps":
                cands.append(parent / stripped)
                break
        for cand in cands:
            if cand.exists():
                try:
                    pp = json.loads(cand.read_text(encoding="utf-8")).get("predicted_proportions") or {}
                    for v in pp.values():
                        for form in _proportion_forms(v):
                            vals.append(form)
                except (ValueError, OSError, TypeError, AttributeError):
                    pass
                break
    return [v for v in vals if v]


def _proportion_forms(v: object) -> list[str]:
    """Distinctive string forms of a proportion to scan for (raw, trimmed, 2-dp) — excludes
    trivially-short / zero values that would false-positive on a substring match."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return []
    forms = {str(v), f"{f:.4f}".rstrip("0").rstrip("."), f"{f:.2f}"}
    return [s for s in forms if len(s) >= 4 and float(s) != 0.0]


def _parse_prediction(raw_text: str):
    """Extract the JSON object and validate it against AMFB-v1. Returns
    (BenchmarkPrediction, None) on success or (None, reason) on schema failure."""
    text = (raw_text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "no JSON object found in the model output"
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError as e:
        return None, f"output is not valid JSON: {e}"
    try:
        return validate_prediction(payload), None
    except Exception as e:  # noqa: BLE001 — schema failure is an expected, recorded outcome
        return None, f"output did not conform to AMFB-v1: {e}"


# ----------------------------------------------------------------------------- main
def main(argv: list[str] | None = None, *, adapters: dict | None = None,
         env: dict | None = None, now_real: date | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 17B-L2 live LLM Tomo baseline lock (PREPARED_NOT_RUN by default).")
    ap.add_argument("--input-bundle", required=True)
    ap.add_argument("--providers", nargs="+", default=["openai", "anthropic", "google"],
                    choices=["openai", "anthropic", "google"])
    ap.add_argument("--max-total-usd", type=float, default=None)
    ap.add_argument("--max-per-provider-usd", type=float, default=None)
    ap.add_argument("--confirm-input-bundle-hash", default=None)
    ap.add_argument("--i-understand-this-costs-real-money", dest="cli_approval", action="store_true")
    ap.add_argument("--locked-at", default=None)
    ap.add_argument("--timeout-s", type=float, default=60.0)
    ap.add_argument("--records-dir", default=None)
    args = ap.parse_args(argv)

    environ = env if env is not None else os.environ
    adapters = adapters if adapters is not None else DEFAULT_ADAPTERS
    # The pre-outcome gate uses the REAL clock; ``now_real`` is a NON-CLI test injection only
    # (no operator-facing flag can spoof it forward past the outcome window).
    today = now_real if now_real is not None else datetime.now(UTC).date()

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
    prompt = build_baseline_prompt(bundle)
    p_hash = prompt_hash(prompt)
    locked_at = args.locked_at or bundle.get("frozen_at") or today.isoformat()

    # --- hard safety pre-checks (any failure => STOP, never call) ---
    leak = check_no_post_lock_sources(bundle, locked_at) if locked_at else ["no locked_at for leakage check"]
    leak_guard_broken = False
    try:
        forbidden = _forbidden_values(bundle_path)
    except ValueError:
        forbidden, leak_guard_broken = [], True
    prompt_issues = assert_prompt_is_clean(prompt, forbidden_values=forbidden)
    expected_hash_drift = ib_hash != EXPECTED_INPUT_BUNDLE_HASH

    outcome = date.fromisoformat(TOMO_OUTCOME_DATE)
    after_outcome = today >= outcome

    records_dir = Path(args.records_dir) if args.records_dir else default_records_dir()
    dir_ok = records_dir.resolve().parts[-3:] == ("benchmarks", "market_fidelity", "baseline_predictions")

    gate = evaluate_live_call_gate(
        approval_flag_present=bool(args.cli_approval) and _env_truthy(environ.get(APPROVAL_ENV_VAR)),
        providers_requested=args.providers,
        global_cost_cap_usd=args.max_total_usd,
        per_provider_cost_cap_usd=args.max_per_provider_usd,
        confirmed_input_bundle_hash=args.confirm_input_bundle_hash,
        actual_input_bundle_hash=ib_hash,
    )
    confirm_hash_wrong = args.confirm_input_bundle_hash is not None and args.confirm_input_bundle_hash != ib_hash

    blockers: list[str] = list(gate.blocking_conditions)
    if leak:
        blockers.append(f"leakage in bundle: {leak}")
    if leak_guard_broken:
        blockers.append("leak guard could not load Assembly secrets (provenance.json unreadable) — fail-closed")
    if prompt_issues:
        blockers.append(f"prompt leakage: {prompt_issues}")
    if expected_hash_drift:
        blockers.append(f"bundle hash {ib_hash} != expected {EXPECTED_INPUT_BUNDLE_HASH}")
    if after_outcome:
        blockers.append(f"REAL date {today} is on/after the Tomo outcome window {TOMO_OUTCOME_DATE} — pre-outcome lock not allowed")
    if not dir_ok:
        blockers.append(f"records dir {records_dir} is not the benchmark .../market_fidelity/baseline_predictions dir")

    print("=== Phase 17B-L2 live baseline executor ===")
    print(json.dumps({
        "benchmark_case_id": case_id, "input_bundle_hash": ib_hash, "prompt_hash": p_hash,
        "expected_hash_match": not expected_hash_drift, "leakage_guard": "CLEAN" if not leak else leak,
        "prompt_cleanliness": "CLEAN" if not prompt_issues else prompt_issues,
        "leak_guard_loaded": not leak_guard_broken, "real_date": today.isoformat(),
        "before_outcome_window": not after_outcome, "records_dir_ok": dir_ok,
        "gate_approved": gate.approved, "gate_blocking": gate.blocking_conditions,
        "providers": args.providers, "max_total_usd": args.max_total_usd,
        "max_per_provider_usd": args.max_per_provider_usd,
        "api_key_present": {p: bool(environ.get(PROVIDERS[p]["key_env"])) for p in args.providers},
    }, indent=2))

    # Hard safety failures (hash drift / leakage / post-outcome / bad dir) are a BLOCK (rc=1).
    # Missing approval/caps is the expected PREPARED_NOT_RUN (rc=0). Either way: no call happens.
    if blockers:
        hard = (expected_hash_drift or confirm_hash_wrong or bool(leak) or leak_guard_broken
                or bool(prompt_issues) or after_outcome or not dir_ok)
        label = "BLOCKED" if hard else "PREPARED_NOT_RUN"
        print(f"\n{label}: no provider call was made. Reasons:", file=sys.stderr)
        for b in blockers:
            print(f"  - {b}", file=sys.stderr)
        if not hard:
            print("\nTo lock live baselines (before 2026-06-21), provide ALL of: "
                  f"{APPROVAL_ENV_VAR}=true, --i-understand-this-costs-real-money, --max-total-usd, "
                  "--max-per-provider-usd, --confirm-input-bundle-hash <the bundle hash> "
                  "(use --max-total-usd >= 3x --max-per-provider-usd to run all three).", file=sys.stderr)
        return 1 if hard else 0

    # --- APPROVED: lock one live baseline per provider, bounded by WORST-CASE reservation ---
    print("\nAPPROVED — locking live baselines (one call per provider, cost-capped by reservation):", file=sys.stderr)
    reserved = 0.0
    results = []
    for provider in args.providers:
        desc = PROVIDERS[provider]
        api_key = environ.get(desc["key_env"])
        if not api_key:
            results.append({"provider": provider, "status": "blocked_missing_api_key"})
            continue
        # reserve the FULL per-provider cap against the global cap BEFORE calling (adapter cost
        # is unreliable, so we never let an unknown cost become a free call for budgeting).
        if reserved + args.max_per_provider_usd > args.max_total_usd + 1e-9:
            results.append({"provider": provider, "status": "blocked_by_cost_cap"})
            continue
        reserved += args.max_per_provider_usd
        results.append(_run_one_provider(
            provider, desc, prompt, api_key, args.timeout_s, adapters,
            case_id=case_id, ib_hash=ib_hash, locked_at=locked_at, records_dir=records_dir,
        ))

    print("\n=== results ===")
    print(json.dumps({
        "reserved_usd_total": round(reserved, 6), "max_total_usd": args.max_total_usd,
        "measured_cost_note": "these SDKs do not return per-call cost; the cap is enforced by worst-case "
                              "per-provider reservation, and record.cost_usd is 0.0 with cost_reporting_status "
                              "in notes when unavailable",
        "results": results,
    }, indent=2))
    return 0


def _run_one_provider(provider, desc, prompt, api_key, timeout_s, adapters, *,
                      case_id, ib_hash, locked_at, records_dir) -> dict:
    """One bounded call (≤2 attempts: 1 call + 1 retry on schema failure). Writes an
    immutable record. A refusal / schema failure is RECORDED, not retried endlessly."""
    adapter = adapters[provider]
    raw = None
    last_reason = ""
    for _attempt in range(2):
        try:
            raw = adapter(prompt, desc["model_hint"], api_key, timeout_s)
        except Exception as e:  # noqa: BLE001 — provider/transport error -> record schema_failure
            last_reason = f"provider call error: {e}"
            raw = None
            continue
        pred, reason = _parse_prediction(raw.get("raw_text", ""))
        if pred is not None:
            return _write_live_record(provider, desc, pred, raw, schema_failure=False, reason="",
                                      model_id_verified=bool(raw.get("model_id_verified")),
                                      case_id=case_id, ib_hash=ib_hash, locked_at=locked_at,
                                      records_dir=records_dir)
        last_reason = reason
    # both attempts failed -> honest schema_failure record (no fabricated buckets)
    model_id = (raw or {}).get("model_id", desc["model_hint"])
    verified = bool((raw or {}).get("model_id_verified"))
    sf_pred = validate_prediction({"confidence": 0.0, "schema_failure": True,
                                   "schema_failure_reason": last_reason or "no valid prediction"})
    fail_raw = {"model_id": model_id, "cost_usd": (raw or {}).get("cost_usd"),
                "runtime_s": (raw or {}).get("runtime_s", 0.0)}
    return _write_live_record(provider, desc, sf_pred, fail_raw, schema_failure=True, reason=last_reason,
                              model_id_verified=verified, case_id=case_id, ib_hash=ib_hash,
                              locked_at=locked_at, records_dir=records_dir)


def _write_live_record(provider, desc, pred, raw, *, schema_failure, reason, model_id_verified,
                       case_id, ib_hash, locked_at, records_dir) -> dict:
    payload = pred.to_payload()
    model_id = raw.get("model_id") or desc["model_hint"]
    cost = raw.get("cost_usd")
    cost_status = "exact" if cost is not None else "unavailable_sdk_no_cost"
    pred_hash = compute_prediction_hash(
        method_id=desc["method_id"], method_version=model_id, input_bundle_hash=ib_hash,
        prediction_payload=payload, locked_at=locked_at,
    )
    record = BaselinePredictionRecord(
        benchmark_case_id=case_id, method_class="plain_llm", method_id=desc["method_id"],
        method_version=model_id, provider=provider, input_bundle_hash=ib_hash,
        prediction_payload=payload, prediction_hash=pred_hash, locked_at=locked_at,
        cost_usd=float(cost) if cost is not None else 0.0, runtime_seconds=float(raw.get("runtime_s", 0.0)),
        mode="live_provider_call", leakage_status="clean_pre_outcome", schema_failure=schema_failure,
        notes=json.dumps({"schema_mode": desc["schema_mode"], "cost_reporting_status": cost_status,
                          "schema_failure_reason": reason, "model_id": model_id,
                          "model_id_source": "response" if model_id_verified else "hint_unverified"}),
    )
    try:
        out = write_record(record, allow_write=True, records_dir=records_dir)
    except ValueError:  # record already exists — records are immutable, never overwrite
        return {"provider": provider, "status": "blocked_immutable_exists",
                "model_id": model_id, "prediction_hash": pred_hash}
    return {"provider": provider, "status": "schema_failure" if schema_failure else "locked",
            "model_id": model_id, "model_id_source": "response" if model_id_verified else "hint_unverified",
            "prediction_hash": pred_hash, "cost_usd": record.cost_usd,
            "cost_reporting_status": cost_status, "runtime_seconds": record.runtime_seconds,
            "path": str(out) if out else None}


if __name__ == "__main__":
    raise SystemExit(main())
