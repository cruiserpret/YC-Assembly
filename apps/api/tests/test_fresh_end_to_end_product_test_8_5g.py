"""Phase 8.5G — fresh end-to-end product test (LumaLoop) tests.

Operator scenarios 1-28 covered. NO LLM calls, NO live retrieval, NO
DB writes from the test file itself. Tests are static-grep + import-
only over the orchestrator script + universal validators.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "fresh_end_to_end_product_test_8_5g.py"
)


def _src() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _src_code_only() -> str:
    """Return the script source with module/function/class docstrings
    AND inline comments stripped — for substring drift checks that
    should only flag actual code, not commentary about what's NOT
    being used."""
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
# 1. Founder brief is the only product input
# ---------------------------------------------------------------------------


def test_founder_brief_is_the_only_product_input() -> None:
    src = _src()
    assert "LUMALOOP_BRIEF = ProductBriefForPlanning(" in src
    # No manual category anchors / persona roles / source categories
    forbidden_kwargs = (
        "manual_anchors=", "manual_category_anchors=",
        "manual_persona_roles=", "manual_source_categories=",
        "manual_roles=",
    )
    for kw in forbidden_kwargs:
        assert kw not in src, f"forbidden kwarg in script: {kw!r}"


# ---------------------------------------------------------------------------
# 2 + 3 + 4. No manual anchors / source categories / persona roles
# ---------------------------------------------------------------------------


def test_anchor_plan_call_uses_only_brief() -> None:
    src = _src()
    # generate_anchor_plan is called with brief only
    assert "generate_anchor_plan(LUMALOOP_BRIEF)" in src


def test_expansion_plan_call_does_not_force_categories() -> None:
    src = _src()
    assert "generate_source_expansion_plan(" in src
    assert "manual_source_categories" not in src
    assert "force_categories" not in src


def test_persona_planner_call_does_not_pass_manual_roles() -> None:
    src = _src()
    assert "PersonaCandidatePlanner(" in src
    forbidden = ("manual_persona_roles=", "allowed_roles=")
    for kw in forbidden:
        assert kw not in src, f"forbidden kwarg: {kw!r}"


# ---------------------------------------------------------------------------
# 5. No Jina / Exa / DataForSEO / Reddit / Apify usage
# ---------------------------------------------------------------------------


def test_no_jina_exa_dataforseo_reddit_apify_usage() -> None:
    code = _src_code_only()
    forbidden = (
        "jina", "exa.", "dataforseo", "reddit", "apify",
        "JINA_API_KEY", "EXA_API_KEY", "DATAFORSEO_API_KEY",
        "REDDIT_CLIENT_ID", "APIFY_TOKEN",
    )
    for s in forbidden:
        assert s.lower() not in code.lower(), f"forbidden: {s!r}"


# ---------------------------------------------------------------------------
# 6 + 7. Provider keys reported as bool only; key values never printed
# ---------------------------------------------------------------------------


def test_provider_keys_reported_as_bool_only() -> None:
    src = _src()
    # Only `is_brave_key_present()` / `is_youtube_key_present()` are
    # used — those return bool. Audit explicitly stamps bool fields.
    assert '"brave_search_configured"' in src
    assert '"youtube_data_configured"' in src
    assert '"tavily_configured"' in src
    assert '"firecrawl_configured"' in src


def test_script_does_not_print_api_key_values() -> None:
    """The script must never `print(...)` / `logger.*(...)` an env-var
    secret value, and must never f-string-interpolate one. Direct
    env-var reads are allowed (e.g. `bool(os.environ.get("X"))`,
    `if not os.environ.get("X")`) — but the *value* is never
    propagated to user-visible output."""
    code = _src_code_only()
    secret_vars = (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "BRAVE_SEARCH_API_KEY", "YOUTUBE_DATA_API_KEY",
        "TAVILY_API_KEY", "FIRECRAWL_API_KEY",
    )
    for v in secret_vars:
        # Forbid `print(... <V> ...)` or `f"... {os.environ.get('<V>')} ..."`
        forbidden_patterns = (
            re.compile(
                rf'print\s*\(\s*[^)]*os\.environ\.get\(\s*["\']'
                rf"{re.escape(v)}",
                re.IGNORECASE,
            ),
            re.compile(
                rf'f["\'][^"\']*\{{[^}}]*os\.environ\.get\(\s*["\']'
                rf"{re.escape(v)}",
                re.IGNORECASE,
            ),
            re.compile(
                rf'logger\.\w+\(\s*[^)]*os\.environ\.get\(\s*["\']'
                rf"{re.escape(v)}",
                re.IGNORECASE,
            ),
        )
        for pat in forbidden_patterns:
            assert pat.search(code) is None, (
                f"forbidden: secret value {v!r} surfaced via {pat.pattern}"
            )


# ---------------------------------------------------------------------------
# 8 + 9. Anchor + expansion plans dynamic
# ---------------------------------------------------------------------------


def test_anchor_plan_is_dynamic() -> None:
    """`generate_anchor_plan(brief)` is the only signature; no hardcoded
    category lists pinned per-product."""
    src = _src()
    # AnchorPlan computed at runtime from brief
    assert "anchor_plan = generate_anchor_plan(LUMALOOP_BRIEF)" in src
    # No hardcoded LumaLoop anchor terms
    forbidden = (
        '"led safety band"', '"runner visibility"',
        '"reflective gear"', '"night safety"',
    )
    # Forbid quoted hardcoded anchor literals
    for s in forbidden:
        assert s not in src, f"hardcoded anchor literal: {s!r}"


def test_expansion_plan_is_dynamic() -> None:
    src = _src()
    assert "generate_source_expansion_plan(" in src
    assert "providers_available=" in src


# ---------------------------------------------------------------------------
# 10 + 11. Bounded retrieval + Firecrawl bounds
# ---------------------------------------------------------------------------


def test_retrieval_is_bounded_per_provider() -> None:
    src = _src()
    # Brave: max 20 queries (bound enforced via planner cap)
    assert "brave_max_queries" in src
    # YouTube: max 10 video queries
    assert "yt_max_video_queries" in src
    assert "yt_max_videos_per_query" in src
    assert "yt_max_comments_per_video" in src


def test_firecrawl_extraction_not_used_unbounded() -> None:
    """8.5G doesn't call Firecrawl directly. If Firecrawl were added
    later, it would be bounded — for now its absence is itself the
    bound."""
    src = _src()
    assert "firecrawl" not in src.lower() or (
        "firecrawl_configured" in src
        and "firecrawl.crawl" not in src.lower()
    )


# ---------------------------------------------------------------------------
# 12. Evidence scoring rejects generic / noisy
# ---------------------------------------------------------------------------


def test_evidence_scoring_rejects_generic_and_below_threshold() -> None:
    src = _src()
    assert "_generic_filler" in src
    assert "reject_generic_only" in src
    assert "reject_below_relevance_threshold" in src


# ---------------------------------------------------------------------------
# 13. Fake LumaLoop usage claims rejected
# ---------------------------------------------------------------------------


def test_fake_target_product_use_rejected_in_retrieval() -> None:
    src = _src()
    assert "scan_unlaunched_product_use_claims" in src
    assert "reject_fake_target_product_use" in src


# ---------------------------------------------------------------------------
# 14. SourceRecord dedupe via content_hash
# ---------------------------------------------------------------------------


def test_source_record_dedupe_via_content_hash() -> None:
    src = _src()
    assert "content_hash" in src
    assert "SourceRecord.source_kind == payload" in src
    assert "SourceRecord.content_hash == payload" in src
    assert "scalar_one_or_none()" in src


# ---------------------------------------------------------------------------
# 15 + 16. Personas are run-scoped / brief-scoped; no global personas
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
    # No tag for global personas
    assert "scope:global" not in src
    assert "not_global_persona:false" not in src


# ---------------------------------------------------------------------------
# 17. Persona diversity gate controls persistence
# ---------------------------------------------------------------------------


def test_persona_diversity_gate_present() -> None:
    src = _src()
    assert "EXPECTED_MIN_COMPRESSED_PERSONAS" in src
    assert "compressed.diff_summary.after_count" in src
    assert "diversity_after_compression_ready" in src


# ---------------------------------------------------------------------------
# 18. Simulation uses persisted personas only
# ---------------------------------------------------------------------------


def test_simulation_loads_run_scoped_personas() -> None:
    src = _src()
    assert "load_run_scoped_agents(" in src
    assert "run_scope_id=run_scope_id" in src


# ---------------------------------------------------------------------------
# 19. Simulation outputs obey allowed stance labels
# ---------------------------------------------------------------------------


def test_simulation_obeys_allowed_stance_labels() -> None:
    src = _src()
    assert "MARKET_ENTRY_STANCES" in src
    assert "if stance_for_db not in MARKET_ENTRY_STANCES:" in src
    assert 'stance_for_db = "needs_more_information"' in src


# ---------------------------------------------------------------------------
# 20. Forecast / verdict scanner catches forbidden claims
# ---------------------------------------------------------------------------


def test_forecast_verdict_scanner_runs_per_round() -> None:
    src = _src()
    assert "scan_forecast_or_verdict_claims" in src
    assert "forecast_or_verdict:" in src


# ---------------------------------------------------------------------------
# 21. Report generator uses simulation output only
# ---------------------------------------------------------------------------


def test_report_generator_used_with_simulation_audit() -> None:
    src = _src()
    assert "aggregate_founder_report(" in src
    assert "simulation_audit=sim_audit_for_report" in src


# ---------------------------------------------------------------------------
# 22 + 23. Report contains required sections + caveats
# ---------------------------------------------------------------------------


def test_report_caveats_required_in_aggregator() -> None:
    """The aggregator's universal `_make_caveats` produces all 9
    required keywords. We don't re-test that here (covered by 8.5F);
    we just confirm the orchestrator USES the aggregator."""
    src = _src()
    assert "render_markdown_report" in src
    assert "evaluate_report_quality" in src


# ---------------------------------------------------------------------------
# 24. Secret scanner gates outputs
# ---------------------------------------------------------------------------


def test_secret_scanner_runs_before_writing_report() -> None:
    src = _src()
    assert "scan_for_secrets" in src
    assert "if not secrets_clean:" in src


# ---------------------------------------------------------------------------
# 25. DB delta audit
# ---------------------------------------------------------------------------


def test_db_delta_audit_present() -> None:
    src = _src()
    assert "db_pre = await _read_table_counts(sm)" in src
    assert "db_post = await _read_table_counts(sm)" in src
    assert '"db_delta_summary"' in src


# ---------------------------------------------------------------------------
# Bonus: --dry-run / --commit modes
# ---------------------------------------------------------------------------


def test_script_supports_dry_run_and_commit_modes() -> None:
    src = _src()
    assert "--dry-run" in src
    assert "--commit" in src
    assert "default=True" in src


# ---------------------------------------------------------------------------
# Bonus: cost-guarded LLM only
# ---------------------------------------------------------------------------


def test_simulation_uses_cost_guarded_chat_only() -> None:
    src = _src()
    assert "cost_guarded_chat" in src
    assert "provider.chat(" not in src
    assert ".structured_output(" not in src
    assert "hard_cap_usd=SIM_HARD_CAP_USD" in src


# ---------------------------------------------------------------------------
# Bonus: drift — no hardcoded LumaLoop persona templates
# ---------------------------------------------------------------------------


def test_no_hardcoded_lumaloop_persona_templates() -> None:
    src = _src()
    forbidden_persona_literals = (
        '"competitor_user_noxgear_tracer2"',
        '"competitor_user_amphipod"',
        '"competitor_user_nathan_reflective_gear"',
        '"competitor_user_flipbelt_lights"',
        '"competitor_user_black_diamond_sprinter_headlamp"',
        '"night_runner_persona"',
        '"cyclist_persona"',
        '"dog_walker_persona"',
    )
    for lit in forbidden_persona_literals:
        assert lit not in src, f"hardcoded persona literal: {lit!r}"


# ---------------------------------------------------------------------------
# Bonus: existing-phase imports still resolve
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
        scan_forecast_or_verdict_claims,
        scan_unlaunched_product_use_claims,
    )
    from assembly.sources.source_expansion_planner import (  # noqa: F401
        generate_source_expansion_plan,
    )
    from assembly.sources.founder_report_generator import (  # noqa: F401
        aggregate_founder_report, render_markdown_report,
        evaluate_report_quality, scan_for_secrets,
    )


# ---------------------------------------------------------------------------
# Bonus: orchestrator imports + universal modules
# ---------------------------------------------------------------------------


def test_orchestrator_imports_only_existing_universal_modules() -> None:
    """Drift: the orchestrator must NOT import any new external lib
    or scraper."""
    src = _src()
    forbidden_imports = (
        "import jina", "import exa", "import dataforseo",
        "import reddit", "import apify", "import praw",
        "from jina", "from exa", "from dataforseo",
        "from reddit", "from apify", "from praw",
        "yt_dlp", "pytube", "scrapetube",
    )
    for s in forbidden_imports:
        assert s not in src, f"forbidden import: {s!r}"


# ---------------------------------------------------------------------------
# Bonus: report quality gate present
# ---------------------------------------------------------------------------


def test_report_quality_gate_present() -> None:
    src = _src()
    assert "report_quality_ready" in src
    assert "READY_FOR_FRESH_END_TO_END_TEST" in src


# ---------------------------------------------------------------------------
# Bonus: anti-fake-claim + forecast gates aggregated end-to-end
# ---------------------------------------------------------------------------


def test_end_to_end_quality_gates_include_anti_fake_and_forecast() -> None:
    src = _src()
    assert '"no_fake_target_product_use"' in src
    assert '"no_forecast_or_verdict"' in src
    assert '"secrets_clean"' in src
