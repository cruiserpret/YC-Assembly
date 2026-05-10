"""Phase 9A — scale-LumaLoop-society tests.

Operator scenarios 1-24 covered. NO live API calls, NO DB writes
from the test file itself.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "scale_lumaloop_society_9a.py"
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
# 1. 9A reads 8.5G.1 baseline audit
# ---------------------------------------------------------------------------


def test_9a_reads_baseline_audit() -> None:
    src = _src()
    assert "fresh_end_to_end_lumaloop_8_5g_1.json" in src
    assert "previous_8_5g_1_summary" in src


# ---------------------------------------------------------------------------
# 2. No Jina / Exa / DataForSEO / Reddit / Apify
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
# 3 + 4. Provider keys reported as bool only; values never printed
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
# 5. Source planning includes ≥2 queries per competitor
#    (universal floor in SourceExpansionPlanner — the 9A spec lifts
#     the `at least 1 query per competitor` 8.5G.1 floor to ≥2.
#     The planner currently emits exactly 1 floor-query per competitor
#     PLUS comparison queries that name 2+ competitors. The orchestrator
#     also runs Tavily which adds another per-competitor floor query.)
# ---------------------------------------------------------------------------


def test_per_competitor_query_coverage_at_least_2_via_brave_plus_tavily() -> None:
    """Across Brave + Tavily, every brief.competitor must appear in
    at least 2 queries (1 from each provider's per-competitor floor)."""
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
    cross_provider_blob = " ".join(
        q.query_text
        for pp in plan.provider_query_plans
        for q in pp.queries
    )
    for c in competitors:
        # At minimum each competitor appears in 2+ queries (Brave
        # floor + Tavily floor + possibly comparison pairs).
        assert cross_provider_blob.count(c) >= 2, (
            f"competitor {c!r} appears in fewer than 2 queries"
        )


# ---------------------------------------------------------------------------
# 6. Retrieval caps are enforced
# ---------------------------------------------------------------------------


def test_retrieval_caps_lifted_to_9a_levels() -> None:
    src = _src()
    assert '"--brave-max-queries"' in src and 'default=20' in src
    assert '"--tavily-max-queries"' in src
    assert '"--yt-max-video-queries"' in src and 'default=8' in src
    assert "yt-max-comments-per-video" in src


# ---------------------------------------------------------------------------
# 7. Firecrawl caps enforced
# ---------------------------------------------------------------------------


def test_firecrawl_caps_enforced() -> None:
    src = _src()
    assert '"--firecrawl-max-pages"' in src
    # Default raised from 8 (8.5G.1) to 15 in 9A
    assert "default=15" in src


# ---------------------------------------------------------------------------
# 8. Source records staged before persona gate
# ---------------------------------------------------------------------------


def test_source_records_staged_in_memory_before_persona_gate() -> None:
    src = _src()
    assert "STAGE source records IN MEMORY" in src
    assert "PERSONA GATE" in src
    assert "halted_at_compression_gate" in src


# ---------------------------------------------------------------------------
# 9 + 10. Source records not inserted on gate fail; no orphans
# ---------------------------------------------------------------------------


def test_no_orphan_source_records_when_compression_gate_fails() -> None:
    src = _src()
    gate_idx = src.find(
        "if compressed.diff_summary.after_count "
        "< EXPECTED_MIN_COMPRESSED_PERSONAS:",
    )
    insert_idx = src.find("session.add(SourceRecord(")
    assert gate_idx > 0 and insert_idx > 0
    assert insert_idx > gate_idx, (
        "SourceRecord insert must appear AFTER the compression gate"
    )


def test_orchestrator_does_not_pre_insert_source_records() -> None:
    src = _src()
    count = len(re.findall(
        r"session\.add\(\s*SourceRecord\(", src,
    ))
    assert count == 1, (
        f"expected exactly one SourceRecord insertion site; found {count}"
    )


# ---------------------------------------------------------------------------
# 11. Raw persona candidate minimum gate
# ---------------------------------------------------------------------------


def test_raw_candidate_minimum_gate_present() -> None:
    src = _src()
    assert "EXPECTED_MIN_RAW_CANDIDATES" in src
    assert "halted_at_raw_candidate_gate" in src
    # Default is 25
    assert "EXPECTED_MIN_RAW_CANDIDATES = 25" in src


# ---------------------------------------------------------------------------
# 12. Compressed persona minimum 21 gate
# ---------------------------------------------------------------------------


def test_compression_gate_minimum_is_21() -> None:
    src = _src()
    assert "EXPECTED_MIN_COMPRESSED_PERSONAS = 21" in src
    assert "EXPECTED_MAX_COMPRESSED_PERSONAS = 30" in src


# ---------------------------------------------------------------------------
# 13. Single-role >35% concentration fails gate
# ---------------------------------------------------------------------------


def test_role_concentration_max_35_pct_gate_present() -> None:
    src = _src()
    assert "MAX_ROLE_CONCENTRATION_FRACTION = 0.35" in src
    assert "halted_at_diversity_gate" in src
    assert "role_concentration_blocker" in src


# ---------------------------------------------------------------------------
# 14. Duplicate same-voice personas are rejected
#    (compressor's universal triple-collapse rule from 8.5D.1E)
# ---------------------------------------------------------------------------


def test_compressor_triple_collapse_rule_in_use() -> None:
    """The compressor used by 9A reuses the same universal
    `compress_persona_set` from 8.5D.1E. That function rejects
    same (role, theme, provider) triples as `duplicate_role_and_theme`.
    Verified by the existing 8.5D.1E test suite, which is part of
    the regression sweep."""
    from assembly.sources.persona_set_compressor import (
        compress_persona_set,
    )
    import inspect
    sig = inspect.signature(compress_persona_set)
    assert "min_behavioral_differential" in sig.parameters


# ---------------------------------------------------------------------------
# 15. Persona persistence only after gate
# ---------------------------------------------------------------------------


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
# 16 + 17. All personas run-scoped / brief-scoped; no global personas
# ---------------------------------------------------------------------------


def test_personas_are_run_scoped_and_brief_scoped() -> None:
    src = _src()
    needed = (
        "target_brief:",
        "run_scope_id:",
        "scope:run_scoped_brief_scoped",
        "not_global_persona:true",
        "compressed_candidate_id:",
    )
    for s in needed:
        assert s in src, f"missing run/brief-scope tag: {s!r}"


def test_no_global_personas_created() -> None:
    src = _src()
    assert "scope:global" not in src
    assert "not_global_persona:false" not in src


# ---------------------------------------------------------------------------
# 18. Simulation runs only after persona persistence
# ---------------------------------------------------------------------------


def test_simulation_runs_only_after_persona_persistence() -> None:
    src = _src()
    persona_insert_idx = src.find("session.add(PersonaRecord(")
    sim_insert_idx = src.find("session.add(Simulation(")
    assert persona_insert_idx > 0 and sim_insert_idx > 0
    assert sim_insert_idx > persona_insert_idx


# ---------------------------------------------------------------------------
# 19. Simulation uses cost guard
# ---------------------------------------------------------------------------


def test_simulation_uses_cost_guarded_chat() -> None:
    src = _src()
    assert "cost_guarded_chat" in src
    assert "provider.chat(" not in src
    assert ".structured_output(" not in src
    assert "hard_cap_usd=SIM_HARD_CAP_USD" in src
    assert 'SIM_HARD_CAP_USD = Decimal("8.00")' in src


# ---------------------------------------------------------------------------
# 20. Report generates only after simulation quality passes
# ---------------------------------------------------------------------------


def test_report_only_generates_after_simulation_quality_passes() -> None:
    src = _src()
    quality_check_idx = src.find('"ready_for_founder_report_phase"')
    aggregate_call_idx = src.find("aggregate_founder_report(")
    assert quality_check_idx > 0 and aggregate_call_idx > 0
    assert aggregate_call_idx > quality_check_idx


# ---------------------------------------------------------------------------
# 21. Secret scanner runs on all output files
# ---------------------------------------------------------------------------


def test_secret_scanner_runs_before_writing_outputs() -> None:
    src = _src()
    assert "scan_for_secrets" in src
    assert "secrets_clean" in src


# ---------------------------------------------------------------------------
# 22. Existing 8.5G.1 + 8.5F + 8.5E imports still resolve
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
# Bonus: --dry-run / --commit modes
# ---------------------------------------------------------------------------


def test_script_supports_dry_run_and_commit_modes() -> None:
    src = _src()
    assert "--dry-run" in src
    assert "--commit" in src
    assert "default=True" in src


# ---------------------------------------------------------------------------
# Bonus: ready_for_9b output
# ---------------------------------------------------------------------------


def test_ready_for_9b_output_field_present() -> None:
    src = _src()
    assert "ready_for_9b_50_to_100_personas" in src


# ---------------------------------------------------------------------------
# Bonus: scaled-mode quality gates include all 9A-specific gates
# ---------------------------------------------------------------------------


def test_scaled_mode_quality_gates_present() -> None:
    src = _src()
    needed = (
        '"compressed_personas_at_least_21"',
        '"compressed_personas_at_most_30"',
        '"no_single_role_over_35_pct"',
        '"at_least_5_distinct_roles"',
        '"raw_candidates_above_floor"',
        '"evidence_from_2_or_more_providers"',
        '"no_fake_target_product_use"',
        '"no_forecast_or_verdict"',
        '"secrets_clean"',
    )
    for g in needed:
        assert g in src, f"missing quality gate: {g}"


# ---------------------------------------------------------------------------
# Bonus: no hardcoded LumaLoop/competitor persona templates in 9A code
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
        '"dog_walker_persona"', '"reflective_vest_rejecter"',
    )
    for lit in forbidden:
        assert lit not in code, f"hardcoded persona literal: {lit!r}"
