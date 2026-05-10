"""Phase 8.5F — founder-facing report generator tests.

Operator scenarios 1-31 covered. NO LLM, NO retrieval, NO DB writes
from the test file itself. Tests are unit tests over the
deterministic aggregator + universal secret scanner + report-quality
evaluator + a static-grep over the dry-run script.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

from assembly.sources.founder_report_generator import (
    FounderReport, aggregate_founder_report, evaluate_report_quality,
    render_markdown_report, scan_for_secrets,
)
from assembly.sources.founder_report_generator.aggregator import (
    _classify_proof_kind, _classify_severity,
    _objection_to_founder_action, _persona_role_to_phrase,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "generate_founder_report_8_5f.py"
)
PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "founder_report_generator"
)


# ---------------------------------------------------------------------------
# Synthetic 8.5E-shaped inputs
# ---------------------------------------------------------------------------


def _synthetic_simulation_audit() -> dict[str, Any]:
    """Audit shaped exactly like the 8.5E real audit, but with
    synthetic data so the test is brand-agnostic."""
    return {
        "phase": "8_5e_strideshield_run_scoped_simulation",
        "simulation_id": "00000000-0000-0000-0000-000000000abc",
        "run_scope_id": "run_test_xyz",
        "founder_brief": {
            "product_name": "TestProduct",
            "product_description": "x",
            "price_or_price_structure": "$9.99",
            "launch_geography": "California",
            "target_customers": ["runners"],
            "competitors": [
                "Brand A", "Brand B", "Brand C",
            ],
            "launch_state": "unlaunched",
        },
        "launch_state": "unlaunched",
        "input_persona_count": 7,
        "input_persona_ids": [f"p{i}" for i in range(7)],
        "input_persona_summary": [
            {
                "persona_id": f"p{i}",
                "display_name": f"Persona-{i}",
                "normalized_primary_role": (
                    "competitor_user_brand_a" if i < 3
                    else "competitor_user_brand_b" if i < 6
                    else "price_skeptic"
                ),
                "compressed_candidate_id": f"cand-{i}",
                "source_provider_family": (
                    "brave_search" if i % 2 else "amazon_reviews_2023_local"
                ),
                "evidence_theme": f"competitor::brand_{'a' if i<3 else 'b'}",
                "trait_count": 2, "evidence_link_count": 2,
                "source_record_count": 1,
            }
            for i in range(7)
        ],
        "traits_loaded_count": 14,
        "evidence_links_loaded_count": 14,
        "source_records_loaded_count": 7,
        "agents_created_count": 7,
        "rounds_completed": 7,
        "per_round_outputs": [
            {
                "agent_persona_id": f"p{i}",
                "display_name": f"Persona-{i}",
                "compressed_candidate_id": f"cand-{i}",
                "normalized_primary_role": (
                    "competitor_user_brand_a" if i < 3
                    else "competitor_user_brand_b" if i < 6
                    else "price_skeptic"
                ),
                "round_type": "final_stance",
                "round_number": 7,
                "stance": "interested_if_proven",
                "reasoning": (
                    "I currently use Brand A for friction. "
                    "Brand A tends to dry up after long runs."
                ),
                "objections": [
                    {
                        "text": (
                            "at $9.99 the value vs Brand A needs proof "
                            "from a comparison test"
                        ),
                        "category": "price",
                    },
                ],
                "persuasion_levers": [
                    {
                        "text": (
                            "side-by-side durability evidence "
                            "vs Brand A over 12+ miles"
                        ),
                        "category": "social_proof",
                    },
                ],
                "competitor_mentions": ["Brand A", "Brand B"],
                "shift_from_previous": None,
                "forbidden_claim_audit": [],
                "raw_text": "...",
            }
            for i in range(7)
        ],
        "final_stance_distribution": {"interested_if_proven": 7},
        "top_objections": [
            {
                "text": (
                    "at $9.99 the value vs Brand A needs proof "
                    "from a comparison test"
                ),
                "count": 7,
            },
            {
                "text": "Brand B already covers this format",
                "count": 3,
            },
            {
                "text": "claims need backing data",
                "count": 2,
            },
        ],
        "top_persuasion_levers": [
            {
                "text": (
                    "side-by-side durability evidence "
                    "vs Brand A over 12+ miles"
                ),
                "count": 7,
            },
            {
                "text": "named runner testimonials",
                "count": 4,
            },
        ],
        "competitor_comparison_summary": [
            {"competitor": "Brand A", "mentions": 21},
            {"competitor": "Brand B", "mentions": 14},
            {"competitor": "Brand C", "mentions": 6},
        ],
        "forbidden_claim_audit": {
            "fake_target_product_use_count": 0,
            "forecast_or_verdict_count": 0,
            "any_fake_target_product_use": False,
            "any_forecast_or_verdict": False,
        },
        "source_persona_tables_unchanged": True,
        "db_delta_summary": {
            "simulations": 1, "agents": 7,
            "simulation_rounds": 7, "agent_responses": 49,
            "debate_turns": 0,
            "source_records": 0, "persona_records": 0,
            "persona_traits": 0, "persona_evidence_links": 0,
        },
        "cost_summary": {
            "calls": 49, "model_used": "claude-sonnet-4-6",
        },
        "ready_for_founder_report_phase": True,
        "quality_evaluator_result": {
            "aggregate_score": 0.95,
            "ready_state": "READY_FOR_FOUNDER_REPORT",
            "anti_fake_claim_score": 1.0,
            "stance_validity_score": 1.0,
        },
    }


def _synthetic_quality_audit() -> dict[str, Any]:
    return {
        "phase": "8_5e_strideshield_run_scoped_simulation_quality",
        "simulation_id": "00000000-0000-0000-0000-000000000abc",
        "scores": {
            "aggregate_score": 0.95,
            "ready_state": "READY_FOR_FOUNDER_REPORT",
            "anti_fake_claim_score": 1.0,
            "stance_validity_score": 1.0,
            "caveat_integrity_score": 1.0,
            "evidence_traceability_score": 1.0,
        },
    }


def _build_full_report() -> tuple[FounderReport, str]:
    sim = _synthetic_simulation_audit()
    qual = _synthetic_quality_audit()
    report = aggregate_founder_report(
        simulation_audit=sim, quality_audit=qual,
    )
    md = render_markdown_report(report)
    return report, md


# ---------------------------------------------------------------------------
# 1 + 2. Script reads 8.5E sim + quality audits
# ---------------------------------------------------------------------------


def test_script_reads_simulation_audit() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "strideshield_simulation_8_5e.json" in src


def test_script_reads_quality_audit() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "strideshield_simulation_quality_8_5e.json" in src


# ---------------------------------------------------------------------------
# 3 + 4. Script refuses if ready_for_founder_report_phase=false /
#  quality NOT_READY
# ---------------------------------------------------------------------------


def test_script_validate_inputs_refuses_when_not_ready() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("p_8_5f", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    sim = _synthetic_simulation_audit()
    sim["ready_for_founder_report_phase"] = False
    ok, blockers = mod._validate_inputs(
        sim=sim, qual=_synthetic_quality_audit(),
    )
    assert ok is False
    assert any("ready_for_founder_report_phase" in b for b in blockers)


def test_script_validate_inputs_refuses_when_quality_not_ready() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("p_8_5f", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    qual = _synthetic_quality_audit()
    qual["scores"]["ready_state"] = "NOT_READY"
    ok, blockers = mod._validate_inputs(
        sim=_synthetic_simulation_audit(), qual=qual,
    )
    assert ok is False
    assert any("NOT_READY" in b for b in blockers)


# ---------------------------------------------------------------------------
# 5-12. All required report sections present
# ---------------------------------------------------------------------------


def test_report_includes_executive_summary() -> None:
    report, _ = _build_full_report()
    assert len(report.executive_summary) >= 5
    assert all(isinstance(b, str) for b in report.executive_summary)


def test_report_includes_simulated_audience_snapshot() -> None:
    report, _ = _build_full_report()
    assert len(report.simulated_audience_snapshot) == 7
    for p in report.simulated_audience_snapshot:
        assert p.display_name
        assert p.compressed_candidate_id
        assert p.normalized_primary_role
        assert p.why_included


def test_report_includes_top_objections() -> None:
    report, _ = _build_full_report()
    assert len(report.top_objections) >= 1
    for o in report.top_objections:
        assert o.title
        assert o.severity in ("high", "medium", "low")
        assert o.founder_action


def test_report_includes_persuasion_levers() -> None:
    report, _ = _build_full_report()
    assert len(report.top_persuasion_levers) >= 1
    for l in report.top_persuasion_levers:
        assert l.title
        assert l.suggested_founder_change


def test_report_includes_competitor_comparison() -> None:
    report, _ = _build_full_report()
    assert len(report.competitor_comparison) >= 1
    competitor_names = {c.competitor for c in report.competitor_comparison}
    # Universal: only brief competitors should appear
    assert all(
        c in {"Brand A", "Brand B", "Brand C"}
        for c in competitor_names
    )


def test_report_includes_proof_needed() -> None:
    report, _ = _build_full_report()
    assert len(report.proof_needed) >= 1
    for p in report.proof_needed:
        assert p.proof_kind
        assert p.suggested_founder_assets


def test_report_includes_positioning_recommendations() -> None:
    report, _ = _build_full_report()
    assert len(report.positioning_recommendations) >= 1
    for r in report.positioning_recommendations:
        assert r.angle_label
        assert r.test_idea


def test_report_includes_what_to_test_next() -> None:
    report, _ = _build_full_report()
    assert len(report.what_to_test_next) >= 1
    for t in report.what_to_test_next:
        assert t.test_label
        assert t.expected_signal


# ---------------------------------------------------------------------------
# 13. Required caveats present
# ---------------------------------------------------------------------------


def test_required_caveats_present() -> None:
    report, md = _build_full_report()
    blob = " | ".join(report.caveats).lower()
    md_low = md.lower()
    needed_keywords = (
        ("micro-simulation", "n=7"),
        ("not a forecast",),
        ("not a market verdict",),
        ("not representative",),
        ("run-scoped",),
        ("synthetic",),
        ("unlaunched",),
        ("no first-party",),
        ("amazon",),
    )
    for keyset in needed_keywords:
        assert all(k in blob or k in md_low for k in keyset), (
            f"missing caveat keywords: {keyset}"
        )


# ---------------------------------------------------------------------------
# 14. No buy/adoption percentages in report
# ---------------------------------------------------------------------------


def test_report_does_not_contain_buy_or_adoption_percentages() -> None:
    report, md = _build_full_report()
    blob = "\n".join([
        md,
        " | ".join(report.executive_summary),
        " | ".join(report.overall_reaction),
    ])
    forbidden_patterns = (
        re.compile(r"\b\d{1,3}(\.\d+)?% (will|would) buy", re.IGNORECASE),
        re.compile(r"\b\d{1,3}(\.\d+)?% of the market", re.IGNORECASE),
        re.compile(r"\b\d{1,3}(\.\d+)?% adoption", re.IGNORECASE),
        re.compile(r"\bconversion rate (will|would|is) \d", re.IGNORECASE),
    )
    for pat in forbidden_patterns:
        assert pat.search(blob) is None, f"matched: {pat.pattern}"


# ---------------------------------------------------------------------------
# 15. No launch / kill verdicts
# ---------------------------------------------------------------------------


def test_report_does_not_contain_launch_kill_verdicts() -> None:
    report, md = _build_full_report()
    blob = (md + "\n" + json.dumps(report.model_dump(), default=str))
    forbidden = (
        re.compile(r"\bshould launch (this|the product)", re.IGNORECASE),
        re.compile(r"\bdo not launch (this|the product)", re.IGNORECASE),
        re.compile(r"\bkill (this product|the product)", re.IGNORECASE),
        re.compile(r"\bgo-to-market verdict:\s*(launch|kill|pivot)", re.IGNORECASE),
        re.compile(
            r"\b(this product will|the market will) "
            r"(succeed|fail|crush|dominate)",
            re.IGNORECASE,
        ),
    )
    for pat in forbidden:
        assert pat.search(blob) is None, f"matched: {pat.pattern}"


# ---------------------------------------------------------------------------
# 16. No representativeness claim
# ---------------------------------------------------------------------------


def test_report_does_not_claim_full_market_representativeness() -> None:
    report, md = _build_full_report()
    blob = (md + " " + " | ".join(report.executive_summary)).lower()
    # Must NOT claim representative; MUST disclaim it.
    assert "is representative" not in blob
    assert "fully representative" not in blob
    assert (
        "not representative" in blob
        or "not representative of every" in blob
    )


# ---------------------------------------------------------------------------
# 17. No fake direct StrideShield buyer/user/reviewer claims
# ---------------------------------------------------------------------------


def test_report_does_not_claim_direct_buyers() -> None:
    """The aggregator must not produce phrases like 'TestProduct buyer
    said' or 'I bought TestProduct'."""
    report, md = _build_full_report()
    blob = (md + " " + " | ".join(report.executive_summary)).lower()
    # Patterns that imply direct customer claims (parameterized)
    for term in (
        "testproduct buyer", "testproduct customer",
        "testproduct user said", "testproduct loyalist",
        "i bought testproduct", "i tried testproduct",
        "my testproduct", "testproduct works great",
    ):
        assert term not in blob, f"forbidden phrase: {term!r}"


# ---------------------------------------------------------------------------
# 18 + 19. Objections / levers trace back to personas
# ---------------------------------------------------------------------------


def test_objections_trace_back_to_personas() -> None:
    report, _ = _build_full_report()
    # Top objection must trace to ≥1 persona
    assert report.top_objections, "no objections to test"
    assert any(
        o.raised_by_personas or o.raised_by_roles
        for o in report.top_objections
    )


def test_persuasion_levers_trace_back_to_personas() -> None:
    report, _ = _build_full_report()
    assert report.top_persuasion_levers
    # At least one lever should have a movable-personas trace
    assert any(
        l.likely_movable_personas
        for l in report.top_persuasion_levers
    )


# ---------------------------------------------------------------------------
# 20. Competitor comparisons are evidence-backed
# ---------------------------------------------------------------------------


def test_competitor_comparisons_evidence_backed() -> None:
    report, _ = _build_full_report()
    for c in report.competitor_comparison:
        assert c.mention_count >= 1


# ---------------------------------------------------------------------------
# 21. DB unchanged — script never writes to DB
# ---------------------------------------------------------------------------


def test_script_does_not_insert_anything() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden_orm = (
        "SourceRecord", "PersonaRecord", "PersonaTrait",
        "PersonaEvidenceLink", "PersonaGraphEdge", "PersonaCluster",
        "Agent", "AgentResponse", "DebateTurn",
        "Simulation", "SimulationOutput", "SimulationRound",
        "PopulationConstructionAudit",
    )
    for term in forbidden_orm:
        pat = re.compile(rf"\b{re.escape(term)}\(\s*\w")
        for m in pat.finditer(src):
            ctx = src[max(0, m.start() - 25):m.end() + 25]
            if "select(" in ctx:
                continue
            raise AssertionError(
                f"forbidden ORM construction: ...{ctx}..."
            )


def test_script_no_session_writes() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    bad = (
        "session.add(", "session.delete(", "session.commit(",
        "session.flush(",
        ".execute(insert(", ".execute(update(", ".execute(delete(",
    )
    for token in bad:
        assert token not in src, f"forbidden token: {token!r}"


# ---------------------------------------------------------------------------
# 22 + 23. No external retrieval / no Brave / YouTube / Tavily / Firecrawl
# ---------------------------------------------------------------------------


def test_script_does_not_call_brave() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "BraveSearchClient(", ".search(queries=",
        "is_brave_key_present(",
    )
    for token in forbidden:
        assert token not in src, f"forbidden Brave call: {token!r}"


def test_script_does_not_call_youtube() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "YouTubeDataClient(", ".search_videos(", ".fetch_comments(",
        "is_youtube_key_present(",
    )
    for token in forbidden:
        assert token not in src, f"forbidden YouTube call: {token!r}"


def test_script_does_not_import_external_libs() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "httpx.", "requests.", "aiohttp.",
        "tavily", "firecrawl", "yt_dlp", "pytube", "scrapetube",
        "anthropic", "openai", "exa", "jina", "dataforseo",
    )
    for s in forbidden:
        assert s.lower() not in src.lower(), f"forbidden: {s!r}"


# ---------------------------------------------------------------------------
# 24. LLM, if used, goes through cost guard (and the deterministic path
#  doesn't use any LLM at all)
# ---------------------------------------------------------------------------


def test_script_does_not_use_llm_at_all() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # Deterministic-only by design; no LLM imports.
    assert "cost_guarded_chat" not in src
    assert "MockProvider" not in src
    assert "LLMProvider" not in src
    assert "LLMMessage" not in src


def test_aggregator_pkg_does_not_import_llm() -> None:
    forbidden_imports = {
        "httpx", "requests", "aiohttp", "anthropic", "openai",
        "tavily", "firecrawl",
    }
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_imports, (
                        f"forbidden import {alias.name} in {f.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_imports, (
                    f"forbidden import {node.module} in {f.name}"
                )


# ---------------------------------------------------------------------------
# 25. Secret scanner catches API-key-like strings
# ---------------------------------------------------------------------------


def test_secret_scanner_catches_anthropic_keys() -> None:
    text = (
        "this contains a key sk-ant-api03-NiiWkx8cpIpE3Z1X1JGedoVg4Lxw"
    )
    r = scan_for_secrets(text)
    assert r.is_clean is False
    assert any(
        f["category"] == "anthropic_key_prefix" for f in r.findings
    )
    assert "[REDACTED]" in r.redacted_text


def test_secret_scanner_catches_openai_proj_keys() -> None:
    text = "key: sk-proj-NQNAPyyp0VQ0IL0KIGbFhHfAvmMYYH76BmmDoh"
    r = scan_for_secrets(text)
    assert r.is_clean is False
    assert any(
        f["category"] == "openai_proj_key_prefix" for f in r.findings
    )


def test_secret_scanner_catches_named_provider_eq() -> None:
    text = "ANTHROPIC_API_KEY=sk-ant-blah-12345-very-long-string-here"
    r = scan_for_secrets(text)
    assert r.is_clean is False
    cats = {f["category"] for f in r.findings}
    assert (
        "named_provider_key_eq" in cats
        or "anthropic_key_prefix" in cats
    )


def test_secret_scanner_catches_aws_access_key() -> None:
    text = "key: AKIAIOSFODNN7EXAMPLE"
    r = scan_for_secrets(text)
    assert r.is_clean is False
    assert any(
        f["category"] == "aws_access_key_id" for f in r.findings
    )


def test_secret_scanner_passes_clean_report() -> None:
    _, md = _build_full_report()
    r = scan_for_secrets(md)
    assert r.is_clean is True
    assert not r.findings


def test_secret_scanner_passes_clean_json_dump() -> None:
    report, _ = _build_full_report()
    txt = json.dumps(report.model_dump(), default=str)
    r = scan_for_secrets(txt)
    assert r.is_clean is True


# ---------------------------------------------------------------------------
# 26 + 27 + 28. Markdown / JSON / quality JSON paths exist
# ---------------------------------------------------------------------------


def test_script_writes_markdown_json_and_quality_files() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "OUT_JSON" in src
    assert "OUT_MD" in src
    assert "OUT_QUALITY_JSON" in src
    assert "render_markdown_report" in src
    assert "evaluate_report_quality" in src


def test_render_markdown_includes_all_required_sections() -> None:
    _, md = _build_full_report()
    required_headers = (
        "## 1. Executive Summary",
        "## 2. Simulated Audience Snapshot",
        "## 3. Overall Reaction",
        "## 4. Top Objections",
        "## 5. Top Persuasion Levers",
        "## 6. Competitor Comparison",
        "## 7. Proof Needed Before Adoption",
        "## 8. Positioning Recommendations",
        "## 9. Product / Offer Recommendations",
        "## 10. What to Test Next",
        "## 11. Caveats",
        "## 12. Appendix",
    )
    for h in required_headers:
        assert h in md, f"missing header: {h!r}"


# ---------------------------------------------------------------------------
# Quality evaluator
# ---------------------------------------------------------------------------


def test_quality_evaluator_synthetic_report_is_ready() -> None:
    report, md = _build_full_report()
    qe = evaluate_report_quality(
        report=report, rendered_markdown=md,
        product_name="TestProduct",
    )
    assert qe.anti_forecast_score == 1.0
    assert qe.unlaunched_product_integrity_score == 1.0
    assert qe.caveat_integrity_score >= 0.8
    assert qe.aggregate_score >= 0.8
    assert qe.ready_state in (
        "READY_FOR_FRESH_END_TO_END_TEST",
        "READY_FOR_REPORT_PROMPT_FIX",
    )


def test_quality_evaluator_blocks_on_forecast_in_report() -> None:
    report, md = _build_full_report()
    bad_md = md + "\n\n30% of the market will buy this.\n"
    qe = evaluate_report_quality(
        report=report, rendered_markdown=bad_md,
        product_name="TestProduct",
    )
    assert qe.anti_forecast_score < 1.0
    assert qe.ready_state == "NOT_READY"


def test_quality_evaluator_blocks_on_fake_use_in_report() -> None:
    report, md = _build_full_report()
    bad_md = md + "\n\nI bought TestProduct last week.\n"
    qe = evaluate_report_quality(
        report=report, rendered_markdown=bad_md,
        product_name="TestProduct",
    )
    assert qe.unlaunched_product_integrity_score < 1.0
    assert qe.ready_state == "NOT_READY"


# ---------------------------------------------------------------------------
# Universal helpers
# ---------------------------------------------------------------------------


def test_persona_role_to_phrase_handles_universal_prefixes() -> None:
    assert _persona_role_to_phrase(
        "competitor_user_brand_x"
    ).startswith("current Brand X")
    assert _persona_role_to_phrase(
        "substitute_user_coffee"
    ).startswith("current Coffee")
    assert _persona_role_to_phrase("price_skeptic") == "price skeptic"


def test_severity_classifier_universal() -> None:
    # Counts ≥3 → high
    assert _classify_severity(text="generic", count=3) == "high"
    # Switching cost language → high
    assert _classify_severity(
        text="already covers this format", count=1,
    ) == "high"
    # Pricing language → high
    assert _classify_severity(text="price", count=1) == "high"
    # Format / packaging → medium
    assert _classify_severity(text="format size", count=1) == "medium"
    # Plain → low
    assert _classify_severity(text="hmm", count=1) == "low"


def test_proof_kind_classifier_universal() -> None:
    kind, _ = _classify_proof_kind("side-by-side miles vs Brand A")
    assert kind == "side_by_side_durability"
    kind, _ = _classify_proof_kind("named runner testimonials")
    assert kind == "runner_or_athlete_testimonials"
    kind, _ = _classify_proof_kind("ounces per tube")
    assert kind == "value_or_pricing_proof"
    kind, _ = _classify_proof_kind("totally vague signal")
    assert kind == "general_credibility_signal"


def test_objection_to_action_universal_mapping() -> None:
    a = _objection_to_founder_action("$12.99 is too expensive")
    assert "value-clarity" in a or "cost-per-application" in a
    a = _objection_to_founder_action("already use Brand A")
    assert "comparison" in a.lower()
    a = _objection_to_founder_action("greasy texture")
    assert "texture" in a.lower()


# ---------------------------------------------------------------------------
# 29. Existing 8.5E imports still resolve
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.run_scoped_persona_simulation import (  # noqa: F401
        evaluate_simulation_quality,
        scan_forecast_or_verdict_claims,
        scan_unlaunched_product_use_claims,
    )
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set,
    )
    from assembly.sources.founder_report_generator import (  # noqa: F401
        aggregate_founder_report, render_markdown_report,
        evaluate_report_quality, scan_for_secrets,
    )


# ---------------------------------------------------------------------------
# Bonus: report drift — no hardcoded brand/category in pkg
# ---------------------------------------------------------------------------


def test_aggregator_pkg_has_no_hardcoded_brand_or_category() -> None:
    forbidden = (
        "strideshield", "triton", "solara",
        "body glide", "body_glide", "megababe", "trail toes",
        "trail_toes", "squirrel", "gold bond", "red bull",
        "monster", "celsius", "gatorade",
        "anti-blister", "anti-chafe",
    )
    for f in PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        ds_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (
                ast.FunctionDef, ast.AsyncFunctionDef,
                ast.ClassDef, ast.Module,
            )):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    n0 = node.body[0]
                    for ln in range(
                        n0.lineno, (n0.end_lineno or n0.lineno) + 1,
                    ):
                        ds_lines.add(ln)
        kept: list[str] = []
        for i, line in enumerate(src.splitlines(), 1):
            if i in ds_lines:
                continue
            ci = line.find("#")
            if ci >= 0:
                line = line[:ci]
            kept.append(line)
        code = "\n".join(kept).lower()
        for term in forbidden:
            assert term not in code, (
                f"hardcoded {term!r} in aggregator pkg {f.name}"
            )


# ---------------------------------------------------------------------------
# Bonus: founder report rejects extra fields (extra=forbid)
# ---------------------------------------------------------------------------


def test_founder_report_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        FounderReport(  # type: ignore[call-arg]
            completed_at="x", simulation_id="x", run_scope_id="x",
            target_brief_id="x", product_name="x", launch_state="x",
            founder_brief={}, input_summary={},
            executive_summary=[], simulated_audience_snapshot=[],
            stance_distribution={}, overall_reaction=[],
            top_objections=[], top_persuasion_levers=[],
            competitor_comparison=[], proof_needed=[],
            positioning_recommendations=[],
            product_offer_recommendations=[],
            what_to_test_next=[], caveats=[],
            appendix={  # type: ignore[arg-type]
                "persona_to_evidence_map": [],
                "round_summary": [],
                "quality_scores": {},
                "forbidden_claim_audit": {},
                "source_persona_traceability": {},
            },
            source_traceability={}, persona_traceability={},
            quality_reference={}, forbidden_claim_audit={},
            security_redaction_audit={},
            ready_for_fresh_end_to_end_test=False,
            rationale=[],
            unexpected_extra="boom",
        )
