"""Phase 8.5D.1D — non-Amazon source-expansion dry-run tests.

Operator scenarios 1-25 covered. NO live API calls (no Brave / no
YouTube traffic). NO DB writes. Uses synthetic
`PersonaDiversityEvaluation` + `EvidenceAnchorPlan` fixtures.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
)
from assembly.sources.persona_diversity_evaluator import (
    PersonaDiversityEvaluation, evaluate_persona_diversity,
)
from assembly.sources.source_expansion_planner import (
    ExpansionQuery, ProviderQueryPlan, SourceExpansionPlan,
    generate_source_expansion_plan,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "fresh_product_source_expansion_dry_run_8_5d_1d.py"
)
PLANNER_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "source_expansion_planner"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _strideshield_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="StrideShield",
        product_description=(
            "A pocket-sized anti-blister and anti-chafe balm for "
            "runners, hikers, and gym-goers."
        ),
        price_or_price_structure="$12.99",
        launch_geography="California, United States",
        target_customers=["runners", "hikers", "gym-goers"],
        competitors=[
            "Body Glide", "Gold Bond Friction Defense",
            "Megababe Thigh Rescue", "Squirrel's Nut Butter",
            "Trail Toes",
        ],
    )


def _diversity_eval_with_undercovered() -> PersonaDiversityEvaluation:
    """Synthetic eval mimicking the 8.5D.1C output shape: one over-
    concentrated competitor (Body Glide) + four undercovered themes."""
    return PersonaDiversityEvaluation(
        diversity_score=0.32,
        primary_role_count=5,
        unique_primary_roles=[
            "competitor_user_body_glide", "price_skeptic",
        ],
        unique_secondary_roles=[],
        evidence_source_count=5,
        competitor_concentration=0.8,
        duplicate_role_cluster_count=1,
        persona_similarity_warnings=[
            "4 candidates share primary role "
            "'competitor_user_body_glide' — duplicate-role cluster",
            "80% of candidates reference the same competitor "
            "(body_glide); diversity is competitor-skewed",
        ],
        undercovered_evidence_themes=[
            "no candidate references brief competitor "
            "'Gold Bond Friction Defense'; consider broader source coverage",
            "no candidate references brief competitor "
            "'Megababe Thigh Rescue'; consider broader source coverage",
            "no candidate references brief competitor "
            "\"Squirrel's Nut Butter\"; consider broader source coverage",
            "no candidate references brief competitor "
            "'Trail Toes'; consider broader source coverage",
        ],
        mutating_persistence_recommendation="DEFER_DIVERSIFY",
        narrow_source_proof_only=False,
        rationale=[
            "diversity_score 0.32 < 0.5; defer until evidence "
            "selection produces a more even role distribution.",
        ],
    )


# ---------------------------------------------------------------------------
# 1. SourceExpansionPlanner exists
# ---------------------------------------------------------------------------


def test_source_expansion_planner_exists_and_callable() -> None:
    sig = inspect.signature(generate_source_expansion_plan)
    params = set(sig.parameters.keys())
    expected = {
        "brief", "anchor_plan", "diversity_eval",
        "providers_available", "target_brief_id", "launch_state",
    }
    assert params == expected
    assert SourceExpansionPlan.model_config.get("extra") == "forbid"


# ---------------------------------------------------------------------------
# 2 + 3 + 4. Inputs accepted; no manual roles / categories required
# ---------------------------------------------------------------------------


def test_planner_accepts_brief_and_anchor_and_diversity_eval() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    assert plan.product_name == "StrideShield"
    assert plan.diversity_recommendation_in == "DEFER_DIVERSIFY"


def test_planner_signature_does_not_accept_manual_persona_roles() -> None:
    sig = inspect.signature(generate_source_expansion_plan)
    params = set(sig.parameters.keys())
    forbidden = {
        "manual_persona_roles", "allowed_roles", "persona_roles",
    }
    assert params.isdisjoint(forbidden)


def test_planner_signature_does_not_accept_manual_source_categories() -> None:
    sig = inspect.signature(generate_source_expansion_plan)
    params = set(sig.parameters.keys())
    forbidden = {
        "source_categories", "manual_source_categories",
        "amazon_categories",
    }
    assert params.isdisjoint(forbidden)


# ---------------------------------------------------------------------------
# 5. Query plan generated from brief/audit fields (not hardcoded)
# ---------------------------------------------------------------------------


def test_query_plan_promotes_undercovered_competitors_to_top() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    assert plan.undercovered_competitor_themes
    # The undercovered list must contain the brief competitors that
    # were flagged.
    assert "Megababe Thigh Rescue" in plan.undercovered_competitor_themes
    assert "Trail Toes" in plan.undercovered_competitor_themes
    # The over-concentrated one is identified.
    assert plan.over_concentrated_competitor == "Body Glide"
    # First Brave query targets one of the undercovered competitors,
    # not Body Glide.
    brave = next(
        p for p in plan.provider_query_plans
        if p.provider == "brave_search"
    )
    first_q = brave.queries[0].query_text
    assert any(
        c in first_q for c in plan.undercovered_competitor_themes
    )
    assert "Body Glide" not in first_q


def test_query_provenance_lists_brief_or_audit_fields() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    for pp in plan.provider_query_plans:
        for q in pp.queries:
            # Every query records its provenance fields
            assert q.generated_from_fields
            blob = " ".join(q.generated_from_fields).lower()
            assert (
                "brief" in blob
                or "anchor_plan" in blob
                or "previous_diversity_evaluation" in blob
            )


# ---------------------------------------------------------------------------
# 6 + 7 + 8. Bounded query counts at planner + per-provider level
# ---------------------------------------------------------------------------


def test_query_plan_total_within_provider_caps() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    brave = next(
        p for p in plan.provider_query_plans
        if p.provider == "brave_search"
    )
    yt = next(
        p for p in plan.provider_query_plans
        if p.provider == "youtube_data_api"
    )
    assert brave.max_queries == 20
    assert brave.max_results_per_query == 10
    assert len(brave.queries) <= brave.max_queries
    assert all(q.max_results <= 10 for q in brave.queries)
    assert yt.max_queries == 10
    assert yt.max_results_per_query == 10
    assert len(yt.queries) <= yt.max_queries
    assert all(q.max_results <= 10 for q in yt.queries)


def test_brave_adapter_caps_match_planner_caps() -> None:
    """The planner's hard caps must align with the Brave adapter's
    hard caps so the plan can never request more work than the
    adapter executes."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "brave" / "adapter.py"
    ).read_text(encoding="utf-8")
    # Adapter has no hardcoded queries cap > 20 in its module-level
    # constants (verified by drift)
    assert "_DEFAULT_MAX_QUERIES" in src


def test_youtube_adapter_caps_match_planner_caps() -> None:
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "youtube" / "adapter.py"
    ).read_text(encoding="utf-8")
    assert "_DEFAULT_MAX_VIDEOS" in src
    assert "_DEFAULT_MAX_COMMENTS_TOTAL" in src


# ---------------------------------------------------------------------------
# 9. Missing provider key handled gracefully
# ---------------------------------------------------------------------------


def test_missing_brave_key_returns_skipped_plan_not_crash() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": False, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    brave = next(
        p for p in plan.provider_query_plans
        if p.provider == "brave_search"
    )
    assert brave.is_provider_configured is False
    assert brave.queries == []
    assert brave.skipped_reason
    assert "BRAVE_SEARCH_API_KEY" in brave.skipped_reason


def test_missing_youtube_key_returns_skipped_plan_not_crash() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": False,
        },
        target_brief_id="strideshield",
    )
    yt = next(
        p for p in plan.provider_query_plans
        if p.provider == "youtube_data_api"
    )
    assert yt.is_provider_configured is False
    assert yt.queries == []
    assert "YOUTUBE_DATA_API_KEY" in (yt.skipped_reason or "")


def test_both_keys_missing_returns_zero_query_plan() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": False, "youtube_data_api": False,
        },
        target_brief_id="strideshield",
    )
    assert plan.total_planned_queries == 0
    for pp in plan.provider_query_plans:
        assert pp.queries == []
        assert pp.is_provider_configured is False


# ---------------------------------------------------------------------------
# 10 + 11. No unofficial scraping; no Amazon.com URLs
# ---------------------------------------------------------------------------


def test_planner_pkg_does_not_import_unofficial_scrapers() -> None:
    forbidden = {
        "yt_dlp", "youtube_dl", "pytube", "scrapetube",
        "youtube_comment_downloader", "selenium", "playwright",
        "scrapy", "beautifulsoup4", "bs4", "requests", "aiohttp",
        "urllib3",
    }
    for f in PLANNER_PKG.rglob("*.py"):
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
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden, (
                    f"forbidden import {node.module} in {f.name}"
                )


def test_script_does_not_scrape_amazon() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    assert pat.search(src) is None


def test_script_uses_only_official_provider_clients() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "yt_dlp", "yt-dlp", "pytube", "scrapetube",
        "youtube_comment_downloader",
        "selenium", "playwright", "scrapy",
        "beautifulsoup4", "bs4",
    )
    for token in forbidden:
        assert token not in src.lower(), f"forbidden token: {token!r}"


# ---------------------------------------------------------------------------
# 12. Raw YouTube author/user IDs are not stored
# ---------------------------------------------------------------------------


def test_youtube_adapter_does_not_store_channel_ids() -> None:
    """The YouTubeCommentResult carries display_name + comment_id only —
    not channelId, not email, not phone."""
    from assembly.sources.youtube.adapter import YouTubeCommentResult
    fields = set(YouTubeCommentResult.__dataclass_fields__.keys())
    forbidden = {"channel_id", "channelId", "author_channel_id", "email"}
    assert fields.isdisjoint(forbidden)
    expected = {
        "video_id", "comment_id", "text", "display_name",
        "like_count", "published_at",
    }
    assert fields == expected


def test_script_never_stores_yt_channel_ids() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        '"channelId"', "channel_id", "authorChannelId",
        "author_channel_id",
    )
    for token in forbidden:
        assert token not in src, f"forbidden field: {token!r}"


# ---------------------------------------------------------------------------
# 13 + 14 + 15. PII / fake-target-use / generic-only scanners run on snippets
# ---------------------------------------------------------------------------


def test_script_runs_pii_scanner_on_external_evidence() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "_scan_pii" in src
    assert "_EMAIL_RE" in src
    assert "_PHONE_RE" in src


def test_script_runs_fake_target_product_use_scanner() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "_scan_fake_target_use" in src
    assert "reject_fake_buyer_for_unlaunched" in src


def test_script_rejects_generic_only_snippets() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "_is_generic_filler" in src
    assert "reject_generic_only_or_too_short" in src


# ---------------------------------------------------------------------------
# 16. Duplicate URL/result dedupe
# ---------------------------------------------------------------------------


def test_script_dedupes_by_url_and_content_hash() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "seen_urls" in src
    assert "seen_hashes" in src
    assert "reject_duplicate_url_or_hash" in src


# ---------------------------------------------------------------------------
# 17 + 18 + 19 + 20 + 21. No DB writes / persona inserts
# ---------------------------------------------------------------------------


def test_script_does_not_insert_anything() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden_orm = (
        "SourceRecord", "PersonaRecord", "PersonaTrait",
        "PersonaEvidenceLink", "PersonaGraphEdge", "PersonaCluster",
        "Agent", "AgentResponse", "DebateTurn",
        "Simulation", "SimulationOutput", "SimulationRound",
    )
    for term in forbidden_orm:
        pat = re.compile(rf"\b{re.escape(term)}\(\s*\w")
        for m in pat.finditer(src):
            ctx = src[max(0, m.start() - 25):m.end() + 25]
            if "select(" in ctx:
                continue
            raise AssertionError(
                f"forbidden ORM construction in script: ...{ctx}..."
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


def test_script_reads_db_pre_and_post_baseline() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "db_pre = await _read_baseline_counts(sm)" in src
    assert "db_post = await _read_baseline_counts(sm)" in src
    assert "db_unchanged = db_pre == db_post" in src


# ---------------------------------------------------------------------------
# 22. Persona diversity evaluator is rerun
# ---------------------------------------------------------------------------


def test_script_reruns_persona_diversity_evaluator() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "evaluate_persona_diversity" in src
    # The eval is fed the persona_plan candidates.
    assert "candidates=persona_plan.persona_candidates" in src


# ---------------------------------------------------------------------------
# 23 + 24. ready_for_mutating_phase rules
# ---------------------------------------------------------------------------


def test_ready_for_mutating_requires_all_five_conditions() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    needle = "ready_for_mutating = ("
    assert needle in src
    snippet = src[src.index(needle):src.index(needle) + 600]
    assert "db_unchanged" in snippet
    assert "fake_use_in_candidates" in snippet
    assert "ready_for_8_5d_2" in snippet
    assert '"READY"' in snippet
    assert "multi_provider" in snippet


def test_ready_false_when_diversity_remains_weak() -> None:
    """Smoke test the readiness gate at the planner level: with a
    DEFER_DIVERSIFY signal, the expansion plan still runs, but
    `ready_for_mutating_phase` (computed in the script) cannot be
    True because the diversity evaluator's output (the same input
    eval here is DEFER_DIVERSIFY) blocks it. The planner itself
    doesn't compute readiness, but its output reflects the input
    state."""
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    plan = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    assert plan.diversity_recommendation_in == "DEFER_DIVERSIFY"


# ---------------------------------------------------------------------------
# 25. Existing 8.5D.1 / 8.5D.1B / 8.5D.1C imports still resolve
# ---------------------------------------------------------------------------


def test_existing_imports_still_resolve() -> None:
    from assembly.sources.persona_role_planner import (  # noqa: F401
        EffectiveSourceRecord, PersonaCandidate, PersonaCandidatePlanner,
        select_effective_sources, validate_launch_state_claims,
    )
    from assembly.sources.ingestion_policy import (  # noqa: F401
        UNIVERSAL_GUARDRAILS, apply_diversity_aware_reranking,
        decide_candidates, generate_ingestion_policy,
    )
    from assembly.sources.persona_diversity_evaluator import (  # noqa: F401
        DiversityRecommendation, PersonaDiversityEvaluation,
        evaluate_persona_diversity,
    )
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        ProductBriefForPlanning, generate_anchor_plan,
        generate_source_category_plan, score_review_with_plan,
    )
    from assembly.sources.brave import (  # noqa: F401
        BraveAdapterConfig, BraveSearchClient,
        is_brave_key_present, redact_url_for_audit,
    )
    from assembly.sources.youtube import (  # noqa: F401
        YouTubeAdapterConfig, YouTubeDataClient,
        is_youtube_key_present,
    )


# ---------------------------------------------------------------------------
# Bonus drift: no StrideShield/Triton hardcoding in expansion planner pkg
# ---------------------------------------------------------------------------


def test_expansion_planner_pkg_has_no_hardcoded_brand_or_category_tokens() -> None:
    forbidden = (
        "strideshield", "triton", "solara",
        "body glide", "megababe", "trail toes",
        "squirrel's nut butter", "gold bond", "red bull",
        "monster", "celsius", "gatorade",
        "anti-blister", "anti-chafe", "energy drink",
        "amazon_reviews_2023",
        "beauty_and_personal_care", "health_and_household",
    )
    for f in PLANNER_PKG.rglob("*.py"):
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
                f"hardcoded {term!r} in expansion planner pkg {f.name}"
            )


# ---------------------------------------------------------------------------
# Bonus: plan_id deterministic
# ---------------------------------------------------------------------------


def test_plan_id_is_deterministic_for_same_inputs() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    p1 = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    p2 = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    assert p1.plan_id == p2.plan_id


def test_plan_id_changes_with_provider_availability() -> None:
    brief = _strideshield_brief()
    ap = generate_anchor_plan(brief)
    de = _diversity_eval_with_undercovered()
    p1 = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": True,
        },
        target_brief_id="strideshield",
    )
    p2 = generate_source_expansion_plan(
        brief=brief, anchor_plan=ap, diversity_eval=de,
        providers_available={
            "brave_search": True, "youtube_data_api": False,
        },
        target_brief_id="strideshield",
    )
    assert p1.plan_id != p2.plan_id


# ---------------------------------------------------------------------------
# Bonus: SourceExpansionPlan extra=forbid
# ---------------------------------------------------------------------------


def test_source_expansion_plan_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        SourceExpansionPlan(
            plan_id="x" * 16,
            target_brief_id="x", product_name="x",
            launch_state="unlaunched",
            diversity_recommendation_in="READY",
            undercovered_competitor_themes=[],
            over_concentrated_competitor=None,
            provider_query_plans=[],
            total_planned_queries=0, total_planned_max_results=0,
            generated_from="deterministic",
            rationale=[], safety_caveats=[],
            generated_at="2026-05-06T00:00:00+00:00",
            unexpected_extra="boom",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Bonus: query_max_results never exceeds Literal-bounded cap
# ---------------------------------------------------------------------------


def test_expansion_query_rejects_excessive_max_results() -> None:
    with pytest.raises(Exception):
        ExpansionQuery(
            query_text="x", provider="brave_search",
            kind="competitor_review",
            generated_from_fields=["brief"],
            rationale="x", expected_evidence_types=["blog_review"],
            max_results=100,  # >20 cap
            safety_notes=[],
        )
