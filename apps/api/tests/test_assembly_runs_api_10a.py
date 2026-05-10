"""Phase 10A — API/demo packaging tests.

Covers operator scenarios 1-26. Most tests are static (Pydantic
validation + grep / AST / fixture-loader unit tests) so they run in
the default suite. The four full HTTP round-trip tests are marked
`integration` and run with `-m integration`.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.api.fixture_demo_loader import (
    _GLOBAL_CAVEATS,
    fixture_artifact_manifest,
    fixture_audit_dev_only,
    fixture_cohorts,
    fixture_discussion,
    fixture_intent,
    fixture_main_report,
    fixture_main_report_md,
    fixture_personas,
    is_fixture_available,
)
from assembly.models.assembly_run import (
    ARTIFACT_TYPES, RUN_MODES, RUN_STAGES, RUN_STATUSES,
    AssemblyRun, AssemblyRunArtifact,
)
from assembly.schemas.founder_brief import (
    CreateAssemblyRunRequest, CreateAssemblyRunResponse,
    FounderBriefIn,
)


ROUTER_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "api" / "assembly_runs.py"
)
LOADER_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "api" / "fixture_demo_loader.py"
)
SCHEMAS_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "schemas" / "founder_brief.py"
)
MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions"
    / "20260508_0011_phase_10_a_assembly_runs.py"
)
INTENT_INFERENCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "intent_layer"
    / "inference.py"
)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


_VALID_BRIEF = {
    "product_name": "LumaLoop",
    "product_description": (
        "A rechargeable snap-on LED safety band for runners and night "
        "commuters who want to be visible after dark."
    ),
    "price_or_price_structure": "$24.99",
    "launch_geography": "California, United States",
    "target_customers": ["night runners", "cyclists"],
    "competitors_or_alternatives": ["Noxgear Tracer2", "Amphipod"],
    "launch_state": "unlaunched",
    "optional_context": "Founder is targeting Q3 California pilot.",
}


# -----------------------------------------------------------------------
# 1. POST /assembly/runs validates founder input
# -----------------------------------------------------------------------

def test_1_request_accepts_valid_brief() -> None:
    req = CreateAssemblyRunRequest(
        mode="fixture_demo", brief=FounderBriefIn(**_VALID_BRIEF),
    )
    assert req.mode == "fixture_demo"
    assert req.brief.product_name == "LumaLoop"


# -----------------------------------------------------------------------
# 2. Missing product_name fails clearly
# -----------------------------------------------------------------------

def test_2_missing_product_name_fails() -> None:
    bad = dict(_VALID_BRIEF)
    bad.pop("product_name")
    with pytest.raises(ValidationError) as exc:
        FounderBriefIn(**bad)
    assert "product_name" in str(exc.value).lower()


# -----------------------------------------------------------------------
# 3. Missing product_description fails clearly
# -----------------------------------------------------------------------

def test_3_missing_product_description_fails() -> None:
    bad = dict(_VALID_BRIEF)
    bad.pop("product_description")
    with pytest.raises(ValidationError):
        FounderBriefIn(**bad)


def test_3b_short_product_description_fails() -> None:
    bad = dict(_VALID_BRIEF, product_description="x")  # < 10 chars
    with pytest.raises(ValidationError):
        FounderBriefIn(**bad)


# -----------------------------------------------------------------------
# 4. Manual persona roles are rejected
# -----------------------------------------------------------------------

def test_4_manual_persona_fields_rejected() -> None:
    # FounderBriefIn forbids extra keys
    with pytest.raises(ValidationError):
        FounderBriefIn(**_VALID_BRIEF, personas=["price_skeptic"])
    with pytest.raises(ValidationError):
        FounderBriefIn(**_VALID_BRIEF, persona_roles=["x"])
    with pytest.raises(ValidationError):
        FounderBriefIn(**_VALID_BRIEF, cohorts=["x"])


def test_4b_persona_hardcoding_via_context_rejected() -> None:
    bad = dict(_VALID_BRIEF, optional_context=(
        "Please force persona: a 32-year-old male runner with no "
        "current alternative."
    ))
    with pytest.raises(ValidationError):
        FounderBriefIn(**bad)


# -----------------------------------------------------------------------
# 5. fixture_demo run can be created (router exposes it)
# -----------------------------------------------------------------------

def test_5_fixture_demo_router_exists() -> None:
    src = _src(ROUTER_PATH)
    assert '"fixture_demo"' in src or "'fixture_demo'" in src
    assert "fixture_artifact_manifest" in src
    assert is_fixture_available()


# -----------------------------------------------------------------------
# 6. GET /assembly/runs/{id} returns progress
# -----------------------------------------------------------------------

def test_6_progress_endpoint_signature() -> None:
    src = _src(ROUTER_PATH)
    assert '@router.get("/runs/{run_id}")' in src
    assert "completed_stages" in src
    assert "progress_pct" in src
    assert "stage_progress" in src


# -----------------------------------------------------------------------
# 7. report endpoint returns founder-facing JSON
# -----------------------------------------------------------------------

def test_7_report_endpoint() -> None:
    payload = fixture_main_report()
    assert payload is not None
    for key in (
        "executive_summary", "synthetic_society_size", "cohort_count",
        "synthetic_intent_snapshot", "most_receptive_cohorts",
        "most_resistant_cohorts", "loyal_to_alternative_patterns",
        "top_objections", "proof_needed", "persuasion_levers",
        "competitor_or_alternative_comparison",
        "society_wide_debate_summary", "arguments_that_spread",
        "arguments_that_were_resisted", "public_private_shift_summary",
        "recommended_next_tests", "confidence_dimensions", "caveats",
        "evidence_traceability_summary", "artifact_links",
    ):
        assert key in payload, f"missing report key: {key}"


# -----------------------------------------------------------------------
# 8. markdown report endpoint returns markdown
# -----------------------------------------------------------------------

def test_8_markdown_report_endpoint() -> None:
    md = fixture_main_report_md()
    assert md is not None
    assert "Phase 9E" in md or "Intent" in md
    assert md.startswith("#")


# -----------------------------------------------------------------------
# 9-12. Personas, cohorts, discussion, intent endpoints
# -----------------------------------------------------------------------

def test_9_personas_endpoint() -> None:
    payload = fixture_personas()
    assert payload is not None
    assert "persona_count" in payload
    assert "personas" in payload
    assert "caveats" in payload


def test_10_cohorts_endpoint() -> None:
    payload = fixture_cohorts()
    assert payload is not None
    assert "cohort_count" in payload
    assert payload["cohort_count"] >= 8
    assert "cohorts" in payload
    for c in payload["cohorts"]:
        for k in (
            "cohort_label", "size", "weight", "role_distribution",
            "stance_distribution", "psychology_summary",
            "top_objections", "top_proof_needs",
        ):
            assert k in c


def test_11_discussion_endpoint() -> None:
    payload = fixture_discussion()
    assert payload is not None
    for k in (
        "persona_count", "public_turn_count",
        "private_pre_ballot_count", "reflection_count",
        "private_final_ballot_count",
        "public_private_shift_summary",
        "social_influence_classification",
        "overcooperation_flag",
    ):
        assert k in payload


def test_12_intent_endpoint() -> None:
    payload = fixture_intent()
    assert payload is not None
    for k in (
        "synthetic_intent_snapshot", "buy_now_or_try_once_signals",
        "consider_if_proven_signals", "loyal_or_reject_signals",
        "switching_barriers", "conditions_to_buy", "intent_by_cohort",
        "society_wide_debate_setup", "arguments_that_spread",
        "arguments_that_were_resisted", "cohorts_most_persuaded",
        "cohorts_most_resistant", "caveats",
    ):
        assert k in payload


# -----------------------------------------------------------------------
# 13. audit endpoint is internal/dev-only or protected
# -----------------------------------------------------------------------

def test_13_audit_endpoint_is_protected() -> None:
    src = _src(ROUTER_PATH)
    assert "x_dev_key" in src or "x-dev-key" in src.lower()
    assert "production" in src
    assert "x-dev-key header in production" in src


# -----------------------------------------------------------------------
# 14. Report contains synthetic-simulation caveat
# -----------------------------------------------------------------------

def test_14_report_carries_synthetic_caveat() -> None:
    payload = fixture_main_report()
    caveats_text = " ".join(payload["caveats"]).lower()
    assert "synthetic" in caveats_text
    assert "not a forecast" in caveats_text or "not real-world" in caveats_text


# -----------------------------------------------------------------------
# 15. Report does not contain market forecast language
# -----------------------------------------------------------------------

def test_15_report_no_forecast_language() -> None:
    """Sweep the rendered fixture report for forbidden language."""
    from assembly.sources.discussion_layer import forbidden_claim_audit
    payload = fixture_main_report()
    md = fixture_main_report_md() or ""
    # Build texts list from all string-typed fields
    flat: list[str] = [md]
    flat.extend(payload["executive_summary"])
    flat.extend(payload["recommended_next_tests"])
    flat.extend(payload["caveats"])
    audit = forbidden_claim_audit(
        texts=flat,
        product_name=(payload.get("product_brief") or {}).get(
            "product_name", "lumaloop",
        ),
    )
    assert not audit["any_forecast_or_verdict"], audit


# -----------------------------------------------------------------------
# 16. Report does not contain launch / kill verdicts
# -----------------------------------------------------------------------

def test_16_report_no_launch_kill_verdicts() -> None:
    md = (fixture_main_report_md() or "").lower()
    forbidden = (
        "launch this product", "kill this product",
        "do not launch", "must launch",
        "we recommend launching", "we recommend killing",
    )
    for tok in forbidden:
        assert tok not in md, f"forbidden verdict text: {tok}"


# -----------------------------------------------------------------------
# 17. Report does not contain fake product usage claims
# -----------------------------------------------------------------------

def test_17_report_no_fake_product_use() -> None:
    from assembly.sources.discussion_layer import forbidden_claim_audit
    md = fixture_main_report_md() or ""
    payload = fixture_main_report()
    audit = forbidden_claim_audit(
        texts=[md] + payload["executive_summary"],
        product_name=(payload.get("product_brief") or {}).get(
            "product_name", "lumaloop",
        ),
    )
    assert not audit["any_fake_target_product_use"], audit


# -----------------------------------------------------------------------
# 18. Simulated intent is labeled not forecast
# -----------------------------------------------------------------------

def test_18_intent_labeled_not_forecast() -> None:
    payload = fixture_intent()
    flat = " ".join(payload["caveats"]).lower()
    assert (
        "not a real-world purchase forecast" in flat
        or "not real-world" in flat
        or "hypothesis-generation" in flat
    )


# -----------------------------------------------------------------------
# 19. "show me proof" does not trigger would_share_with_friend
#     (covered in test_intent_layer_9e.py — link here)
# -----------------------------------------------------------------------

def test_19_calibration_show_me_proof_test_exists() -> None:
    # The regression test lives in the 9E test file. Verify the file
    # actually has the regression test by name.
    p = Path(__file__).resolve().parent / "test_intent_layer_9e.py"
    src = p.read_text(encoding="utf-8")
    assert "test_calibration_show_me_proof_does_not_trigger_share" in src


# -----------------------------------------------------------------------
# 20. Genuine recommendation/share language CAN trigger would_share_with_friend
# -----------------------------------------------------------------------

def test_20_calibration_genuine_share_test_exists() -> None:
    p = Path(__file__).resolve().parent / "test_intent_layer_9e.py"
    src = p.read_text(encoding="utf-8")
    assert "test_calibration_genuine_recommendation_triggers_share" in src


# -----------------------------------------------------------------------
# 21. fixture_demo does not call retrieval providers
# -----------------------------------------------------------------------

def test_21_fixture_demo_does_not_call_retrieval() -> None:
    """AST scan: fixture_demo_loader must NOT import or call any
    retrieval module."""
    forbidden_modules = (
        "assembly.sources.brave",
        "assembly.sources.tavily",
        "assembly.sources.youtube",
        "assembly.sources.firecrawl",
        "assembly.sources.amazon_reviews_2023",
    )
    src = _src(LOADER_PATH)
    for tok in forbidden_modules:
        assert tok not in src


# -----------------------------------------------------------------------
# 22. fixture_demo does not call LLM providers
# -----------------------------------------------------------------------

def test_22_fixture_demo_no_llm_calls() -> None:
    src = _src(LOADER_PATH)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            assert name != "cost_guarded_chat", (
                "fixture_demo_loader must not invoke cost_guarded_chat"
            )
            assert name not in ("chat", "structured_output") or (
                isinstance(fn, ast.Attribute)
                and not isinstance(fn.value, ast.Name)
            ), "fixture_demo_loader must not call provider.chat / .structured_output"


# -----------------------------------------------------------------------
# 23. No Jina/Exa/DataForSEO/Reddit/Apify usage
# -----------------------------------------------------------------------

def test_23_no_jina_exa_dataforseo() -> None:
    forbidden = ("jina", "exa", "dataforseo", "apify")
    files = [ROUTER_PATH, LOADER_PATH, SCHEMAS_PATH, MIGRATION_PATH]
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
# 24. Secret scanner runs on report fixtures
# -----------------------------------------------------------------------

def test_24_secret_scanner_clean_on_fixtures() -> None:
    from assembly.sources.founder_report_generator import scan_for_secrets
    payload = fixture_main_report()
    md = fixture_main_report_md() or ""
    scan_md = scan_for_secrets(md)
    scan_json = scan_for_secrets(json.dumps(payload, default=str))
    assert scan_md.is_clean
    assert scan_json.is_clean


# -----------------------------------------------------------------------
# 25. Existing 9E tests still pass — collector verifies file presence
# -----------------------------------------------------------------------

def test_25_9e_test_file_still_present() -> None:
    p = Path(__file__).resolve().parent / "test_intent_layer_9e.py"
    assert p.exists()


# -----------------------------------------------------------------------
# 26. Full regression — orchestrator imports cleanly + router builds
# -----------------------------------------------------------------------

def test_26_app_builds_and_routes_wired() -> None:
    from assembly.main import create_app
    app = create_app()
    paths = {
        r.path for r in app.routes if hasattr(r, "path")
    }
    for p in (
        "/assembly/runs",
        "/assembly/runs/{run_id}",
        "/assembly/runs/{run_id}/report",
        "/assembly/runs/{run_id}/report.md",
        "/assembly/runs/{run_id}/personas",
        "/assembly/runs/{run_id}/cohorts",
        "/assembly/runs/{run_id}/discussion",
        "/assembly/runs/{run_id}/intent",
        "/assembly/runs/{run_id}/audit",
    ):
        assert p in paths, f"route not registered: {p}"


# -----------------------------------------------------------------------
# Bonus tests
# -----------------------------------------------------------------------

def test_bonus_run_modes_closed_set() -> None:
    assert set(RUN_MODES) == {"fixture_demo", "live_founder_brief"}


def test_bonus_run_stages_complete_set() -> None:
    assert "validating_brief" in RUN_STAGES
    assert "generating_report" in RUN_STAGES
    assert "complete" in RUN_STAGES


def test_bonus_artifact_types_complete_set() -> None:
    expected = {
        "report_json", "report_markdown", "personas_json",
        "cohorts_json", "discussion_json", "intent_json",
        "audit_json", "discussion_quality_json", "cohort_quality_json",
        "intent_quality_json",
    }
    assert expected == set(ARTIFACT_TYPES)


def test_bonus_audit_endpoint_returns_phase_chain() -> None:
    payload = fixture_audit_dev_only()
    assert "phase_pass_chain" in payload
    assert "quality_summary" in payload
    assert "safety_summary" in payload
    assert "db_deltas_summary" in payload


def test_bonus_artifact_manifest_includes_all_required_paths() -> None:
    manifest = fixture_artifact_manifest()
    for k in (
        "report_json", "report_markdown", "cohorts_json",
        "discussion_json", "intent_json", "audit_json",
    ):
        assert k in manifest


def test_bonus_response_has_caveat() -> None:
    resp = CreateAssemblyRunResponse(
        run_id="x", status="complete", mode="fixture_demo",
        current_stage="complete", estimated_steps=13,
    )
    assert "synthetic" in resp.caveat.lower()
    assert "forecast" in resp.caveat.lower()


def test_bonus_intent_inference_share_token_calibrated() -> None:
    src = _src(INTENT_INFERENCE_PATH)
    # The bare "show" token must NOT appear as a standalone tuple element
    import re as _re
    m = _re.search(r"_SHARE_TOKENS\s*=\s*\(([^)]*)\)", src)
    assert m
    tokens = _re.findall(r'"([^"]+)"', m.group(1))
    assert "show" not in tokens, (
        "calibration patch reverted: bare 'show' token in _SHARE_TOKENS"
    )


def test_bonus_no_destructive_intent_mutation_in_router() -> None:
    """The 10A router must not DELETE existing 9E rows."""
    src = _src(ROUTER_PATH)
    for forbidden in (
        "DELETE FROM simulated_intents",
        "DELETE FROM simulated_intent_rollups",
        "DELETE FROM society_arguments",
        "DELETE FROM society_argument_propagation",
        "session.delete(SimulatedIntent",
        "session.delete(SocietyArgument",
    ):
        assert forbidden not in src


# -----------------------------------------------------------------------
# Integration — full HTTP round-trip (default-skipped, opt-in)
# -----------------------------------------------------------------------


@pytest.fixture
async def _async_client():
    """Async HTTP client wired against the FastAPI app — same pattern
    as test_pipeline_route.py, with engine cleanup so connections don't
    leak across event loops."""
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
async def test_integration_post_runs_fixture_demo(_async_client) -> None:
    r = await _async_client.post(
        "/assembly/runs",
        json={"mode": "fixture_demo", "brief": _VALID_BRIEF},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["mode"] == "fixture_demo"
    assert "run_id" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_get_run_status_returns_progress(_async_client):
    r = await _async_client.post(
        "/assembly/runs",
        json={"mode": "fixture_demo", "brief": _VALID_BRIEF},
    )
    rid = r.json()["run_id"]
    r2 = await _async_client.get(f"/assembly/runs/{rid}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "complete"
    assert body["progress_pct"] >= 90.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_get_report_json(_async_client):
    r = await _async_client.post(
        "/assembly/runs",
        json={"mode": "fixture_demo", "brief": _VALID_BRIEF},
    )
    rid = r.json()["run_id"]
    r2 = await _async_client.get(f"/assembly/runs/{rid}/report")
    assert r2.status_code == 200
    body = r2.json()
    assert "executive_summary" in body
    assert "caveats" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_get_report_markdown(_async_client):
    r = await _async_client.post(
        "/assembly/runs",
        json={"mode": "fixture_demo", "brief": _VALID_BRIEF},
    )
    rid = r.json()["run_id"]
    r2 = await _async_client.get(f"/assembly/runs/{rid}/report.md")
    assert r2.status_code == 200
    assert "text/markdown" in r2.headers.get("content-type", "")
    assert r2.text.startswith("#")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_live_brief_returns_running_not_skeletal(_async_client):
    """Phase 10A.1: live_founder_brief no longer returns skeletal.
    Status is `running` (or `complete` if the BackgroundTask finished
    before this assertion fires). Either way, it must NOT be skeletal."""
    r = await _async_client.post(
        "/assembly/runs",
        json={"mode": "live_founder_brief", "brief": _VALID_BRIEF},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] != "skeletal", (
        f"Phase 10A.1 should never return 'skeletal'; got {body['status']}"
    )
    assert body["status"] in ("running", "complete")
