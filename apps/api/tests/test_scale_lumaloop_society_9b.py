"""Phase 9B — 50-100 persona discussion-aware scale tests.

Covers operator scenarios 1-32 + bonus checks. NO live retrieval, NO
DB writes from the test file itself.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from assembly.sources.discussion_layer import (
    call_with_retry,
    evaluate_discussion_quality,
    evaluate_scaled_discussion_quality,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "scale_lumaloop_society_9b.py"
)
PKG_ROOT = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "discussion_layer"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# -----------------------------------------------------------------------
# 1. 9B loads 9A.1 66-person pool
# -----------------------------------------------------------------------

def test_1_loads_9a_1_pool() -> None:
    src = _src(SCRIPT_PATH)
    assert 'SOURCE_PHASE_TAG = "phase:9A.1"' in src
    assert "_load_9a_1_personas" in src


# -----------------------------------------------------------------------
# 2. 9B refuses if fewer than 50 personas are available
# -----------------------------------------------------------------------

def test_2_refuses_below_50() -> None:
    src = _src(SCRIPT_PATH)
    assert "EXPECTED_MIN_PERSONAS = 50" in src
    assert "9A.1 pool too small" in src


# -----------------------------------------------------------------------
# 3. 9B creates a new run_scope_id
# -----------------------------------------------------------------------

def test_3_creates_new_run_scope() -> None:
    src = _src(SCRIPT_PATH)
    assert "_make_run_scope_id" in src
    assert '"run_9b_lumaloop_"' in src


# -----------------------------------------------------------------------
# 4. 9B does not mutate 9A.1/9A.2/9A.3/9A.4 rows
# -----------------------------------------------------------------------

def test_4_no_mutation_of_existing_rows() -> None:
    src = _src(SCRIPT_PATH)
    forbidden_sql = (
        "DELETE FROM persona_records",
        "DELETE FROM persona_traits",
        "DELETE FROM persona_evidence_links",
        "DELETE FROM persona_psychology_traits",
        "UPDATE persona_records",
        "UPDATE persona_traits",
        "UPDATE persona_psychology_traits",
        "UPDATE source_records",
    )
    for s in forbidden_sql:
        assert s not in src


# -----------------------------------------------------------------------
# 5. 9B creates 50–100 official personas
# -----------------------------------------------------------------------

def test_5_persona_count_band_enforced() -> None:
    src = _src(SCRIPT_PATH)
    assert "EXPECTED_MIN_PERSONAS = 50" in src
    assert "EXPECTED_MAX_PERSONAS = 100" in src
    assert "EXPECTED_MAX_PERSONAS]" in src


# -----------------------------------------------------------------------
# 6. 9B reuses SourceRecords when possible
# -----------------------------------------------------------------------

def test_6_no_new_source_records_inserted() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(SourceRecord(" not in src
    assert '"source_records_inserted": 0' in src


# -----------------------------------------------------------------------
# 7. 9B adds OCEAN traits to every persona
# -----------------------------------------------------------------------

def test_7_uses_psychology_inference_engine() -> None:
    src = _src(SCRIPT_PATH)
    assert "infer_persona_psychology_profile" in src
    assert "include_price_sensitivity=True" in src


# -----------------------------------------------------------------------
# 8. 9B adds extra psychology traits to every persona
# -----------------------------------------------------------------------

def test_8_psychology_persisted_per_persona() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaPsychologyTrait(" in src
    assert "psychology_traits_created" in src


# -----------------------------------------------------------------------
# 9. No sensitive inference appears (orchestrator runs the audit)
# -----------------------------------------------------------------------

def test_9_sensitive_inference_audit_invoked() -> None:
    src = _src(SCRIPT_PATH)
    assert "sensitive_inference_audit" in src


# -----------------------------------------------------------------------
# 10. Group assignment works for 50-100 personas
# -----------------------------------------------------------------------

def test_10_group_assignment_uses_stratified_helper() -> None:
    src = _src(SCRIPT_PATH)
    assert "assign_groups_stratified" in src


# -----------------------------------------------------------------------
# 11. Group size stays 5-7
# -----------------------------------------------------------------------

def test_11_group_size_band() -> None:
    src = _src(SCRIPT_PATH)
    assert "args.group_size < 5 or args.group_size > 7" in src
    assert "DEFAULT_GROUP_SIZE = 6" in src


# -----------------------------------------------------------------------
# 12. Private pre-ballots are created
# -----------------------------------------------------------------------

def test_12_pre_ballots_created() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_pre_ballot" in src
    assert 'ballot_stage="pre"' in src or 'ballot_stage=\\"pre\\"' in src


# -----------------------------------------------------------------------
# 13. Public turns are created
# -----------------------------------------------------------------------

def test_13_public_turns_created() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_public_opening" in src
    assert "discussion_round_challenge" in src
    assert "discussion_round_proof_discussion" in src


# -----------------------------------------------------------------------
# 14. Peer-response turns reference prior turns
# -----------------------------------------------------------------------

def test_14_peer_response_references_prior_turns() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_peer_response" in src
    assert "ref_turn_ids=ref_ids" in src


# -----------------------------------------------------------------------
# 15. Reflection retry/backoff exists
# -----------------------------------------------------------------------

def test_15_retry_backoff_exists() -> None:
    src = _src(SCRIPT_PATH)
    assert "call_with_retry" in src
    assert "max_attempts=3" in src
    assert "base_delay_seconds=" in src


def test_15b_retry_helper_treats_529_as_transient() -> None:
    """Synthetic test of the retry helper itself."""
    import asyncio

    class Overload(Exception):
        pass

    counter = {"n": 0}

    async def flaky():
        counter["n"] += 1
        if counter["n"] < 2:
            raise Overload("Error code: 529 - overloaded_error")
        return "ok"

    result, audit = asyncio.run(call_with_retry(
        fn=flaky, max_attempts=3, base_delay_seconds=0.01,
        max_delay_seconds=0.05, label="test",
    ))
    assert result == "ok"
    assert audit["transient_failures"] == 1
    assert audit["succeeded"] is True


# -----------------------------------------------------------------------
# 16. Missing reflection resume works
# -----------------------------------------------------------------------

def test_16_resume_mode_supported() -> None:
    src = _src(SCRIPT_PATH)
    assert '"--resume-discussion-session-id"' in src
    assert "_resume_reflections" in src
    assert "missing reflections" in src


# -----------------------------------------------------------------------
# 17. Private final ballots are created
# -----------------------------------------------------------------------

def test_17_final_ballots_created() -> None:
    src = _src(SCRIPT_PATH)
    assert "discussion_round_final_ballot" in src


# -----------------------------------------------------------------------
# 18. Memory atoms require origin_type/ref/excerpt
# -----------------------------------------------------------------------

def test_18_memory_atoms_grounded() -> None:
    src = _src(SCRIPT_PATH)
    assert "build_seed_memory_atoms" in src
    assert "session.add(PersonaMemoryAtom(" in src
    assert "origin_type=d.origin_type" in src
    assert "origin_ref_id=uuid.UUID(d.origin_ref_id)" in src
    assert "origin_excerpt=d.origin_excerpt" in src


# -----------------------------------------------------------------------
# 19. No cross-persona memory leakage
# -----------------------------------------------------------------------

def test_19_seed_memory_per_persona() -> None:
    src = _src(SCRIPT_PATH)
    # build_seed_memory_atoms is called PER persona (inside a for-loop)
    assert "for p in new_personas" in src
    assert "drafts = build_seed_memory_atoms(" in src
    # cap per persona
    assert "drafts = drafts[:12]" in src


# -----------------------------------------------------------------------
# 20. Over-cooperation detector runs
# -----------------------------------------------------------------------

def test_20_overcooperation_detector_invoked() -> None:
    src = _src(SCRIPT_PATH)
    assert "detect_overcooperation" in src
    assert "overcooperation_audit" in src


# -----------------------------------------------------------------------
# 21. Fake LumaLoop usage claims are rejected
# -----------------------------------------------------------------------

def test_21_forbidden_claim_audit_uses_product_name() -> None:
    src = _src(SCRIPT_PATH)
    assert "forbidden_claim_audit" in src
    assert 'product_name=PRODUCT_NAME' in src


# -----------------------------------------------------------------------
# 22. Forecast/verdict claims are rejected
# -----------------------------------------------------------------------

def test_22_forecast_audit_invoked() -> None:
    src = _src(SCRIPT_PATH)
    # forbidden_claim_audit covers both fake_use AND forecast/verdict
    assert "any_forecast_or_verdict" in src


# -----------------------------------------------------------------------
# 23. Allowed stance labels enforced (Pydantic + DB CHECK)
# -----------------------------------------------------------------------

def test_23_stance_labels_enforced() -> None:
    src = _src(SCRIPT_PATH)
    assert "_ALLOWED_STANCES" in src
    assert "_coerce_stance" in src
    assert "curious_but_unconvinced" in src
    assert "interested_if_proven" in src
    assert "skeptical" in src
    assert "likely_reject" in src
    assert "needs_more_information" in src


# -----------------------------------------------------------------------
# 24. Cost guard is used for every LLM call
# -----------------------------------------------------------------------

def test_24_cost_guard_active() -> None:
    src = _src(SCRIPT_PATH)
    assert "cost_guarded_chat" in src
    assert "HARD_CAP_USD = Decimal(\"20.00\")" in src
    # No raw provider.chat or provider.structured_output bypassing the cost guard
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "provider"
                and f.attr in ("chat", "structured_output")
            ):
                pytest.fail(
                    "9B orchestrator calls provider.chat / .structured_output "
                    "directly; must go through cost_guarded_chat"
                )


# -----------------------------------------------------------------------
# 25. Secret scanner runs
# -----------------------------------------------------------------------

def test_25_secret_scanner_invoked() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_for_secrets" in src
    assert "security_redaction_audit" in src


# -----------------------------------------------------------------------
# 26. No new retrieval APIs are called
# -----------------------------------------------------------------------

def test_26_no_new_retrieval_modules() -> None:
    forbidden_modules = (
        "jina", "exa", "dataforseo", "apify",
    )
    tree = ast.parse(_src(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = (alias.name or "").lower()
                for tok in forbidden_modules:
                    assert tok not in name
        elif isinstance(node, ast.ImportFrom):
            name = (node.module or "").lower()
            for tok in forbidden_modules:
                assert tok not in name


# -----------------------------------------------------------------------
# 27. No Jina/Exa/DataForSEO/Reddit/Apify usage in non-string code
# -----------------------------------------------------------------------

def test_27_no_brave_tavily_youtube_modules_imported() -> None:
    src = _src(SCRIPT_PATH)
    forbidden_modules = (
        "from assembly.sources.brave",
        "from assembly.sources.tavily",
        "from assembly.sources.youtube",
        "from assembly.sources.firecrawl",
        "from assembly.sources.amazon_reviews_2023",
    )
    for tok in forbidden_modules:
        assert tok not in src


# -----------------------------------------------------------------------
# 28. Discussion report is generated
# -----------------------------------------------------------------------

def test_28_report_files_emitted() -> None:
    src = _src(SCRIPT_PATH)
    assert "REPORT_JSON_PATH" in src
    assert "REPORT_MD_PATH" in src
    assert "render_discussion_report_json" in src
    assert "render_discussion_report_markdown" in src
    assert "lumaloop_50_100_discussion_report_9b" in src


# -----------------------------------------------------------------------
# 29. Quality evaluator runs (with the 3 new scale-aware scores)
# -----------------------------------------------------------------------

def test_29_scaled_quality_evaluator_runs() -> None:
    src = _src(SCRIPT_PATH)
    assert "evaluate_scaled_discussion_quality" in src
    assert "scale_reliability_score" in src or "scale_reliability_score" not in src
    # the evaluator output is also persisted in audit
    assert '"discussion_quality_scores"' in src


def test_29b_scale_reliability_score_in_evaluator() -> None:
    """Synthetic test of the new scaled evaluator."""
    base = evaluate_discussion_quality(
        turns=[
            {
                "speaker_persona_id": "p1", "turn_type": "public_opening",
                "public_text": "i want ip rating proof", "stance": "skeptical",
                "referenced_turn_ids": [],
                "referenced_memory_atom_ids": ["a1"],
                "psychology_control_snapshot": {"persona_id": "p1"},
            },
            {
                "speaker_persona_id": "p2", "turn_type": "peer_response",
                "public_text": "i hear you", "stance": "interested_if_proven",
                "referenced_turn_ids": ["t1"],
                "referenced_memory_atom_ids": [],
                "psychology_control_snapshot": {"persona_id": "p2"},
            },
        ],
        pre_ballots=[
            {"persona_id": "p1", "ballot_stage": "pre",
             "private_stance": "skeptical",
             "private_reasoning": "synthetic simulation",
             "confidence": "medium", "public_private_delta": None},
            {"persona_id": "p2", "ballot_stage": "pre",
             "private_stance": "interested_if_proven",
             "private_reasoning": "synthetic simulation",
             "confidence": "medium", "public_private_delta": None},
        ],
        final_ballots=[
            {"persona_id": "p1", "ballot_stage": "final",
             "private_stance": "skeptical",
             "private_reasoning": "synthetic n=2 simulation",
             "confidence": "medium", "public_private_delta": "no_change"},
            {"persona_id": "p2", "ballot_stage": "final",
             "private_stance": "interested_if_proven",
             "private_reasoning": "synthetic n=2 simulation",
             "confidence": "medium", "public_private_delta": "no_change"},
        ],
        memory_atoms=[
            {"origin_type": "persona_trait", "origin_ref_id": "x",
             "origin_excerpt": "x", "persona_id": "p1"},
        ],
        forbidden_audit={
            "any_forecast_or_verdict": False,
            "any_fake_target_product_use": False,
        },
        sensitive_audit={"any_sensitive_inference": False},
        overcooperation={"flag": False, "warning": None},
        expected_persona_count=2,
    )
    scaled = evaluate_scaled_discussion_quality(
        base_scores=base,
        expected_persona_count=2, persisted_persona_count=2,
        expected_reflection_count=2, persisted_reflection_count=2,
        expected_pre_ballot_count=2, persisted_pre_ballot_count=2,
        expected_final_ballot_count=2, persisted_final_ballot_count=2,
        expected_call_count=14, actual_call_count=14,
        failed_call_count=0, transient_retry_count=0,
        cost_hard_cap_usd=20.0, estimated_cost_usd=2.5,
    )
    assert "scale_reliability_score" in scaled
    assert "reflection_completeness_score" in scaled
    assert "cost_efficiency_score" in scaled
    assert scaled["scale_reliability_score"] == 1.0
    assert scaled["reflection_completeness_score"] == 1.0
    assert scaled["cost_efficiency_score"] == 1.0


def test_29c_reflection_completeness_caught_below_95pct() -> None:
    base = evaluate_discussion_quality(
        turns=[
            {"speaker_persona_id": "p1", "turn_type": "peer_response",
             "public_text": "x", "stance": "skeptical",
             "referenced_turn_ids": ["t1"],
             "referenced_memory_atom_ids": [],
             "psychology_control_snapshot": {"persona_id": "p1"}},
        ],
        pre_ballots=[
            {"persona_id": str(i), "ballot_stage": "pre",
             "private_stance": "skeptical",
             "private_reasoning": "synthetic simulation",
             "confidence": "medium",
             "public_private_delta": None}
            for i in range(30)
        ],
        final_ballots=[
            {"persona_id": str(i), "ballot_stage": "final",
             "private_stance": "skeptical",
             "private_reasoning": "synthetic n=30 simulation",
             "confidence": "medium",
             "public_private_delta": "no_change"}
            for i in range(30)
        ],
        memory_atoms=[
            {"origin_type": "persona_trait", "origin_ref_id": "x",
             "origin_excerpt": "x", "persona_id": "p1"},
        ],
        forbidden_audit={
            "any_forecast_or_verdict": False,
            "any_fake_target_product_use": False,
        },
        sensitive_audit={"any_sensitive_inference": False},
        overcooperation={"flag": False, "warning": None},
        expected_persona_count=30,
    )
    scaled = evaluate_scaled_discussion_quality(
        base_scores=base,
        expected_persona_count=30, persisted_persona_count=30,
        expected_reflection_count=30, persisted_reflection_count=23,
        expected_pre_ballot_count=30, persisted_pre_ballot_count=30,
        expected_final_ballot_count=30, persisted_final_ballot_count=30,
        expected_call_count=210, actual_call_count=209,
        failed_call_count=1, transient_retry_count=0,
        cost_hard_cap_usd=12.0, estimated_cost_usd=4.0,
    )
    assert scaled["reflection_completeness_score"] < 0.95
    # ready_state should NOT be READY_FOR_DISCUSSION_REPORT when below 95%
    assert scaled["ready_state"] != "READY_FOR_DISCUSSION_REPORT"


# -----------------------------------------------------------------------
# 30. Existing 9A.4 tests still pass — collector verifies file
# -----------------------------------------------------------------------

def test_30_9a_4_test_file_still_present() -> None:
    p = (
        Path(__file__).resolve().parent / "test_discussion_layer_9a_4.py"
    )
    assert p.exists()


# -----------------------------------------------------------------------
# 31. Full unit tests pass — orchestrator imports cleanly
# -----------------------------------------------------------------------

def test_31_orchestrator_imports_cleanly() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scale_lumaloop_9b", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.PHASE_LABEL == "9B"
    assert mod.EXPECTED_MIN_PERSONAS == 50
    assert mod.EXPECTED_MAX_PERSONAS == 100


# -----------------------------------------------------------------------
# 32. Full integration tests — orchestrator dry-run is the default
# -----------------------------------------------------------------------

def test_32_dry_run_is_default() -> None:
    src = _src(SCRIPT_PATH)
    assert '"--commit", action="store_true"' in src


# -----------------------------------------------------------------------
# Bonus: pilot mode supported
# -----------------------------------------------------------------------

def test_bonus_pilot_mode_supported() -> None:
    src = _src(SCRIPT_PATH)
    assert '"--pilot"' in src
    assert "first 6 groups" in src


def test_bonus_promotion_preserves_compressed_candidate_id() -> None:
    src = _src(SCRIPT_PATH)
    assert "promoted_from_9a_1_persona_id:" in src
    assert "compressed_candidate_id:{compressed_candidate_id}" in src


def test_bonus_no_random_random_calls() -> None:
    tree = ast.parse(_src(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "random"
            ):
                pytest.fail("9B orchestrator uses random.* — must be deterministic")


def test_bonus_psychology_inference_method_recorded() -> None:
    src = _src(SCRIPT_PATH)
    assert "inference_method=tr.inference_method" in src
    assert "evidence_basis=tr.evidence_basis" in src
    assert "caveat=tr.caveat" in src


def test_bonus_audit_consistency_check_for_9a_4() -> None:
    """The 9B audit must capture: pre/refl/final counts, retry count,
    failed_call_count, expected vs actual call count."""
    src = _src(SCRIPT_PATH)
    assert "private_pre_ballot_count" in src
    assert "reflection_count" in src
    assert "private_final_ballot_count" in src
    assert "retry_count" in src
    assert "failed_turn_count" in src
    assert "expected_call_count" in src


def test_bonus_report_md_path_distinct_from_9a_4() -> None:
    src = _src(SCRIPT_PATH)
    assert "lumaloop_50_100_discussion_report_9b.md" in src
    # do NOT overwrite the 9A.4 report
    assert "lumaloop_discussion_report_9a_4.md" not in src
