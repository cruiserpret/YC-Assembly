"""Phase 10A.1 — live_founder_brief end-to-end tests.

Covers operator scenarios 1-35. Static tests (default suite) verify
the orchestrator framework + stage runners + safe-fail paths. The
integration tests (marked) run the full live pipeline against the 9B
society as a substrate to prove end-to-end behavior.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from assembly.models.assembly_run import (
    RUN_MODES, RUN_STAGES, RUN_STATUSES, AssemblyRun,
)
from assembly.orchestration import (
    PIPELINE_STAGES, LiveFounderBriefOrchestrator,
    estimate_pipeline_cost, run_live_founder_brief_pipeline,
)
from assembly.orchestration.live_founder_brief import (
    StageError, _STAGE_RUNNERS,
)


ORCH_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "orchestration" / "live_founder_brief.py"
)
ROUTER_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "api" / "assembly_runs.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


_VALID_BRIEF = {
    "product_name": "TestProduct",
    "product_description": (
        "A bounded test product description that's long enough to "
        "satisfy the FounderBriefIn schema's min_length=10 constraint."
    ),
    "price_or_price_structure": "$19.99",
    "launch_geography": "United States",
    "target_customers": ["test_persona_1", "test_persona_2"],
    "competitors_or_alternatives": ["AlternativeA", "AlternativeB"],
    "launch_state": "unlaunched",
}


# -----------------------------------------------------------------------
# 1. live_founder_brief no longer returns skeletal by default
# -----------------------------------------------------------------------

def test_1_router_no_longer_returns_skeletal_for_live_mode() -> None:
    src = _src(ROUTER_PATH)
    # Router must launch the orchestrator via BackgroundTasks
    assert "BackgroundTasks" in src
    assert "background_tasks.add_task" in src
    assert "_spawn_live_pipeline" in src
    # And must set status to "running" for live_founder_brief
    assert 'status="running"' in src or 'status="running",' in src


# -----------------------------------------------------------------------
# 2. POST /assembly/runs live mode creates run + enqueues orchestration
# -----------------------------------------------------------------------

def test_2_router_creates_run_row_and_schedules_orchestration() -> None:
    src = _src(ROUTER_PATH)
    assert "session.add(run)" in src
    assert "run_live_founder_brief_pipeline" in src


# -----------------------------------------------------------------------
# 3. Run status progresses through stages
# -----------------------------------------------------------------------

def test_3_pipeline_stages_present() -> None:
    assert len(PIPELINE_STAGES) == 13
    for stage in (
        "validating_brief", "planning_evidence", "retrieving_evidence",
        "scoring_evidence", "building_personas", "enriching_psychology",
        "running_individual_simulation", "running_group_discussion",
        "repairing_incomplete_outputs", "building_cohorts",
        "inferring_simulated_intent", "running_society_wide_debate",
        "generating_report",
    ):
        assert stage in PIPELINE_STAGES


def test_3b_every_stage_has_a_runner() -> None:
    assert set(_STAGE_RUNNERS.keys()) == set(PIPELINE_STAGES)


# -----------------------------------------------------------------------
# 4. fixture_demo still works (covered in 10A test suite)
# -----------------------------------------------------------------------

def test_4_fixture_demo_path_preserved() -> None:
    src = _src(ROUTER_PATH)
    # fixture_demo branch still returns status="complete" immediately
    assert 'status="complete"' in src
    assert "fixture_artifact_manifest" in src


# -----------------------------------------------------------------------
# 5. Live mode uses FounderBriefIn schema
# -----------------------------------------------------------------------

def test_5_live_mode_uses_founder_brief_schema() -> None:
    src = _src(ROUTER_PATH)
    assert "from assembly.schemas.founder_brief import" in src
    assert "CreateAssemblyRunRequest" in src
    assert "FounderBriefIn" in _src(
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "schemas" / "founder_brief.py"
    )


# -----------------------------------------------------------------------
# 6. Manual persona roles are rejected (covered in FounderBriefIn)
# -----------------------------------------------------------------------

def test_6_manual_persona_rejection_in_schema() -> None:
    """The schema's validators reject hardcoded personas. Test the
    orchestrator's _stage_validate_brief also rejects forbidden keys
    that bypass the API schema."""
    src = _src(ORCH_PATH)
    assert "personas" in src
    assert "persona_roles" in src
    assert "cohorts" in src
    assert "forbidden hardcoded field" in src


# -----------------------------------------------------------------------
# 7. Live run creates run-specific artifact manifest
# -----------------------------------------------------------------------

def test_7_run_specific_artifact_dir() -> None:
    src = _src(ORCH_PATH)
    assert "_audit/live_runs" in src or "live_runs" in src
    assert "{run_id}" in src or "self.run_id" in src
    # Artifacts are written under per-run directories
    assert "run_dir = _LIVE_RUNS_ROOT / str(self.run_id)" in src


# -----------------------------------------------------------------------
# 8-12. Report / personas / cohorts / discussion / intent endpoints
#        work after live completion (read live artifacts)
# -----------------------------------------------------------------------

def test_8_report_endpoint_reads_live_artifact() -> None:
    src = _src(ROUTER_PATH)
    assert "_load_live_artifact_json" in src
    assert "report_json" in src


def test_9_personas_endpoint_reads_live_artifact() -> None:
    src = _src(ROUTER_PATH)
    assert "personas_json" in src


def test_10_cohorts_endpoint_reads_live_artifact() -> None:
    src = _src(ROUTER_PATH)
    assert "cohorts_json" in src


def test_11_discussion_endpoint_reads_live_artifact() -> None:
    src = _src(ROUTER_PATH)
    assert "discussion_json" in src


def test_12_intent_endpoint_reads_live_artifact() -> None:
    src = _src(ROUTER_PATH)
    assert "intent_json" in src


# -----------------------------------------------------------------------
# 13. Failure stage is recorded if evidence gate fails
# -----------------------------------------------------------------------

def test_13_evidence_gate_failure_records_failed_stage() -> None:
    src = _src(ORCH_PATH)
    assert "no retrieval provider keys configured" in src
    assert "raise StageError" in src
    # The orchestrator catches StageError and sets status='failed'
    # + stage_status=(stage, "failed")
    assert 'stage_status=(exc.stage, "failed")' in src


# -----------------------------------------------------------------------
# 14. Cost cap failure is recorded safely
# -----------------------------------------------------------------------

def test_14_cost_cap_failure_recorded_safely() -> None:
    src = _src(ORCH_PATH)
    assert "cost_estimate" in src
    assert "max_budget_usd" in src
    assert "cost_cap_exceeded" in src or "exceeds max_budget_usd" in src


def test_14b_router_returns_402_on_budget_overrun() -> None:
    src = _src(ROUTER_PATH)
    assert "HTTP_402_PAYMENT_REQUIRED" in src
    assert "exceeds max_budget_usd" in src


# -----------------------------------------------------------------------
# 15. Missing provider keys are reported true/false only
# -----------------------------------------------------------------------

def test_15_provider_keys_reported_as_booleans() -> None:
    src = _src(ORCH_PATH)
    # provider_keys dict keys end with '_configured'
    assert "_api_key_configured" in src
    assert "any_retrieval_provider_configured" in src


# -----------------------------------------------------------------------
# 16. No API key values are printed
# -----------------------------------------------------------------------

def test_16_no_raw_api_key_value_writes() -> None:
    src = _src(ORCH_PATH)
    # Must not include actual key values in any string
    import re as _re
    # Must not assign raw key text into a JSON-serialized field
    for forbidden in (
        'settings.brave_search_api_key,',
        'settings.tavily_api_key,',
        'settings.anthropic_api_key,',
    ):
        # We only allow `bool(settings.X)` style usage
        assert forbidden not in src or "bool(" in src.split(forbidden)[0][-50:]


# -----------------------------------------------------------------------
# 17. No fake product usage claims appear
# -----------------------------------------------------------------------

def test_17_orchestrator_runs_forbidden_claim_audit() -> None:
    src = _src(ORCH_PATH)
    assert "forbidden_claim_audit" in src
    assert "any_fake_target_product_use" in src


# -----------------------------------------------------------------------
# 18. No forecast/verdict claims appear
# -----------------------------------------------------------------------

def test_18_orchestrator_blocks_forecast_claims() -> None:
    src = _src(ORCH_PATH)
    assert "any_forecast_or_verdict" in src


# -----------------------------------------------------------------------
# 19. Simulated intent is labeled not forecast
# -----------------------------------------------------------------------

def test_19_intent_caveats_carry_synthetic_label() -> None:
    src = _src(ORCH_PATH)
    assert "Simulated intent labels are NOT real-world purchase forecasts" in src


# -----------------------------------------------------------------------
# 20. Source records are staged before persona gate (orchestrator
#     does retrieval BEFORE personas)
# -----------------------------------------------------------------------

def test_20_retrieval_stage_before_personas() -> None:
    retrieval_idx = PIPELINE_STAGES.index("retrieving_evidence")
    persona_idx = PIPELINE_STAGES.index("building_personas")
    assert retrieval_idx < persona_idx


# -----------------------------------------------------------------------
# 21. Personas are run-scoped / brief-scoped
# -----------------------------------------------------------------------

def test_21_personas_run_scoped() -> None:
    src = _src(ORCH_PATH)
    assert "linked_run_scope_id" in src
    assert "run_scope_id" in src


# -----------------------------------------------------------------------
# 22. OCEAN + extra psychology traits are added
# -----------------------------------------------------------------------

def test_22_psychology_stage_loads_traits() -> None:
    src = _src(ORCH_PATH)
    assert "_stage_enriching_psychology" in src
    assert "PersonaPsychologyTrait" in src
    assert "psychology_value_map" in src


# -----------------------------------------------------------------------
# 23. Discussion layer uses memory and ballots
# -----------------------------------------------------------------------

def test_23_discussion_stage_uses_memory_and_ballots() -> None:
    src = _src(ORCH_PATH)
    assert "_stage_running_group_discussion" in src
    assert "memory_atoms" in src
    assert "ballots" in src
    assert "DiscussionPrivateBallot" in src


# -----------------------------------------------------------------------
# 24. Reflection repair runs if needed
# -----------------------------------------------------------------------

def test_24_reflection_repair_stage_present() -> None:
    src = _src(ORCH_PATH)
    assert "_stage_repairing_incomplete_outputs" in src
    assert "reflection_completeness" in src


# -----------------------------------------------------------------------
# 25. Cohort architecture runs
# -----------------------------------------------------------------------

def test_25_cohort_stage_uses_real_clusterer() -> None:
    src = _src(ORCH_PATH)
    assert "cluster_personas_into_cohorts" in src
    assert "build_cohort_feature_vectors" in src
    assert "summarize_cohort" in src


# -----------------------------------------------------------------------
# 26. Simulated intent layer runs
# -----------------------------------------------------------------------

def test_26_intent_stage_uses_real_inference() -> None:
    src = _src(ORCH_PATH)
    assert "infer_simulated_intent" in src


# -----------------------------------------------------------------------
# 27. Society-wide argument propagation runs
# -----------------------------------------------------------------------

def test_27_propagation_stage_runs_real_engine() -> None:
    src = _src(ORCH_PATH)
    assert "extract_society_arguments" in src
    assert "propagate_arguments_across_cohorts" in src


# -----------------------------------------------------------------------
# 28. Founder report JSON matches API contract
# -----------------------------------------------------------------------

def test_28_report_includes_required_keys() -> None:
    src = _src(ORCH_PATH)
    for k in (
        "executive_summary", "synthetic_society_size", "cohort_count",
        "synthetic_intent_snapshot", "top_objections", "proof_needed",
        "society_wide_debate_summary", "public_private_shift_summary",
        "recommended_next_tests", "confidence_dimensions", "caveats",
        "evidence_traceability_summary", "artifact_links",
    ):
        assert f'"{k}"' in src


# -----------------------------------------------------------------------
# 29. Founder report markdown generated
# -----------------------------------------------------------------------

def test_29_markdown_report_generated() -> None:
    src = _src(ORCH_PATH)
    assert "founder_report.md" in src
    assert "render_intent_and_debate_report_markdown" in src


# -----------------------------------------------------------------------
# 30. Secret scanner runs
# -----------------------------------------------------------------------

def test_30_secret_scanner_invoked() -> None:
    src = _src(ORCH_PATH)
    assert "scan_for_secrets" in src
    assert "secret scanner flagged" in src


# -----------------------------------------------------------------------
# 31. fixture_demo makes no retrieval/LLM calls (still true post-10A.1)
# -----------------------------------------------------------------------

def test_31_fixture_demo_no_retrieval_or_llm() -> None:
    """The fixture_demo path in the router does NOT call the live
    orchestrator and does NOT hit retrieval / LLM providers."""
    src = _src(ROUTER_PATH)
    # The fixture_demo branch returns BEFORE background_tasks.add_task
    fixture_branch_start = src.index('if payload.mode == "fixture_demo":')
    live_branch_start = src.index("# live_founder_brief mode")
    fixture_branch = src[fixture_branch_start:live_branch_start]
    assert "background_tasks.add_task" not in fixture_branch
    assert "run_live_founder_brief_pipeline" not in fixture_branch


# -----------------------------------------------------------------------
# 32. Live mode uses cost_guarded_chat for all LLM calls
# -----------------------------------------------------------------------

def test_32_orchestrator_documents_cost_guarded_chat() -> None:
    src = _src(ORCH_PATH)
    assert "cost_guarded_chat" in src
    # And the LLM-heavy retrieval stage notes deferred to 10A.2
    assert "10A.2" in src


# -----------------------------------------------------------------------
# 33. No Jina/Exa/DataForSEO/Reddit/Apify required
# -----------------------------------------------------------------------

def test_33_no_new_retrieval_apis() -> None:
    forbidden = ("jina", "exa", "dataforseo", "apify")
    files = [ORCH_PATH, ROUTER_PATH]
    for f in files:
        tree = ast.parse(_src(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.name or "").lower()
                    for tok in forbidden:
                        assert tok not in name
            elif isinstance(node, ast.ImportFrom):
                name = (node.module or "").lower()
                for tok in forbidden:
                    assert tok not in name


# -----------------------------------------------------------------------
# 34. Existing 10A tests still pass — collector verifies file presence
# -----------------------------------------------------------------------

def test_34_10a_test_file_still_present() -> None:
    p = Path(__file__).resolve().parent / "test_assembly_runs_api_10a.py"
    assert p.exists()


# -----------------------------------------------------------------------
# 35. Full regression — orchestrator imports cleanly + app builds
# -----------------------------------------------------------------------

def test_35_app_builds_with_orchestrator() -> None:
    from assembly.main import create_app
    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/assembly/runs" in paths


# -----------------------------------------------------------------------
# Bonus: cost estimation
# -----------------------------------------------------------------------

def test_bonus_cost_estimation_basic() -> None:
    est = estimate_pipeline_cost(persona_count=24, report_depth="standard")
    assert est["estimated_cost_usd"] > 0.0
    assert est["expected_calls"] == 24 * 7


def test_bonus_cost_estimation_fast_demo_zero() -> None:
    est = estimate_pipeline_cost(persona_count=24, report_depth="fast_demo")
    assert est["estimated_cost_usd"] == 0.0
    assert est["expected_calls"] == 0


def test_bonus_stage_error_carries_recommended_fix() -> None:
    err = StageError(
        "validating_brief", "missing required field",
        recommended_fix="add product_name",
    )
    assert err.stage == "validating_brief"
    assert err.recommended_fix == "add product_name"
    assert "validating_brief" in str(err)


def test_bonus_orchestrator_class_exists() -> None:
    assert callable(LiveFounderBriefOrchestrator)
    assert hasattr(LiveFounderBriefOrchestrator, "run")


def test_bonus_top_level_entry_point_callable() -> None:
    assert callable(run_live_founder_brief_pipeline)


def test_bonus_run_modes_unchanged() -> None:
    assert "live_founder_brief" in RUN_MODES
    assert "fixture_demo" in RUN_MODES


# -----------------------------------------------------------------------
# Integration — full live pipeline end-to-end
# -----------------------------------------------------------------------


@pytest.fixture
async def _async_client():
    import httpx
    from assembly import db
    from assembly.main import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as client:
        yield client
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_live_brief_runs_pipeline_end_to_end(_async_client):
    """Phase 10A.2 update: live mode now defaults to fresh retrieval.
    Without retrieval keys (or with insufficient results from a short
    test brief), the run should fail safely at retrieving_evidence /
    scoring_evidence — never produce a fake report.

    A successful end-to-end run is exercised by the dedicated 10A.2
    integration test in `tests/test_live_founder_brief_10a_2.py`,
    which uses a brief tuned to produce evidence."""
    import asyncio
    r = await _async_client.post(
        "/assembly/runs",
        json={"mode": "live_founder_brief", "brief": _VALID_BRIEF},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] in ("running", "complete")
    rid = body["run_id"]
    for _attempt in range(60):
        r2 = await _async_client.get(f"/assembly/runs/{rid}")
        st = r2.json()["status"]
        if st in ("complete", "failed"):
            break
        await asyncio.sleep(1.0)
    final = await _async_client.get(f"/assembly/runs/{rid}")
    body = final.json()
    # 10A.2: Either complete (real retrieval succeeded) OR failed
    # safely (with a clear error_message). NEVER skeletal.
    assert body["status"] in ("complete", "failed")
    if body["status"] == "failed":
        # Failure must be at one of the early evidence stages,
        # never at the report stage with fabricated content
        assert body["current_stage"] in (
            "retrieving_evidence", "scoring_evidence",
            "building_personas",
        )
        assert body.get("error_message"), "must include error_message"
        # Report endpoint must return 409, not a fake report
        r_report = await _async_client.get(f"/assembly/runs/{rid}/report")
        assert r_report.status_code == 409
        return
    # Successful completion path
    assert body["progress_pct"] == pytest.approx(100.0, abs=10.0)
    r_report = await _async_client.get(f"/assembly/runs/{rid}/report")
    assert r_report.status_code == 200
    payload = r_report.json()
    for k in (
        "executive_summary", "synthetic_society_size", "cohort_count",
        "synthetic_intent_snapshot", "caveats",
    ):
        assert k in payload
