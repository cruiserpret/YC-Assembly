"""Phase 10A.3 — live_founder_brief reliability + audit cleanup tests.

Covers:
  - Final-ballot repair gate (Part A) — tests 1-6
  - Audit / artifact wording cleanup (Part B) — tests 7-9
  - Retrieval provider escalation (Part C) — tests 10-13
  - Persona quality gates (Part D) — tests 14-19
  - Live API endpoints (Part F) — tests 20-22
  - Failure handling + retry hardening (Part E) — tests 23-24
  - Safety scanners (Part G) — tests 25-28
  - Regression: fixture_demo + 10A.2 fresh-mode default still work — 29-31
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from assembly.orchestration import (
    LiveFounderBriefOrchestrator, PIPELINE_STAGES,
)
from assembly.orchestration.live_evidence_pipeline import (
    _evaluate_tier1_thresholds, plan_live_evidence_queries,
    provider_keys_summary, run_live_retrieval,
)
from assembly.orchestration.live_quality_gates import (
    evaluate_persona_quality_gates, scan_fresh_live_artifacts_for_stale_wording,
    scan_user_facing_language,
    write_persona_quality_gates_artifact,
    write_wording_audit_artifact,
)
from assembly.orchestration.live_final_ballot_repair import (
    _coerce_stance, _safe_json_parse, repair_missing_final_ballots,
)


ORCH_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "orchestration" / "live_founder_brief.py"
)
EVIDENCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "orchestration" / "live_evidence_pipeline.py"
)
ROUTER_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "api" / "assembly_runs.py"
)
QUALITY_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "orchestration" / "live_quality_gates.py"
)
REPAIR_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "orchestration" / "live_final_ballot_repair.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _make_candidate(
    *, candidate_id: str, role: str = "use_case_focused_buyer",
    n_traits: int = 3, n_objections: int = 1, n_preferences: int = 1,
    src_ids: list[str] | None = None,
    snippet: str = "real evidence about the product category",
    reaction: str = "would likely compare to competitors first",
    provider: str = "brave_search",
):
    """Build a CompressedPersonaCandidate-shaped object for tests
    (uses SimpleNamespace so we don't need the real Pydantic model)."""
    return SimpleNamespace(
        candidate_id=candidate_id,
        normalized_primary_role=role,
        pre_normalization_role=role,
        segment_label=role,
        source_record_ids=src_ids or [f"live::brave::{candidate_id}"],
        evidence_summary=f"summary for {candidate_id}",
        evidence_snippets=[snippet],
        evidence_theme="live_evidence",
        source_provider_family=provider,
        inferred_traits=[
            {"trait_name": "interests", "trait_value": "topic A",
             "evidence_excerpt": "ev1", "confidence": "medium"},
            {"trait_name": "buying_constraints", "trait_value": "B",
             "evidence_excerpt": "ev2", "confidence": "medium"},
            {"trait_name": "trust_triggers", "trait_value": "C",
             "evidence_excerpt": "ev3", "confidence": "medium"},
        ][:n_traits],
        inferred_preferences=[
            f"prefers detailed comparison data {i}"
            for i in range(n_preferences)
        ],
        inferred_objections=[
            f"unclear pricing concerns level {i}"
            for i in range(n_objections)
        ],
        inferred_behaviors=[],
        hypothetical_target_product_reaction=reaction,
        confidence="medium",
        evidence_strength="moderate",
        quality_score=7.0,
        caveats=["live retrieval-driven persona"],
        simulation_usefulness_summary="useful",
        persistence_recommendation="DEFER",
        kept_reason="live_compression_simple",
    )


# -----------------------------------------------------------------------
# Part A — Final-ballot repair gate
# -----------------------------------------------------------------------


# 1. helper module exists with the public surface
def test_1_final_ballot_repair_module_exists():
    src = _src(REPAIR_PATH)
    assert "async def repair_missing_final_ballots" in src
    assert "deterministic_fallback" in src
    assert "completeness_after" in src
    assert "missing_persona_ids_after" in src


# 2. JSON parser handles markdown fences + plain JSON
def test_2_repair_safe_json_parse_handles_fences():
    plain = '{"private_stance": "skeptical"}'
    fenced = "```json\n" + plain + "\n```"
    assert _safe_json_parse(plain) == {"private_stance": "skeptical"}
    assert _safe_json_parse(fenced) == {"private_stance": "skeptical"}
    assert _safe_json_parse("not json at all") is None
    assert _safe_json_parse("") is None


# 3. stance coercion enforces the closed allowed set
def test_3_repair_coerce_stance_enforces_closed_set():
    assert _coerce_stance("skeptical") == "skeptical"
    assert _coerce_stance("interested_if_proven") == "interested_if_proven"
    assert _coerce_stance("definitely_buying") is None
    assert _coerce_stance(None) is None
    assert _coerce_stance(42) is None


# 4. orchestrator wires the final-ballot repair stage
def test_4_orchestrator_wires_final_ballot_repair():
    src = _src(ORCH_PATH)
    assert "repair_missing_final_ballots" in src
    assert "_FINAL_BALLOT_MIN_COMPLETENESS" in src
    # Stage hard-fails below threshold
    assert "final ballot completeness" in src.lower()


# 5. final-ballot repair writes its dedicated audit file
def test_5_orchestrator_writes_final_ballot_repair_artifact():
    src = _src(ORCH_PATH)
    assert "final_ballot_repair.json" in src


# 6. minimum completeness threshold is set to 95%
def test_6_minimum_completeness_threshold_is_95_percent():
    src = _src(ORCH_PATH)
    assert "_FINAL_BALLOT_MIN_COMPLETENESS = 0.95" in src


# -----------------------------------------------------------------------
# Part B — Stale-wording scanner
# -----------------------------------------------------------------------


# 7. wording scanner detects "reuse_existing_society mode"
def test_7_scanner_flags_stale_reuse_wording(tmp_path):
    (tmp_path / "test.json").write_text(
        '{"phase": "10a_3", "note": "reuse_existing_society mode is bad"}',
        encoding="utf-8",
    )
    audit = scan_fresh_live_artifacts_for_stale_wording(
        run_dir=tmp_path, is_dev_reuse=False,
    )
    assert audit["any_violations"] is True
    assert "test.json" in audit["violations_by_file"]


# 8. wording scanner detects 10A.1 phase tags
def test_8_scanner_flags_10a_1_phase_tags(tmp_path):
    (tmp_path / "doc.json").write_text(
        '{"phase": "10a_1_individual_simulation"}', encoding="utf-8",
    )
    audit = scan_fresh_live_artifacts_for_stale_wording(
        run_dir=tmp_path, is_dev_reuse=False,
    )
    assert audit["any_violations"]


# 9. wording scanner skips dev-reuse mode
def test_9_scanner_skips_dev_reuse(tmp_path):
    (tmp_path / "doc.json").write_text(
        '{"phase": "10a_1_dev_reuse"}', encoding="utf-8",
    )
    audit = scan_fresh_live_artifacts_for_stale_wording(
        run_dir=tmp_path, is_dev_reuse=True,
    )
    assert audit["skipped"] is True
    assert audit["any_violations"] is False


# 9b. clean fresh-mode artifacts produce zero violations
def test_9b_scanner_clean_fresh_artifacts(tmp_path):
    (tmp_path / "evidence_plan.json").write_text(
        json.dumps({
            "phase": "10a_3_evidence_plan",
            "mode": "live_founder_brief",
            "evidence_source": "live_retrieval",
        }),
        encoding="utf-8",
    )
    audit = scan_fresh_live_artifacts_for_stale_wording(
        run_dir=tmp_path, is_dev_reuse=False,
    )
    assert audit["any_violations"] is False


# 9c. excluded files (user input + audit's own file) are not scanned
def test_9c_scanner_excludes_user_input_files(tmp_path):
    # Even if user's brief contains "fixture", scanner ignores it
    (tmp_path / "live_founder_brief_input.json").write_text(
        '{"product_description": "uses a fixture for mounting"}',
        encoding="utf-8",
    )
    audit = scan_fresh_live_artifacts_for_stale_wording(
        run_dir=tmp_path, is_dev_reuse=False,
    )
    assert audit["any_violations"] is False
    assert "live_founder_brief_input.json" in audit["files_excluded"]


# -----------------------------------------------------------------------
# Part C — Retrieval provider escalation
# -----------------------------------------------------------------------


# 10. provider escalation policy exists in code
def test_10_provider_escalation_policy_exists():
    src = _src(EVIDENCE_PATH)
    assert "_evaluate_tier1_thresholds" in src
    assert "escalation_triggered" in src
    assert "escalation_reason" in src
    assert "tier_2_providers_attempted" in src


# 11. tier-1 below threshold triggers escalation
def test_11_tier1_low_count_triggers_escalation():
    items = [
        {"provider": "brave_search", "url": f"https://x.com/{i}",
         "snippet": "ok", "domain": "x.com"}
        for i in range(5)
    ]
    escalate, reason = _evaluate_tier1_thresholds(
        items=items, persona_count_target=21,
        min_raw_results=24, min_distinct_domains=6,
    )
    assert escalate is True
    assert reason and "tier_1_raw_count=5" in reason


# 12. tier-1 with sufficient diversity does NOT escalate
def test_12_tier1_sufficient_does_not_escalate():
    items = [
        {"provider": "brave_search" if i % 2 == 0 else "tavily_search",
         "url": f"https://example{i}.com", "snippet": "ok",
         "domain": f"example{i}.com"}
        for i in range(30)
    ]
    with patch(
        "assembly.orchestration.live_evidence_pipeline."
        "provider_keys_summary",
        return_value={
            "brave_search_api_key_configured": True,
            "tavily_api_key_configured": True,
            "youtube_data_api_key_configured": False,
            "firecrawl_api_key_configured": False,
            "anthropic_api_key_configured": True,
        },
    ):
        escalate, reason = _evaluate_tier1_thresholds(
            items=items, persona_count_target=21,
        )
    assert escalate is False
    assert reason is None


# 13. provider keys summary reports only booleans
def test_13_provider_keys_summary_only_booleans():
    summary = provider_keys_summary()
    for k, v in summary.items():
        assert isinstance(v, bool), f"{k} is not bool"
        assert k.endswith("_configured")


# 13b. retrieval audit captures escalation fields
def test_13b_retrieval_audit_captures_escalation_fields():
    with patch(
        "assembly.orchestration.live_evidence_pipeline."
        "provider_keys_summary",
        return_value={
            "brave_search_api_key_configured": False,
            "tavily_api_key_configured": False,
            "youtube_data_api_key_configured": False,
            "firecrawl_api_key_configured": False,
            "anthropic_api_key_configured": True,
        },
    ):
        items, audit = run_live_retrieval(queries=["q1", "q2"])
    assert items == []
    assert "escalation_triggered" in audit
    assert "escalation_reason" in audit
    assert audit["any_retrieval_provider_configured"] is False
    assert "providers_skipped" in audit


# -----------------------------------------------------------------------
# Part D — Persona quality gates
# -----------------------------------------------------------------------


# 14. quality gates enforce 21–30 persona count window
def test_14_quality_gates_enforce_count_range():
    too_few = [_make_candidate(candidate_id=f"c{i}") for i in range(10)]
    audit = evaluate_persona_quality_gates(
        compressed_candidates=too_few, accepted_evidence=[],
        target_brief_id="t", run_scope_id="run_live_t_xxx",
    )
    assert audit["all_gates_passed"] is False
    assert audit["gate_results"]["count_in_range"] is False


# 15. quality gates reject role concentration > 35%
def test_15_quality_gates_reject_role_concentration():
    cands = [
        _make_candidate(candidate_id=f"a{i}", role="competitor_user_x")
        for i in range(20)
    ] + [_make_candidate(candidate_id="b1", role="other")]
    audit = evaluate_persona_quality_gates(
        compressed_candidates=cands, accepted_evidence=[],
        target_brief_id="t", run_scope_id="run_live_t_xxx",
    )
    assert audit["all_gates_passed"] is False
    assert audit["gate_results"]["role_concentration_ok"] is False


# 16. duplicate persona signatures fail the no-duplicates gate
def test_16_quality_gates_reject_duplicates():
    same = "an identical evidence snippet shared by both candidates"
    dup_a = _make_candidate(
        candidate_id="d1", role="role_x", snippet=same,
    )
    dup_b = _make_candidate(
        candidate_id="d2", role="role_x", snippet=same,
    )
    others = [
        _make_candidate(candidate_id=f"c{i}", role=f"r{i}")
        for i in range(22)
    ]
    audit = evaluate_persona_quality_gates(
        compressed_candidates=[dup_a, dup_b, *others],
        accepted_evidence=[], target_brief_id="t",
        run_scope_id="run_live_t_xxx",
    )
    assert audit["gate_results"]["no_duplicates_ok"] is False


# 17. evidence-link coverage gate fires when src_ids missing
def test_17_quality_gates_evidence_link_coverage():
    missing = _make_candidate(
        candidate_id="m1", role="r_missing", src_ids=["unknown"],
    )
    others = [
        _make_candidate(candidate_id=f"c{i}", role=f"r{i}")
        for i in range(21)
    ]
    audit = evaluate_persona_quality_gates(
        compressed_candidates=[missing, *others],
        accepted_evidence=[], target_brief_id="t",
        run_scope_id="run_live_t_xxx",
    )
    assert audit["gate_results"]["evidence_link_coverage_ok"] is False


# 18. objection / trait-dimension diversity gates
def test_18_quality_gates_diversity_gates():
    # All candidates share a single objection bucket AND a single
    # trait dimension → both diversity gates fire.
    cands = [
        SimpleNamespace(
            candidate_id=f"c{i}",
            normalized_primary_role=f"r{i}",
            pre_normalization_role=f"r{i}",
            segment_label="r",
            source_record_ids=[f"s{i}"],
            evidence_summary="s",
            evidence_snippets=[f"snippet{i}" * 4],
            evidence_theme="theme",
            source_provider_family="brave_search",
            inferred_traits=[
                # All 22 candidates carry the same single dimension
                # name → trait_dimension_diversity = 1 < 2.
                {"trait_name": "interests", "trait_value": "v",
                 "evidence_excerpt": "e", "confidence": "medium"},
                {"trait_name": "role_or_context",
                 "trait_value": "B", "evidence_excerpt": "e",
                 "confidence": "medium"},
            ],
            inferred_preferences=[],
            inferred_objections=["price"],  # same bucket every time
            inferred_behaviors=[],
            hypothetical_target_product_reaction="x",
        )
        for i in range(22)
    ]
    audit = evaluate_persona_quality_gates(
        compressed_candidates=cands, accepted_evidence=[],
        target_brief_id="t", run_scope_id="r",
        min_objection_diversity=3, min_trait_dimension_diversity=3,
    )
    assert audit["gate_results"]["objection_diversity_ok"] is False
    assert audit["gate_results"]["trait_dimension_diversity_ok"] is False


# 19. persistence is rolled back when quality gates fail
def test_19_orchestrator_aborts_persistence_on_quality_failure():
    src = _src(ORCH_PATH)
    # The building_personas stage must call evaluate_persona_quality_gates
    # BEFORE persist_live_society, and raise if not all_gates_passed.
    eval_idx = src.find("evaluate_persona_quality_gates(")
    persist_idx = src.find("persist_live_society(")
    assert eval_idx > 0 and persist_idx > 0
    assert eval_idx < persist_idx, (
        "evaluate_persona_quality_gates must run before "
        "persist_live_society"
    )
    assert "all_gates_passed" in src
    assert "Personas were NOT persisted" in src


# 19b. happy path: well-formed candidates pass every gate
def test_19b_quality_gates_happy_path():
    cands = []
    roles = ["a", "b", "c", "d", "e", "f", "g"]
    for i in range(22):
        c = _make_candidate(
            candidate_id=f"c{i}",
            role=roles[i % len(roles)],
            snippet=f"unique snippet number {i} talking about topic",
        )
        c.inferred_objections = [f"objection bucket {i % 5}"]
        c.inferred_preferences = [f"proof bucket {i % 5}"]
        cands.append(c)
    audit = evaluate_persona_quality_gates(
        compressed_candidates=cands, accepted_evidence=[],
        target_brief_id="t", run_scope_id="run_live_t_x",
    )
    assert audit["all_gates_passed"] is True, audit["blocker_messages"]


# -----------------------------------------------------------------------
# Part F — Live API endpoints
# -----------------------------------------------------------------------


# 20. /audit endpoint returns live audit for live runs (no fixture leakage)
def test_20_audit_endpoint_serves_live_audit_for_live_runs():
    src = _src(ROUTER_PATH)
    audit_block = src[src.find("async def get_audit"):]
    audit_block = audit_block[:audit_block.find("\n\n\n")]
    assert "audit_kind" in audit_block
    assert "live_founder_brief" in audit_block
    # Must call _live_run_status_check for live runs
    assert "_live_run_status_check" in audit_block
    # Must NOT unconditionally return fixture audit
    assert audit_block.count("fixture_audit_dev_only()") == 1
    fixture_idx = audit_block.find("fixture_audit_dev_only()")
    fixture_mode_idx = audit_block.find('fixture_demo')
    assert fixture_mode_idx > 0 and fixture_mode_idx < fixture_idx


# 21. live endpoints raise if the live artifact is not on disk
def test_21_live_endpoints_raise_when_artifact_missing():
    src = _src(ROUTER_PATH)
    # Each non-fixture endpoint should raise 503 if artifact missing
    for endpoint in (
        "get_report_json", "get_personas", "get_cohorts",
        "get_discussion", "get_intent",
    ):
        block = src[src.find(f"async def {endpoint}"):]
        block = block[:block.find("\n\n\n")]
        assert "HTTPException" in block, f"{endpoint} missing 503 raise"
        assert "503" in block or "SERVICE_UNAVAILABLE" in block


# 22. failed runs surface error_message + failed_stage in /runs/{id}
def test_22_status_endpoint_surfaces_failed_stage():
    src = _src(ROUTER_PATH)
    status_block = src[src.find("async def get_run_status"):]
    status_block = status_block[:status_block.find("\n\n\n")]
    assert "failed_stage" in status_block
    assert "error_message" in status_block


# -----------------------------------------------------------------------
# Part E — Failure handling + retry hardening
# -----------------------------------------------------------------------


# 23. transient retry exists in discussion path
def test_23_transient_provider_retry_exists():
    discussion_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_discussion_pipeline.py"
    )
    src = _src(discussion_path)
    assert "call_with_retry" in src
    assert "max_attempts=3" in src or "max_attempts=" in src


# 24. final-ballot repair LLM ladder retries via cost_guarded_chat
def test_24_final_repair_uses_cost_guarded_chat():
    src = _src(REPAIR_PATH)
    assert "cost_guarded_chat" in src
    assert "_REPAIR_INSTRUCTION_STRICT" in src
    assert "_REPAIR_INSTRUCTION_STRICTER" in src
    assert "deterministic_fallback" in src


# -----------------------------------------------------------------------
# Part G — Safety scanners
# -----------------------------------------------------------------------


# 25. user-facing language scanner blocks market-percentage forecasts
def test_25_user_facing_blocks_percent_forecasts():
    audit = scan_user_facing_language(
        "12% of the market will buy this product within 6 months."
    )
    assert audit["any_violations"] is True
    assert any(
        f["label"] == "market-percentage forecast"
        for f in audit["findings"]
    )


# 26. user-facing language scanner blocks "launch this" / "kill this"
def test_26_user_facing_blocks_verdicts():
    a = scan_user_facing_language("launch this!")
    b = scan_user_facing_language("kill this product immediately")
    assert a["any_violations"] and b["any_violations"]


# 27. user-facing language scanner blocks fake usage claims
def test_27_user_facing_blocks_fake_usage_claims():
    audit = scan_user_facing_language(
        "Real buyers said this was great. Customers used this product daily."
    )
    assert audit["any_violations"] is True
    labels = {f["label"] for f in audit["findings"]}
    assert "fake real-buyer attribution" in labels


# 28. quality gates fake-use detection inside persona reaction
def test_28_quality_gates_block_fake_use_in_reaction():
    bad = _make_candidate(
        candidate_id="bad1", role="r_bad",
        reaction="I bought the product last month and loved it.",
    )
    others = [
        _make_candidate(candidate_id=f"c{i}", role=f"r{i}")
        for i in range(22)
    ]
    audit = evaluate_persona_quality_gates(
        compressed_candidates=[bad, *others],
        accepted_evidence=[], target_brief_id="t",
        run_scope_id="run_live_t_xx",
    )
    assert audit["gate_results"]["no_fake_product_users_ok"] is False
    assert "bad1" in audit["fake_use_candidate_ids"]


# 28b. allowed-language baseline: synthetic-society wording passes
def test_28b_user_facing_allows_synthetic_language():
    safe = (
        "In this synthetic run-scoped society, 19 personas expressed "
        "synthetic would_consider_if_proven intent. This is not a "
        "real-world forecast."
    )
    audit = scan_user_facing_language(safe)
    assert audit["any_violations"] is False


# -----------------------------------------------------------------------
# Regression — fixture_demo + 10A.2 contracts still work
# -----------------------------------------------------------------------


# 29. fixture_demo path is preserved in router
def test_29_fixture_demo_still_supported():
    src = _src(ROUTER_PATH)
    assert "fixture_demo" in src
    assert "is_fixture_available()" in src


# 30. 10A.2 fresh-mode default is preserved
def test_30_fresh_mode_default_preserved():
    orch = LiveFounderBriefOrchestrator(
        run_id=__import__("uuid").uuid4(),
    )
    assert orch._dev_reuse_existing_society is False


# 31. 13-stage pipeline order is intact (no stage added/removed)
def test_31_pipeline_stage_order_intact():
    expected = (
        "validating_brief", "planning_evidence",
        "retrieving_evidence", "scoring_evidence",
        "building_personas", "enriching_psychology",
        "running_individual_simulation", "running_group_discussion",
        "repairing_incomplete_outputs", "building_cohorts",
        "inferring_simulated_intent", "running_society_wide_debate",
        "generating_report",
    )
    assert PIPELINE_STAGES == expected


# 32. reflection_repair stage now wraps final-ballot repair (fresh mode)
def test_32_reflection_repair_now_calls_final_ballot_repair():
    src = _src(ORCH_PATH)
    repair_block = src[
        src.find("async def _stage_repairing_incomplete_outputs"):
    ]
    repair_block = repair_block[:repair_block.find("async def _stage_building_cohorts")]
    # Fresh mode should NOT just be a passthrough
    assert "repair_missing_final_ballots" in repair_block
    assert "_FINAL_BALLOT_MIN_COMPLETENESS" in repair_block


# 33. wording-audit artifact is emitted at the end of pipeline
def test_33_wording_audit_artifact_emitted():
    src = _src(ORCH_PATH)
    assert "scan_fresh_live_artifacts_for_stale_wording" in src
    assert "fresh_live_artifact_wording_audit.json" in src


# 34. user-facing language audit artifact is emitted
def test_34_user_facing_audit_artifact_emitted():
    src = _src(ORCH_PATH)
    assert "scan_user_facing_language" in src
    assert "user_facing_language_audit.json" in src


# 35. quality-gates artifact is emitted
def test_35_quality_gates_artifact_emitted():
    src = _src(ORCH_PATH)
    assert "write_persona_quality_gates_artifact" in src
    assert "persona_quality_gates.json" in src


# 36. all stale 10a_1_ phase tags removed from fresh stages
def test_36_no_stale_10a_1_phase_tags_in_fresh_stages():
    src = _src(ORCH_PATH)
    # Find all `"phase": "10a_1_..."` literal occurrences
    matches = re.findall(r'"phase":\s*"10a_1_[a-z_]+', src)
    assert matches == [], (
        f"stale 10a_1_ phase tags still present: {matches}"
    )
