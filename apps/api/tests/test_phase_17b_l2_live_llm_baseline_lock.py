"""Phase 17B-L2 — approval-gated live LLM Tomo baseline executor tests.

Exercises the fail-closed gate, the bundle-hash confirmation, the real-clock pre-outcome
gate, the reservation-based cost caps, and the record-writing path with FAKE provider
adapters — NO real provider call is ever made.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from datetime import date
from pathlib import Path

from assembly.benchmarks.market_fidelity.baseline_records import BaselinePredictionRecord
from assembly.benchmarks.market_fidelity.hash_lock import compute_prediction_hash

APP_API = Path(__file__).resolve().parents[1]
SCRIPT = APP_API / "scripts" / "phase_17b_l2_lock_live_llm_tomo_baselines.py"
TOMO = APP_API / "benchmarks" / "market_fidelity" / "prospective_baseline_inputs" / "tomo_endless_blue_2026"
BUNDLE = str(TOMO / "input_bundle.json")
HASH = "sha256:f29e8a46e0a677e0985e606f643e49fbc63822402d3dbf2c0570be5be2dd5d01"
BEFORE = date(2026, 6, 13)  # injected "real" date, before the 2026-06-21 outcome window
FULL_ENV = {"ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS": "true", "OPENAI_API_KEY": "sk-fake",
            "ANTHROPIC_API_KEY": "a-fake", "GOOGLE_API_KEY": "g-fake"}


def _load():
    spec = _ilu.spec_from_file_location("p17bl2", SCRIPT)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_ok(prompt, model_hint, api_key, timeout_s):
    return {"raw_text": json.dumps({
        "buyer_action_positive": 5.0, "receptive": 60.0, "uncertain_proof_needed": 25.0,
        "skeptical_resistant": 10.0, "confidence": 0.4, "top_adoption_reasons": ["novel concept"],
        "forecast_notes": "fake"}),
        "model_id": f"{model_hint}-2026-06-snapshot", "model_id_verified": True,
        "cost_usd": None, "runtime_s": 1.0}


def _fake_ok_unverified(prompt, model_hint, api_key, timeout_s):
    out = _fake_ok(prompt, model_hint, api_key, timeout_s)
    out["model_id"] = model_hint
    out["model_id_verified"] = False
    return out


def _fake_refuse(prompt, model_hint, api_key, timeout_s):
    return {"raw_text": "I can't produce a numeric forecast from this.", "model_id": model_hint,
            "model_id_verified": True, "cost_usd": None, "runtime_s": 0.5}


def _adapters(fn):
    return {"openai": fn, "anthropic": fn, "google": fn}


def _recdir(tmp_path):
    d = tmp_path / "benchmarks" / "market_fidelity" / "baseline_predictions"
    d.mkdir(parents=True)
    return d


def _approved_argv(providers=("openai", "anthropic", "google"), max_total=6.0, max_pp=2.0,
                   confirm=HASH, records_dir=None, cli=True):
    a = ["--input-bundle", BUNDLE, "--providers", *providers,
         "--max-total-usd", str(max_total), "--max-per-provider-usd", str(max_pp),
         "--confirm-input-bundle-hash", confirm]
    if cli:
        a.append("--i-understand-this-costs-real-money")
    if records_dir is not None:
        a += ["--records-dir", str(records_dir)]
    return a


def _run(mod, argv, adapters=None, env=None, now_real=BEFORE):
    return mod.main(argv, adapters=_adapters(adapters or _fake_ok), env=env, now_real=now_real)


# ------------------------------------------------------------------ fail-closed gate
def test_default_preflight_makes_no_call_and_writes_nothing(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, ["--input-bundle", BUNDLE, "--records-dir", str(rec)], env={})
    assert rc == 0  # PREPARED_NOT_RUN
    assert list(rec.glob("*.json")) == []


def test_env_true_without_cli_flag_refuses(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, _approved_argv(records_dir=rec, cli=False), env=FULL_ENV)
    assert rc == 0  # needs BOTH env + cli flag
    assert list(rec.glob("*.json")) == []


def test_missing_caps_refuses(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    argv = ["--input-bundle", BUNDLE, "--providers", "openai", "--i-understand-this-costs-real-money",
            "--confirm-input-bundle-hash", HASH, "--records-dir", str(rec)]
    rc = _run(mod, argv, env=FULL_ENV)
    assert rc == 0
    assert list(rec.glob("*.json")) == []


def test_wrong_confirm_hash_blocks(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, _approved_argv(confirm="sha256:deadbeef", records_dir=rec), env=FULL_ENV)
    assert rc == 1  # hard BLOCK
    assert list(rec.glob("*.json")) == []


def test_real_clock_on_or_after_outcome_blocks(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    for d in (date(2026, 6, 21), date(2026, 6, 22)):
        rc = _run(mod, _approved_argv(records_dir=rec), env=FULL_ENV, now_real=d)
        assert rc == 1  # blocked: real date in/after outcome window — NOT operator-spoofable
    assert list(rec.glob("*.json")) == []


def test_last_pre_outcome_day_locks(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, _approved_argv(providers=("openai",), records_dir=rec), env=FULL_ENV,
              now_real=date(2026, 6, 20))
    assert rc == 0
    assert len(list(rec.glob("*.json"))) == 1


def test_bad_records_dir_blocks(tmp_path):
    mod = _load()
    bad = tmp_path / "evil" / "baseline_predictions"  # parts[-3:] != benchmarks/market_fidelity/...
    bad.mkdir(parents=True)
    rc = _run(mod, _approved_argv(records_dir=bad), env=FULL_ENV)
    assert rc == 1
    assert list(bad.glob("*.json")) == []


def test_corrupt_provenance_fails_closed(tmp_path, capsys):
    mod = _load()
    # copy the real bundle (so input_bundle_hash == expected) next to a CORRUPT provenance.json
    bdir = tmp_path / "bundle"
    bdir.mkdir()
    (bdir / "input_bundle.json").write_text((TOMO / "input_bundle.json").read_text())
    (bdir / "provenance.json").write_text("{ this is not json")
    rec = _recdir(tmp_path)
    argv = _approved_argv(records_dir=rec)
    argv[argv.index(BUNDLE)] = str(bdir / "input_bundle.json")
    rc = _run(mod, argv, env=FULL_ENV)
    assert rc == 1  # leak guard could not load -> hard block
    assert "leak guard could not load" in capsys.readouterr().err
    assert list(rec.glob("*.json")) == []


# ------------------------------------------------------------------- adapter isolation
def test_module_import_loads_no_sdk():
    before = set(sys.modules)
    _load()
    for sdk in ("openai", "anthropic", "google.generativeai"):
        assert sdk not in (set(sys.modules) - before), f"{sdk} must not import at module load"


# ----------------------------------------------------------------- live (fake) locking
def test_fake_response_locks_three_immutable_self_verifying_records(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, _approved_argv(records_dir=rec), env=FULL_ENV)
    assert rc == 0
    files = sorted(rec.glob("*.json"))
    assert len(files) == 3
    for f in files:
        r = BaselinePredictionRecord.model_validate(json.loads(f.read_text()))
        assert r.method_class == "plain_llm"
        assert r.mode == "live_provider_call"
        assert r.observed is None
        assert r.input_bundle_hash == HASH
        assert r.schema_failure is False
        assert r.prediction_hash == compute_prediction_hash(
            method_id=r.method_id, method_version=r.method_version, input_bundle_hash=r.input_bundle_hash,
            prediction_payload=r.prediction_payload, locked_at=r.locked_at)
        assert r.method_version.endswith("snapshot")  # exact runtime model id recorded
        assert json.loads(r.notes)["model_id_source"] == "response"


def test_unverified_model_id_is_labeled_honestly(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, _approved_argv(providers=("google",), records_dir=rec),
              adapters=_fake_ok_unverified, env=FULL_ENV)
    assert rc == 0
    r = BaselinePredictionRecord.model_validate(json.loads(next(rec.glob("*.json")).read_text()))
    assert r.method_version == "gemini-3.5-flash"  # SDK echoed no id -> the hint
    assert json.loads(r.notes)["model_id_source"] == "hint_unverified"


def test_fake_refusal_writes_honest_schema_failure(tmp_path):
    mod = _load()
    rec = _recdir(tmp_path)
    rc = _run(mod, _approved_argv(providers=("openai",), records_dir=rec),
              adapters=_fake_refuse, env=FULL_ENV)
    assert rc == 0
    r = BaselinePredictionRecord.model_validate(json.loads(next(rec.glob("*.json")).read_text()))
    assert r.schema_failure is True
    assert r.prediction_payload.get("schema_failure") is True
    assert json.loads(r.notes)["schema_failure_reason"]


def test_duplicate_run_refuses_overwrite(tmp_path, capsys):
    mod = _load()
    rec = _recdir(tmp_path)
    _run(mod, _approved_argv(providers=("openai",), records_dir=rec), env=FULL_ENV)
    assert len(list(rec.glob("*.json"))) == 1
    rc = _run(mod, _approved_argv(providers=("openai",), records_dir=rec), env=FULL_ENV)
    assert rc == 0
    assert len(list(rec.glob("*.json"))) == 1  # immutable; not overwritten
    assert "blocked_immutable_exists" in capsys.readouterr().out


def test_cost_cap_skips_third_provider(tmp_path, capsys):
    mod = _load()
    rec = _recdir(tmp_path)
    # cap total 4, per-provider 2 -> reservation allows 2 calls, 3rd is blocked_by_cost_cap
    rc = _run(mod, _approved_argv(max_total=4.0, max_pp=2.0, records_dir=rec), env=FULL_ENV)
    assert rc == 0
    assert len(list(rec.glob("*.json"))) == 2
    out = capsys.readouterr().out
    assert "blocked_by_cost_cap" in out
    assert '"reserved_usd_total": 4.0' in out  # reservation never exceeds the global cap


def test_missing_api_key_blocks_that_provider(tmp_path, capsys):
    mod = _load()
    rec = _recdir(tmp_path)
    env = {k: v for k, v in FULL_ENV.items() if k != "GOOGLE_API_KEY"}
    rc = _run(mod, _approved_argv(records_dir=rec), env=env)
    assert rc == 0
    assert len(list(rec.glob("*.json"))) == 2  # openai + anthropic; google has no key
    assert "blocked_missing_api_key" in capsys.readouterr().out


def test_forbidden_values_catch_assembly_secrets():
    mod = _load()
    vals = mod._forbidden_values(TOMO / "input_bundle.json")
    assert any("0a9ce639" in v for v in vals)
    assert "83.3333" in vals and "8.3333" in vals


# --------------------------------------------------------------- ledger / isolation
def test_records_are_not_validation_cases_and_tomo_untouched():
    from assembly.validation_ledger import load_all_cases
    cases = load_all_cases()
    assert len(cases) == 8
    tomo = [c for c in cases if c.case_id == "run_4fcc4cbf-64d5-478f-a4a1-88df1a5c6ea9"][0]
    assert tomo.metadata.validation_status == "pending"
    assert getattr(tomo, "observed", None) is None
    assert len(getattr(tomo, "action_signals", [])) == 0
