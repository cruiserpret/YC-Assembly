"""Phase 17B-L — Tomo baseline lock preflight + naive locks + live-call gate.

Covers: fail-closed live-call gate, leakage-free baseline prompt, the committed Tomo
frozen input bundle (clean + hash-stable), honest naive baselines, immutable record
integrity, and isolation from the validation ledger. No model is called.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.benchmarks.market_fidelity.baseline_prompt import (
    AMFB_OUTPUT_CONTRACT,
    FORBIDDEN_PROMPT_KEYS,
    assert_prompt_is_clean,
    build_baseline_prompt,
    prompt_hash,
    render_bundle_for_prompt,
)
from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    default_records_dir,
    load_records,
)
from assembly.benchmarks.market_fidelity.hash_lock import (
    compute_prediction_hash,
    input_bundle_hash,
)
from assembly.benchmarks.market_fidelity.live_call_gate import (
    APPROVAL_ENV_VAR,
    evaluate_live_call_gate,
    gate_from_env,
)
from assembly.benchmarks.market_fidelity.naive_baselines import naive_baseline
from assembly.benchmarks.market_fidelity.validators import check_no_post_lock_sources

APP_API = Path(__file__).resolve().parents[1]
TOMO_DIR = APP_API / "benchmarks" / "market_fidelity" / "prospective_baseline_inputs" / "tomo_endless_blue_2026"
CASE_ID = "tomo_endless_blue_onibi_ks_2026"
LOCKED_AT = "2026-06-04T03:23:13.481724+00:00"
# Assembly's actual locked Tomo proportions / hash — must NOT appear in any baseline prompt.
ASSEMBLY_FORBIDDEN_VALUES = ["83.3333", "8.3333", "0a9ce639"]


@pytest.fixture
def bundle() -> dict:
    return json.loads((TOMO_DIR / "input_bundle.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- gate
def test_gate_default_is_fail_closed():
    d = evaluate_live_call_gate(
        approval_flag_present=False, providers_requested=["openai"],
        global_cost_cap_usd=None, per_provider_cost_cap_usd=None,
    )
    assert d.approved is False
    assert d.mode == "preflight_dry_run"
    assert any("no explicit approval" in b for b in d.blocking_conditions)


def test_gate_requires_both_caps_even_with_approval():
    d = evaluate_live_call_gate(
        approval_flag_present=True, providers_requested=["openai"],
        global_cost_cap_usd=None, per_provider_cost_cap_usd=None,
    )
    assert d.approved is False
    assert any("global cost cap" in b for b in d.blocking_conditions)
    assert any("per-provider cost cap" in b for b in d.blocking_conditions)


def test_gate_rejects_unknown_provider():
    d = evaluate_live_call_gate(
        approval_flag_present=True, providers_requested=["openai", "acme"],
        global_cost_cap_usd=6.0, per_provider_cost_cap_usd=2.0,
    )
    assert d.approved is False
    assert any("unknown provider" in b for b in d.blocking_conditions)


def test_gate_rejects_per_provider_exceeding_global():
    d = evaluate_live_call_gate(
        approval_flag_present=True, providers_requested=["openai"],
        global_cost_cap_usd=2.0, per_provider_cost_cap_usd=5.0,
    )
    assert d.approved is False
    assert any("exceeds the global cap" in b for b in d.blocking_conditions)


def test_gate_all_conditions_met_approves_but_notes_executor_unwired():
    d = evaluate_live_call_gate(
        approval_flag_present=True, providers_requested=["openai", "anthropic", "google"],
        global_cost_cap_usd=6.0, per_provider_cost_cap_usd=2.0,
    )
    assert d.approved is True
    assert d.mode == "approved_live"
    assert "deliberately-unwired" in d.notes


def test_gate_from_env_reads_flag_but_still_needs_caps():
    # flag present via env, but no caps -> still not approved
    d = gate_from_env(
        providers_requested=["openai"], global_cost_cap_usd=None,
        per_provider_cost_cap_usd=None, env={APPROVAL_ENV_VAR: "true"},
    )
    assert d.approved is False
    assert d.approval_flag_present is True
    # empty env -> no approval flag
    d2 = gate_from_env(
        providers_requested=["openai"], global_cost_cap_usd=6.0,
        per_provider_cost_cap_usd=2.0, env={},
    )
    assert d2.approval_flag_present is False
    assert d2.approved is False


# ------------------------------------------------------------------------- prompt
def test_prompt_is_clean_for_tomo_bundle(bundle):
    prompt = build_baseline_prompt(bundle)
    assert assert_prompt_is_clean(prompt, forbidden_values=ASSEMBLY_FORBIDDEN_VALUES) == []
    # Assembly's actual numbers / hash never appear
    for v in ASSEMBLY_FORBIDDEN_VALUES:
        assert v not in prompt
    for k in FORBIDDEN_PROMPT_KEYS:
        assert k not in prompt.lower()


def test_prompt_asks_for_full_amfb_schema(bundle):
    prompt = build_baseline_prompt(bundle)
    for token in ("buyer_action_positive", "receptive", "uncertain_proof_needed",
                  "skeptical_resistant", "confidence", "schema_failure",
                  "top_adoption_reasons", "top_rejection_reasons", "one_thing_needed",
                  "recommended_segment", "expected_action_signal"):
        assert token in prompt
    assert "sum to ~100" in AMFB_OUTPUT_CONTRACT


def test_prompt_bans_outside_knowledge(bundle):
    prompt = build_baseline_prompt(bundle)
    assert "Do NOT use outside knowledge" in prompt
    assert "current funding amount" in prompt


def test_prompt_render_whitelist_drops_audit_fields():
    # an injected audit/outcome field must NOT render into the prompt
    poisoned = {
        "benchmark_case_id": "x",
        "product_name": "P",
        "product_description": "D",
        "assembly_prediction_hash": "sha256:0a9ce639",
        "predicted_proportions": {"buyer_action_positive": 0.0},
        "observed": {"final_pledged_usd": 999999},
    }
    rendered = render_bundle_for_prompt(poisoned)
    assert "0a9ce639" not in rendered
    assert "predicted_proportions" not in rendered
    assert "999999" not in rendered
    assert "P" in rendered and "D" in rendered


def test_prompt_hash_is_stable(bundle):
    assert prompt_hash(build_baseline_prompt(bundle)) == prompt_hash(build_baseline_prompt(bundle))


# ------------------------------------------------------------------------- bundle
def test_bundle_is_leakage_clean(bundle):
    assert check_no_post_lock_sources(bundle, LOCKED_AT) == []


def test_bundle_hash_matches_committed_file(bundle):
    committed = (TOMO_DIR / "input_bundle_hash.txt").read_text(encoding="utf-8").strip()
    assert input_bundle_hash(bundle) == committed


def test_bundle_excludes_current_progress_and_outcome(bundle):
    # the bundle must NOT carry current-progress / outcome / Assembly-prediction fields
    for forbidden in ("crowdfunding_progress", "category_prior", "predicted_proportions",
                      "observed", "current_funding", "final_pledged_usd", "backers"):
        assert forbidden not in bundle
    blob = json.dumps(bundle).lower()
    assert "0a9ce639" not in blob  # no Assembly prediction hash
    assert "83.3333" not in blob   # no Assembly proportions


def test_provenance_records_authenticity(bundle):
    prov = json.loads((TOMO_DIR / "provenance.json").read_text(encoding="utf-8"))
    assert prov["authenticity_proof"]["recorded_brief_hash"].startswith("sha256:4b188a0d")
    assert "REPRODUCED" in prov["authenticity_proof"]["result"]
    assert prov["leakage_guard"]["clean"] is True
    assert prov["benchmark_input_bundle_hash"] == input_bundle_hash(bundle)


# ------------------------------------------------------------------ naive baselines
def test_naive_distributions_lock_on_tomo_bundle(bundle):
    for nb in ("always_zero_buyer", "majority_receptive", "uniform_distribution"):
        p = naive_baseline(nb, bundle)
        assert p.schema_failure is False
        assert abs(sum(p.buckets().values()) - 100.0) <= 1.5


def test_naive_placeholders_honestly_schema_fail(bundle):
    # no category_prior and no pre-lock crowdfunding_progress in the bundle -> schema_failure
    for nb in ("category_prior_placeholder", "crowdfunding_goal_progress_placeholder"):
        p = naive_baseline(nb, bundle)
        assert p.schema_failure is True
        assert p.schema_failure_reason.strip()


# ------------------------------------------------------------------------ records
def test_locked_records_are_consistent_and_isolated():
    recs = [r for r in load_records() if r.benchmark_case_id == CASE_ID]
    naive = [r for r in recs if r.method_class == "naive_baseline"]
    assert len(naive) == 5
    assert all(r.mode == "naive" and r.cost_usd == 0.0 for r in naive)
    # EVERY locked Tomo record — the 5 naive baselines AND any live LLM baselines
    # (gpt/claude/gemini) locked later — shares the ONE frozen bundle hash (the fairness
    # invariant), is observed-free, carries the not-validation-data purpose, and lives
    # outside validation_cases/ (never loaded by the ledger).
    assert len({r.input_bundle_hash for r in recs}) == 1
    assert all(r.observed is None for r in recs)
    assert all(r.purpose == "benchmark_baseline_prediction_not_validation_data" for r in recs)
    assert "validation_cases" not in str(default_records_dir())
    assert default_records_dir().name == "baseline_predictions"


def test_tomo_validation_case_untouched():
    from assembly.validation_ledger import load_all_cases
    cases = load_all_cases()
    assert len(cases) == 8
    tomo = [c for c in cases if c.case_id == "run_4fcc4cbf-64d5-478f-a4a1-88df1a5c6ea9"][0]
    assert tomo.metadata.validation_status == "pending"
    assert getattr(tomo, "observed", None) is None
    assert len(getattr(tomo, "action_signals", [])) == 0
    assert tomo.prediction_lock.prediction_hash.endswith("263fa2e8")


# ----------------------------------------------- review-hardening regressions
_VALID_PAYLOAD = {"buyer_action_positive": 10.0, "receptive": 50.0,
                  "uncertain_proof_needed": 30.0, "skeptical_resistant": 10.0, "confidence": 0.6}


def _load_preflight():
    spec = _ilu.spec_from_file_location(
        "phase_17b_l_preflight", APP_API / "scripts" / "phase_17b_l_baseline_preflight.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_record_rejects_tampered_prediction_hash():
    with pytest.raises(ValidationError):
        BaselinePredictionRecord(
            benchmark_case_id="c", method_class="naive_baseline", method_id="m", method_version="v",
            input_bundle_hash="sha256:ib", prediction_payload=_VALID_PAYLOAD,
            prediction_hash="sha256:" + "b" * 64, locked_at="2026-06-10T00:00:00+00:00", mode="naive",
        )


def test_record_rejects_inconsistent_schema_failure_flag():
    payload = {"confidence": 0.0, "schema_failure": True, "schema_failure_reason": "x"}
    h = compute_prediction_hash(method_id="m", method_version="v", input_bundle_hash="sha256:ib",
                                prediction_payload=payload, locked_at="2026-06-10T00:00:00+00:00")
    with pytest.raises(ValidationError):
        BaselinePredictionRecord(
            benchmark_case_id="c", method_class="plain_llm", method_id="m", method_version="v",
            input_bundle_hash="sha256:ib", prediction_payload=payload, prediction_hash=h,
            locked_at="2026-06-10T00:00:00+00:00", mode="manual_output", schema_failure=False,
        )


def test_prompt_clean_check_flags_assembly_and_phase_tokens():
    assert assert_prompt_is_clean("forecast this. assembly knows best.")  # 'assembly' banned
    assert assert_prompt_is_clean("this is a Phase 16A target")          # 'phase 16a' banned
    assert assert_prompt_is_clean("a clean product market forecast prompt") == []


def test_committed_campaign_context_is_neutral(bundle):
    ctx = bundle["campaign_context"]
    for bad in ("Assembly", "Phase 16A", "PROSPECTIVE FORECAST TARGET", "buyer-anchor",
                "will later be compared"):
        assert bad not in ctx


def test_preflight_forbidden_values_include_assembly_secrets():
    vals = _load_preflight()._forbidden_values(TOMO_DIR / "input_bundle.json")
    assert any("0a9ce639" in v for v in vals)        # the Assembly prediction hash
    assert "83.3333" in vals and "8.3333" in vals    # the locked proportions


def test_preflight_clean_run_is_prepared_not_run(capsys):
    rc = _load_preflight().main([
        "--input-bundle", str(TOMO_DIR / "input_bundle.json"),
        "--providers", "openai", "anthropic", "google", "--locked-at", LOCKED_AT,
    ])
    assert rc == 0  # PREPARED_NOT_RUN — no spend, no approval


def test_preflight_rejects_non_dict_bundle(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert _load_preflight().main(["--input-bundle", str(bad)]) == 2


def test_preflight_rejects_hash_drift(tmp_path):
    b = json.loads((TOMO_DIR / "input_bundle.json").read_text())
    b["product_name"] = b["product_name"] + " EDITED"  # tamper a model-facing field
    d = tmp_path / "tomo"
    d.mkdir()
    (d / "input_bundle.json").write_text(json.dumps(b))
    (d / "input_bundle_hash.txt").write_text((TOMO_DIR / "input_bundle_hash.txt").read_text())
    rc = _load_preflight().main([
        "--input-bundle", str(d / "input_bundle.json"), "--providers", "openai", "--locked-at", LOCKED_AT,
    ])
    assert rc == 1  # input_bundle_hash drift refused
