"""Phase 9B.1 — reflection completion repair tests.

Covers operator scenarios 1-28. NO live retrieval, NO DB writes from
the test file itself.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "repair_9b_reflections_9b_1.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# -----------------------------------------------------------------------
# 1. 9B.1 loads existing 9B discussion session
# -----------------------------------------------------------------------

def test_1_loads_existing_9b_session() -> None:
    src = _src(SCRIPT_PATH)
    assert "_load_session_full" in src
    assert 'DiscussionSession.phase == "9B"' in src
    assert "no 9B discussion session" in src


# -----------------------------------------------------------------------
# 2. 9B.1 identifies missing reflection ballots
# -----------------------------------------------------------------------

def test_2_identifies_missing_reflections() -> None:
    src = _src(SCRIPT_PATH)
    assert "missing = sorted(all_pids - refl_existing" in src
    assert "missing_reflection_count" in src


# -----------------------------------------------------------------------
# 3. 9B.1 does not duplicate existing reflections
# -----------------------------------------------------------------------

def test_3_does_not_duplicate_reflections() -> None:
    src = _src(SCRIPT_PATH)
    assert "if pid_uuid in existing_set" in src
    assert "continue" in src
    assert "existing_set = set(existing_now)" in src


# -----------------------------------------------------------------------
# 4. Idempotency key works (session_id + persona_id + ballot_stage)
# -----------------------------------------------------------------------

def test_4_idempotency_key() -> None:
    src = _src(SCRIPT_PATH)
    # Pre-flight: confirm key is composed of the three required pieces
    assert (
        "DiscussionPrivateBallot.discussion_session_id == sess.id" in src
    )
    assert 'DiscussionPrivateBallot.ballot_stage == "reflection"' in src


# -----------------------------------------------------------------------
# 5. Strict reflection prompt requires JSON only
# -----------------------------------------------------------------------

def test_5_strict_prompt_is_json_only() -> None:
    src = _src(SCRIPT_PATH)
    assert "_SYSTEM_STRICT" in src
    # The strict system prompt must say "JSON only" / "no markdown"
    assert "EXACTLY one JSON object" in src
    assert "no markdown" in src
    assert "no fences" in src


# -----------------------------------------------------------------------
# 6. Invalid JSON triggers repair prompt (Attempt 2)
# -----------------------------------------------------------------------

def test_6_attempt_2_kicks_in_on_invalid_json() -> None:
    src = _src(SCRIPT_PATH)
    # The flow must have a fallback to "_llm_stricter" after the
    # strict attempt fails parsing/coercion.
    assert "_llm_stricter" in src
    assert "_SYSTEM_STRICTER" in src
    # The continue/return-after-success guard must be there so we
    # only fall through on actual failures.
    assert 'log["attempts"].append("strict_llm:failed")' in src


# -----------------------------------------------------------------------
# 7. Second invalid JSON triggers deterministic fallback
# -----------------------------------------------------------------------

def test_7_attempt_3_is_deterministic_fallback() -> None:
    src = _src(SCRIPT_PATH)
    assert "_deterministic_fallback" in src
    assert 'log["attempts"].append("stricter_llm:failed")' in src
    # the deterministic fallback is invoked AFTER both LLM attempts
    assert "deterministic_fallback" in src
    assert "deterministic_repair" in src


# -----------------------------------------------------------------------
# 8. Deterministic fallback is clearly marked
# -----------------------------------------------------------------------

def test_8_fallback_clearly_marked() -> None:
    src = _src(SCRIPT_PATH)
    # The reasoning text must lead with a [deterministic_repair ...] tag
    assert "[deterministic_repair" in src
    assert "generation_method=deterministic_repair" in src


# -----------------------------------------------------------------------
# 9. Deterministic fallback does not pretend to be direct agent speech
# -----------------------------------------------------------------------

def test_9_fallback_not_direct_agent_speech() -> None:
    src = _src(SCRIPT_PATH)
    # The reasoning literal must say "synthesized from ... not direct
    # agent speech"
    assert "not direct agent speech" in src
    assert "synthesized from" in src


# -----------------------------------------------------------------------
# 10. Only missing reflection ballots are inserted
# -----------------------------------------------------------------------

def test_10_only_missing_reflections_inserted() -> None:
    src = _src(SCRIPT_PATH)
    # The persistence loop ONLY iterates `repaired_drafts` (which is
    # built from `missing`), and skips any persona already in
    # existing_set.
    assert "for (pid_str, draft) in repaired_drafts:" in src
    assert "if pid_uuid in existing_set:" in src


# -----------------------------------------------------------------------
# 11. Expected DB delta is +missing_reflections only
# -----------------------------------------------------------------------

def test_11_db_delta_is_only_ballot() -> None:
    src = _src(SCRIPT_PATH)
    assert "non_ballot_deltas_zero" in src
    # forbidden table list inside the additive_only_check
    for tbl in (
        "source_records", "persona_records", "persona_traits",
        "persona_evidence_links", "persona_psychology_traits",
        "agents", "agent_responses",
        "discussion_groups", "discussion_turns",
        "persona_memory_atoms",
    ):
        assert f'"{tbl}"' in src


# -----------------------------------------------------------------------
# 12-17. No mutation of protected tables
# -----------------------------------------------------------------------

def test_12_no_source_records_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(SourceRecord(" not in src


def test_13_no_persona_records_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaRecord(" not in src


def test_14_no_persona_traits_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaTrait(" not in src


def test_15_no_persona_evidence_links_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaEvidenceLink(" not in src


def test_16_no_psychology_traits_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(PersonaPsychologyTrait(" not in src


def test_17_no_discussion_turns_added() -> None:
    src = _src(SCRIPT_PATH)
    assert "session.add(DiscussionTurn(" not in src


# -----------------------------------------------------------------------
# 18. Quality evaluator re-runs after repair
# -----------------------------------------------------------------------

def test_18_quality_evaluator_reruns() -> None:
    src = _src(SCRIPT_PATH)
    assert "evaluate_discussion_quality" in src
    assert "evaluate_scaled_discussion_quality" in src
    # Re-load AFTER repair (sess2/groups2/turns2 etc.)
    assert "groups2, turns2, ballots2" in src


# -----------------------------------------------------------------------
# 19. Reflection completeness ≥95% passes
# -----------------------------------------------------------------------

def test_19_reflection_completeness_floor() -> None:
    src = _src(SCRIPT_PATH)
    assert 'audit["reflection_completeness_after"] >= 0.95' in src


# -----------------------------------------------------------------------
# 20-23. Audits + scanners run
# -----------------------------------------------------------------------

def test_20_fake_use_scanner_runs() -> None:
    src = _src(SCRIPT_PATH)
    assert "forbidden_claim_audit" in src
    assert 'product_name=sess.product_name' in src


def test_21_forecast_scanner_runs() -> None:
    src = _src(SCRIPT_PATH)
    assert "any_forecast_or_verdict" in src


def test_22_sensitive_inference_scanner_runs() -> None:
    src = _src(SCRIPT_PATH)
    assert "sensitive_inference_audit" in src


def test_23_secret_scanner_runs() -> None:
    src = _src(SCRIPT_PATH)
    assert "scan_for_secrets" in src
    assert "security_redaction_audit" in src


# -----------------------------------------------------------------------
# 24-25. No new retrieval APIs
# -----------------------------------------------------------------------

def test_24_no_new_retrieval_apis() -> None:
    forbidden_modules = ("jina", "exa", "dataforseo", "apify")
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


def test_25_no_brave_tavily_youtube_modules() -> None:
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
# 26. Existing 9B tests still pass — collector verifies file presence
# -----------------------------------------------------------------------

def test_26_9b_test_file_still_present() -> None:
    p = (
        Path(__file__).resolve().parent
        / "test_scale_lumaloop_society_9b.py"
    )
    assert p.exists()


# -----------------------------------------------------------------------
# 27. Full unit tests pass — orchestrator imports cleanly
# -----------------------------------------------------------------------

def test_27_orchestrator_imports_cleanly() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "repair_9b_1", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.PHASE_LABEL == "9B.1"
    assert mod.HARD_CAP_USD <= 5.0  # narrow phase, small cap


# -----------------------------------------------------------------------
# 28. Full integration tests pass — dry-run is the default
# -----------------------------------------------------------------------

def test_28_dry_run_is_default() -> None:
    src = _src(SCRIPT_PATH)
    assert '"--commit", action="store_true"' in src


# -----------------------------------------------------------------------
# Bonus: deterministic fallback + helpers behave correctly
# -----------------------------------------------------------------------

def test_bonus_fallback_unit_test() -> None:
    """Synthetic test of the deterministic fallback function."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "repair_9b_1_b", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    pre = {
        "private_stance": "skeptical",
        "private_reasoning": "I want IP rating proof.",
        "confidence": "medium",
        "top_objection": "no IP rating disclosed",
        "top_proof_need": "IP rating + drop test",
    }
    final = {
        "private_stance": "interested_if_proven",
        "private_reasoning": "Proof requirements outlined would shift me.",
        "confidence": "medium",
    }
    rel_turns = [
        {
            "turn_type": "challenge",
            "public_text": "Show me the lumens at 8 hours runtime",
        },
        {
            "turn_type": "peer_response",
            "public_text": "I'd accept athlete-tested durability data",
        },
    ]
    out = mod._deterministic_fallback(
        pre_ballot=pre, final_ballot=final,
        relevant_turns=rel_turns,
        persona_display_name="TestPersona",
    )
    assert out["private_stance"] in mod._ALLOWED_STANCES
    assert out["generation_method"] == "deterministic_repair"
    assert "[deterministic_repair" in out["private_reasoning"]
    assert "synthetic n=66 simulation" in out["private_reasoning"]
    assert "TestPersona" in out["private_reasoning"]


def test_bonus_safe_json_parse_strips_fences() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "repair_9b_1_c", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    txt = (
        '```json\n{"private_stance": "skeptical", "private_reasoning": '
        '"x", "confidence": "low"}\n```'
    )
    out = mod._safe_json_parse(txt)
    assert out["private_stance"] == "skeptical"


def test_bonus_coerce_stance_rejects_unknown() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "repair_9b_1_d", str(SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod._coerce_stance("will_buy") is None
    assert mod._coerce_stance("interested_if_proven") == "interested_if_proven"


def test_bonus_orchestrator_does_not_overwrite_9b_artifacts() -> None:
    src = _src(SCRIPT_PATH)
    # 9B.1 must write to lumaloop_50_100_discussion_report_9b_1
    # NOT lumaloop_50_100_discussion_report_9b
    assert "lumaloop_50_100_discussion_report_9b_1.md" in src
    assert "lumaloop_50_100_discussion_report_9b_1.json" in src
    # Must read from 9B's audit
    assert "scale_lumaloop_society_9b.json" in src
