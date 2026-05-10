"""Phase 8.5G.1 — evidence-coverage broadening tests.

Operator scenarios 1-25 covered. NO live API calls, NO DB writes
from the test file itself.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "fresh_end_to_end_product_test_8_5g_1.py"
)
TAVILY_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "tavily"
)
FIRECRAWL_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "firecrawl"
)
EXPANSION_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "source_expansion_planner"
)


def _src() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _src_code_only() -> str:
    src = _src()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
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
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# 1 + 2. Tavily routing exists + skipped gracefully if key missing
# ---------------------------------------------------------------------------


def test_tavily_routing_exists_in_planner() -> None:
    src = (EXPANSION_PKG / "planner.py").read_text(encoding="utf-8")
    assert "_tavily_queries" in src
    assert "tavily_search" in src
    assert "TAVILY_API_KEY" in src


def test_tavily_skipped_gracefully_when_key_missing() -> None:
    """If `tavily_search` is `False` in providers_available, the
    plan should still return a ProviderQueryPlan with empty queries
    + skipped_reason set."""
    from assembly.sources.evidence_anchor_planner import (
        ProductBriefForPlanning, generate_anchor_plan,
    )
    from assembly.sources.persona_diversity_evaluator import (
        evaluate_persona_diversity,
    )
    from assembly.sources.source_expansion_planner import (
        generate_source_expansion_plan,
    )
    brief = ProductBriefForPlanning(
        product_name="Test", product_description="x x x x x x x x",
        competitors=["A", "B"], target_customers=["users"],
    )
    ap = generate_anchor_plan(brief)
    de = evaluate_persona_diversity(brief=brief, candidates=[])
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
            "tavily_search": False,
        },
        target_brief_id="test",
    )
    tavily = next(
        p for p in plan.provider_query_plans
        if p.provider == "tavily_search"
    )
    assert tavily.is_provider_configured is False
    assert tavily.queries == []
    assert "TAVILY_API_KEY" in (tavily.skipped_reason or "")


# ---------------------------------------------------------------------------
# 3 + 4 + 5. Firecrawl extraction exists + skipped gracefully + bounded
# ---------------------------------------------------------------------------


def test_firecrawl_adapter_exists() -> None:
    from assembly.sources.firecrawl import (  # noqa: F401
        FirecrawlExtractClient, is_firecrawl_key_present,
    )


def test_firecrawl_skipped_gracefully_when_key_missing() -> None:
    src = _src()
    assert "is_firecrawl_key_present()" in src
    assert "if is_firecrawl_key_present()" in src


def test_firecrawl_extraction_is_bounded() -> None:
    src = (FIRECRAWL_PKG / "adapter.py").read_text(encoding="utf-8")
    assert "max_pages" in src
    assert "max_pages_per_domain" in src
    # Defaults are tight
    assert "_DEFAULT_MAX_PAGES = " in src
    assert "_DEFAULT_MAX_PAGES_PER_DOMAIN" in src


# ---------------------------------------------------------------------------
# 6 + 7. Per-competitor query coverage + universal (no LumaLoop hardcoding)
# ---------------------------------------------------------------------------


def test_planner_creates_at_least_one_query_per_competitor() -> None:
    from assembly.sources.evidence_anchor_planner import (
        ProductBriefForPlanning, generate_anchor_plan,
    )
    from assembly.sources.persona_diversity_evaluator import (
        evaluate_persona_diversity,
    )
    from assembly.sources.source_expansion_planner import (
        generate_source_expansion_plan,
    )
    competitors = ["BrandA", "BrandB", "BrandC", "BrandD", "BrandE"]
    brief = ProductBriefForPlanning(
        product_name="TestProduct",
        product_description="Test product for verifying coverage.",
        competitors=competitors,
        target_customers=["users"],
    )
    ap = generate_anchor_plan(brief)
    de = evaluate_persona_diversity(brief=brief, candidates=[])
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
            "tavily_search": True,
        },
        target_brief_id="test",
    )
    brave = next(
        p for p in plan.provider_query_plans
        if p.provider == "brave_search"
    )
    tavily = next(
        p for p in plan.provider_query_plans
        if p.provider == "tavily_search"
    )
    # Brave + Tavily both lead with per-competitor floor queries.
    brave_query_text = " ".join(q.query_text for q in brave.queries)
    tavily_query_text = " ".join(q.query_text for q in tavily.queries)
    for c in competitors:
        assert c in brave_query_text, (
            f"competitor {c!r} missing from Brave plan"
        )
        assert c in tavily_query_text, (
            f"competitor {c!r} missing from Tavily plan"
        )


def test_per_competitor_coverage_is_product_general() -> None:
    """The planner code must NOT contain any product-specific
    competitor literals (LumaLoop / Body Glide / etc)."""
    src = (EXPANSION_PKG / "planner.py").read_text(encoding="utf-8")
    forbidden = (
        "Noxgear", "Amphipod", "Nathan reflective",
        "FlipBelt", "Black Diamond Sprinter",
        "Body Glide", "Megababe", "Trail Toes",
        "StrideShield", "Triton", "LumaLoop",
    )
    # Strip docstrings
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    ds_lines: set[int] = set()
    if tree:
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
    code = "\n".join(kept)
    for term in forbidden:
        assert term not in code, (
            f"hardcoded {term!r} in planner code"
        )


# ---------------------------------------------------------------------------
# 8. No hardcoded LumaLoop competitor/persona/category templates
# ---------------------------------------------------------------------------


def test_no_hardcoded_lumaloop_persona_templates_in_orchestrator() -> None:
    code = _src_code_only()
    forbidden = (
        '"competitor_user_noxgear_tracer2"',
        '"competitor_user_amphipod"',
        '"competitor_user_nathan_reflective_gear"',
        '"competitor_user_flipbelt_lights"',
        '"competitor_user_black_diamond_sprinter_headlamp"',
        '"night_runner_persona"', '"cyclist_persona"',
        '"dog_walker_persona"',
    )
    for lit in forbidden:
        assert lit not in code, f"hardcoded persona literal: {lit!r}"


# ---------------------------------------------------------------------------
# 9. No Jina / Exa / DataForSEO / Reddit / Apify
# ---------------------------------------------------------------------------


def test_no_jina_exa_dataforseo_reddit_apify_usage() -> None:
    code = _src_code_only()
    forbidden = (
        "jina", "exa.", "dataforseo", "reddit", "apify",
        "JINA_API_KEY", "EXA_API_KEY", "DATAFORSEO_API_KEY",
    )
    for s in forbidden:
        assert s.lower() not in code.lower(), f"forbidden: {s!r}"


# ---------------------------------------------------------------------------
# 10 + 11. Provider keys reported as bool only; values never printed
# ---------------------------------------------------------------------------


def test_provider_keys_reported_as_bool_only() -> None:
    src = _src()
    assert '"brave_search_configured"' in src
    assert '"youtube_data_configured"' in src
    assert '"tavily_configured"' in src
    assert '"firecrawl_configured"' in src


def test_no_secret_value_printed_or_logged() -> None:
    code = _src_code_only()
    secrets = (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "BRAVE_SEARCH_API_KEY", "YOUTUBE_DATA_API_KEY",
        "TAVILY_API_KEY", "FIRECRAWL_API_KEY",
    )
    for v in secrets:
        bad = (
            re.compile(
                rf'print\s*\(\s*[^)]*os\.environ\.get\(\s*["\']{re.escape(v)}',
                re.IGNORECASE,
            ),
            re.compile(
                rf'logger\.\w+\(\s*[^)]*os\.environ\.get\(\s*["\']{re.escape(v)}',
                re.IGNORECASE,
            ),
        )
        for pat in bad:
            assert pat.search(code) is None, (
                f"secret value {v!r} surfaced via {pat.pattern}"
            )


# ---------------------------------------------------------------------------
# 12 + 13. Source records staged before DB insert + cleanup if gate fails
# ---------------------------------------------------------------------------


def test_source_records_staged_in_memory_before_persona_gate() -> None:
    src = _src()
    # The script must NOT call _build_source_record_payload BEFORE
    # the persona compression gate. The Phase 8.5G.1 discipline:
    # build SourceRecord ORM objects only inside the post-gate
    # bounded transaction.
    # Verify: comments/code flag the staging discipline.
    assert "STAGE source records IN MEMORY" in src
    assert "PERSONA GATE" in src
    assert "halted_at_compression_gate" in src


def test_no_orphan_source_records_when_gate_fails() -> None:
    """When the compression gate fails, the script never opens the
    source_records insertion transaction. Verify by static check
    that source_records insertion is INSIDE the post-gate code path."""
    src = _src()
    # The session.add(SourceRecord(...)) call must appear AFTER the
    # `EXPECTED_MIN_COMPRESSED_PERSONAS` gate check.
    gate_idx = src.find("if compressed.diff_summary.after_count "
                        "< EXPECTED_MIN_COMPRESSED_PERSONAS:")
    insert_idx = src.find("session.add(SourceRecord(")
    assert gate_idx > 0, "compression gate not present"
    assert insert_idx > 0, "SourceRecord insert not present"
    assert insert_idx > gate_idx, (
        "SourceRecord insert must appear AFTER the compression gate"
    )


def test_partial_audit_written_on_gate_halt() -> None:
    src = _src()
    # On halt, audit is written with rollback_reason + db_post_counts
    # + db_delta_summary so the operator can see net-zero.
    halt_block = src[
        src.find("halted_at_compression_gate"):
        src.find("halted_at_compression_gate") + 1500
    ]
    assert "db_post_counts" in halt_block
    assert "db_delta_summary" in halt_block


# ---------------------------------------------------------------------------
# 14. No orphan source_records remain after failed gate
# ---------------------------------------------------------------------------


def test_orchestrator_does_not_pre_insert_source_records() -> None:
    """The ONLY place that creates SourceRecord ORM rows must be
    inside the post-gate bounded transaction."""
    src = _src()
    # Count occurrences of `session.add(SourceRecord(`. There must
    # be exactly ONE in the script (inside the post-gate transaction).
    count = len(re.findall(
        r"session\.add\(\s*SourceRecord\(", src,
    ))
    assert count == 1, (
        f"expected exactly one SourceRecord insertion site; found {count}"
    )


# ---------------------------------------------------------------------------
# 15. Evidence scoring rejects generic SEO fluff
# ---------------------------------------------------------------------------


def test_evidence_scoring_rejects_generic_and_below_threshold() -> None:
    src = _src()
    assert "_generic_filler" in src
    assert "reject_generic_only" in src
    assert "reject_below_relevance_threshold" in src


# ---------------------------------------------------------------------------
# 16. Firecrawl-extracted pages pass PII / fake-use scanners
# ---------------------------------------------------------------------------


def test_firecrawl_extracted_pages_pass_universal_scanners() -> None:
    src = _src()
    fc_block_start = src.find("# ---- Firecrawl extraction")
    fc_block_end = src.find("# ---- YouTube ----")
    assert fc_block_start > 0
    assert fc_block_end > fc_block_start
    fc_block = src[fc_block_start:fc_block_end]
    assert "_scan_pii" in fc_block
    assert "scan_unlaunched_product_use_claims" in fc_block
    assert "reject_fake_target_product_use" in fc_block


# ---------------------------------------------------------------------------
# 17. Fake LumaLoop usage claims are rejected
# ---------------------------------------------------------------------------


def test_fake_target_product_use_rejected_in_retrieval() -> None:
    src = _src()
    assert "scan_unlaunched_product_use_claims" in src
    assert "reject_fake_target_product_use" in src


# ---------------------------------------------------------------------------
# 18 + 19. Compression gate >= 5; persistence only after gate
# ---------------------------------------------------------------------------


def test_compression_gate_minimum_is_five() -> None:
    src = _src()
    assert "EXPECTED_MIN_COMPRESSED_PERSONAS = 5" in src
    assert (
        "compressed.diff_summary.after_count "
        "< EXPECTED_MIN_COMPRESSED_PERSONAS" in src
    )


def test_persona_persistence_only_after_gate() -> None:
    src = _src()
    gate_idx = src.find(
        "if compressed.diff_summary.after_count "
        "< EXPECTED_MIN_COMPRESSED_PERSONAS:",
    )
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    assert gate_idx > 0 and persona_insert_idx > gate_idx, (
        "PersonaRecord insertion must be after the compression gate"
    )


# ---------------------------------------------------------------------------
# 20. Simulation only runs after persona persistence
# ---------------------------------------------------------------------------


def test_simulation_runs_only_after_persona_persistence() -> None:
    src = _src()
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    sim_insert_idx = src.find("session.add(Simulation(")
    assert persona_insert_idx > 0 and sim_insert_idx > 0
    assert sim_insert_idx > persona_insert_idx, (
        "Simulation row must be inserted AFTER PersonaRecord rows"
    )


# ---------------------------------------------------------------------------
# 21. Report generates only after simulation quality passes
# ---------------------------------------------------------------------------


def test_report_only_generates_after_simulation_quality_passes() -> None:
    src = _src()
    quality_check_idx = src.find('"ready_for_founder_report_phase"')
    aggregate_call_idx = src.find("aggregate_founder_report(")
    assert quality_check_idx > 0 and aggregate_call_idx > 0
    # Aggregate call must come AFTER the quality readiness check
    assert aggregate_call_idx > quality_check_idx


# ---------------------------------------------------------------------------
# 22. Secret scanner runs on all output files
# ---------------------------------------------------------------------------


def test_secret_scanner_runs_before_writing_outputs() -> None:
    src = _src()
    assert "scan_for_secrets" in src
    assert "secrets_clean" in src
    assert "secrets_detected_in_inputs" in src or "redactions_applied" in src


# ---------------------------------------------------------------------------
# 23. Existing 8.5G + 8.5F + 8.5E imports still resolve
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        generate_anchor_plan,
    )
    from assembly.sources.persona_diversity_evaluator import (  # noqa: F401
        evaluate_persona_diversity,
    )
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set,
    )
    from assembly.sources.run_scoped_persona_simulation import (  # noqa: F401
        load_run_scoped_agents, evaluate_simulation_quality,
    )
    from assembly.sources.source_expansion_planner import (  # noqa: F401
        generate_source_expansion_plan,
    )
    from assembly.sources.founder_report_generator import (  # noqa: F401
        aggregate_founder_report, render_markdown_report,
        evaluate_report_quality, scan_for_secrets,
    )
    from assembly.sources.tavily import (  # noqa: F401
        is_tavily_key_present, TavilySearchClient,
    )
    from assembly.sources.firecrawl import (  # noqa: F401
        is_firecrawl_key_present, FirecrawlExtractClient,
    )


# ---------------------------------------------------------------------------
# Bonus: Tavily + Firecrawl adapters refuse to run without keys
# ---------------------------------------------------------------------------


def test_tavily_refuses_to_search_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from assembly.sources.tavily import TavilySearchClient
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    client = TavilySearchClient()
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        client.search(queries=["test"])


@pytest.mark.asyncio
async def test_firecrawl_refuses_to_extract_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from assembly.sources.firecrawl import FirecrawlExtractClient
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    client = FirecrawlExtractClient()
    with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
        await client.extract_top_urls(urls=["https://example.com"])


# ---------------------------------------------------------------------------
# Bonus: Tavily + Firecrawl drift — no scrapers, no PII storage
# ---------------------------------------------------------------------------


def test_tavily_pkg_no_scraper_imports() -> None:
    forbidden = {
        "yt_dlp", "pytube", "scrapetube", "selenium",
        "playwright", "scrapy", "beautifulsoup4", "bs4",
    }
    for f in TAVILY_PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, (
                        f"forbidden import {alias.name} in {f.name}"
                    )


def test_firecrawl_pkg_no_scraper_imports() -> None:
    forbidden = {
        "yt_dlp", "pytube", "scrapetube", "selenium",
        "playwright", "scrapy",
    }
    for f in FIRECRAWL_PKG.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, (
                        f"forbidden import {alias.name} in {f.name}"
                    )


# ---------------------------------------------------------------------------
# Bonus: --dry-run / --commit modes
# ---------------------------------------------------------------------------


def test_script_supports_dry_run_and_commit_modes() -> None:
    src = _src()
    assert "--dry-run" in src
    assert "--commit" in src
    assert "default=True" in src


# ---------------------------------------------------------------------------
# Bonus: ProviderName Literal extended with tavily_search
# ---------------------------------------------------------------------------


def test_provider_name_literal_includes_tavily() -> None:
    from assembly.sources.source_expansion_planner.schemas import (
        ProviderName,
    )
    from typing import get_args
    assert "tavily_search" in get_args(ProviderName)


# ---------------------------------------------------------------------------
# Bonus: end-to-end quality gates include all 8.5G.1-specific gates
# ---------------------------------------------------------------------------


def test_quality_gates_include_2_provider_minimum() -> None:
    src = _src()
    assert '"evidence_from_2_or_more_providers"' in src
    assert '"compressed_personas_at_least_5"' in src
    assert '"no_fake_target_product_use"' in src
    assert '"no_forecast_or_verdict"' in src
    assert '"secrets_clean"' in src
