"""Phase 8.5D.1E — persona-set compression + role-slug normalization
tests. Operator scenarios 1-29 covered.

NO live API calls. NO DB writes. Pure unit tests over the
compressor + a static read of the dry-run script.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from assembly.sources.persona_set_compressor import (
    CompressedPersonaCandidate, CompressedPersonaSet,
    CompressionPolicy, CompressionRejection, RoleSlugNormalization,
    compress_persona_set, normalize_role_slug,
    normalize_role_slugs_for_candidates,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "persona_set_compression_dry_run_8_5d_1e.py"
)
PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_set_compressor"
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    candidate_id: str,
    primary_role: str,
    source_record_id: str = "planned::strideshield::brave_search_result::aaa",
    trait_names: list[str] | None = None,
    objections: list[str] | None = None,
    behaviors: list[str] | None = None,
    confidence: str = "high",
    evidence_strength: str = "strong",
) -> dict:
    if trait_names is None:
        trait_names = ["preference_a", "objection_b"]
    return {
        "candidate_id": candidate_id,
        "scope": "brief_scoped",
        "persistence_status": "dry_run_only",
        "target_brief": "strideshield",
        "generated_for_phase": "8.5D.1D",
        "not_global_persona": True,
        "inferred_persona_role": primary_role,
        "secondary_persona_roles": [],
        "role_inference_basis": ["evidence-tied basis"],
        "segment_label": primary_role.replace("_", " "),
        "source_record_ids": [source_record_id],
        "evidence_summary": "evidence summary",
        "evidence_snippets": ["I tried Body Glide on my heels."],
        "inferred_traits": [
            {
                "trait_name": tn, "trait_value": "value_for_" + tn,
                "evidence_source_record_id": source_record_id,
                "evidence_excerpt": "excerpt",
                "confidence": "high", "caveat": None,
            }
            for tn in trait_names
        ],
        "inferred_preferences": ["pref"],
        "inferred_objections": objections or ["price too high"],
        "inferred_behaviors": behaviors or ["uses for long runs"],
        "hypothetical_target_product_reaction": "would compare to Body Glide",
        "confidence": confidence,
        "evidence_strength": evidence_strength,
        "caveats": [],
        "simulation_usefulness_summary": "useful",
        "persistence_recommendation": "DEFER",
    }


def _make_planned_source(
    *,
    sid: str,
    matched_terms: list[str] | None = None,
    provider: str = "brave_search",
) -> dict:
    return {
        "planned_source_record_id_synthetic": sid,
        "source_kind": (
            "brave_search_result" if provider == "brave_search"
            else "youtube_video_result"
            if provider == "youtube_data_api"
            else "amazon_reviews_2023_local"
        ),
        "source_url": f"https://example.com/{sid}",
        "content_preview": "preview",
        "content_length": 100,
        "content_hash": "0" * 64,
        "language": "en",
        "metadata": {
            "provider": provider,
            "matched_terms": matched_terms or [
                "positive:balm", "competitor:Body Glide",
            ],
            "persona_value_roles": [],
        },
        "ingested_by": "dry_run",
        "compliance_tag": "public_html",
        "captured_at": "2026-05-06T00:00:00+00:00",
        "pii_redaction_status": "passed",
        "sensitive_scan_status": "passed",
        "user_handle_hash": None,
    }


# ---------------------------------------------------------------------------
# 1. Role slug normalizer exists
# ---------------------------------------------------------------------------


def test_role_slug_normalizer_exists() -> None:
    sig = inspect.signature(normalize_role_slug)
    assert list(sig.parameters.keys()) == ["role"]
    assert isinstance(normalize_role_slug("a"), str)


# ---------------------------------------------------------------------------
# 2. Apostrophes + punctuation handled generically
# ---------------------------------------------------------------------------


def test_normalizer_strips_apostrophes_and_collapses_underscores() -> None:
    assert (
        normalize_role_slug("competitor_user_squirrel's_nut_butter")
        == "competitor_user_squirrels_nut_butter"
    )
    # Curly apostrophe too
    assert (
        normalize_role_slug("competitor_user_squirrel’s_nut_butter")
        == "competitor_user_squirrels_nut_butter"
    )


def test_normalizer_collapses_runs_of_punctuation() -> None:
    assert (
        normalize_role_slug("Competitor User: Body--Glide!!")
        == "competitor_user_body_glide"
    )


def test_normalizer_lowercases() -> None:
    assert (
        normalize_role_slug("Competitor_User_Body_Glide")
        == "competitor_user_body_glide"
    )


def test_normalizer_is_idempotent() -> None:
    inputs = (
        "competitor_user_squirrel's_nut_butter",
        "Competitor User: Body--Glide!!",
        "price_skeptic",
        "use_case_focused_buyer",
    )
    for s in inputs:
        once = normalize_role_slug(s)
        twice = normalize_role_slug(once)
        assert once == twice, f"not idempotent for {s!r}"


def test_normalizer_handles_empty_and_none() -> None:
    assert normalize_role_slug("") == ""
    assert normalize_role_slug(None) == ""  # type: ignore[arg-type]


def test_normalizer_preserves_role_prefix() -> None:
    # Must keep `competitor_user_` / `substitute_user_` prefix.
    assert normalize_role_slug(
        "competitor_user_TRAIL_TOES",
    ).startswith("competitor_user_")
    assert normalize_role_slug(
        "substitute_user_coffee",
    ).startswith("substitute_user_")


# ---------------------------------------------------------------------------
# 3. Normalizer does NOT hardcode StrideShield competitor names
# ---------------------------------------------------------------------------


def test_normalizer_pkg_has_no_hardcoded_brand_or_category_names() -> None:
    forbidden = (
        "strideshield", "triton", "solara",
        "body glide", "body_glide", "megababe", "trail toes",
        "trail_toes", "squirrel", "gold bond", "red bull",
        "monster", "celsius", "gatorade",
        "anti-blister", "anti_blister", "anti-chafe", "anti_chafe",
        "energy drink", "amazon_reviews_2023",
        "beauty_and_personal_care", "health_and_household",
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
                f"hardcoded {term!r} in compressor pkg {f.name}"
            )


# ---------------------------------------------------------------------------
# 4. PersonaSetCompressor exists
# ---------------------------------------------------------------------------


def test_compress_persona_set_exists_and_callable() -> None:
    sig = inspect.signature(compress_persona_set)
    params = set(sig.parameters.keys())
    expected = {
        "candidates", "planned_source_records",
        "target_brief_id", "product_name", "launch_state",
        "generated_for_phase", "min_traits",
        "max_target_range",
        "min_behavioral_differential",
    }
    assert expected.issubset(params)
    assert CompressedPersonaSet.model_config.get("extra") == "forbid"


# ---------------------------------------------------------------------------
# 5. Accepts candidates + planned_source_records
# ---------------------------------------------------------------------------


def test_compressor_accepts_candidates_and_planned_records() -> None:
    cands = [_make_candidate(
        candidate_id="c1",
        primary_role="competitor_user_body_glide",
    )]
    srs = [_make_planned_source(
        sid="planned::strideshield::brave_search_result::aaa",
    )]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert isinstance(out, CompressedPersonaSet)
    assert len(out.compressed_candidates) == 1


# ---------------------------------------------------------------------------
# 6. Compressor groups by normalized role / theme / provider / traits / etc
# ---------------------------------------------------------------------------


def test_compression_policy_lists_grouping_and_rules() -> None:
    cands = [_make_candidate(
        candidate_id="c1",
        primary_role="competitor_user_body_glide",
    )]
    out = compress_persona_set(
        candidates=cands, planned_source_records=[],
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    p = out.policy
    grouping = " ".join(p.grouping_dimensions)
    assert "normalized_primary_role" in grouping
    assert "evidence_theme" in grouping
    assert "source_provider_family" in grouping
    assert "trait_signature" in grouping
    assert "objection_signature" in grouping
    assert p.selection_rules
    assert p.rejection_rules


# ---------------------------------------------------------------------------
# 7 + 9. Strongest per role first; same-role same-theme triple-duplicate rejected
# ---------------------------------------------------------------------------


def test_strongest_candidate_per_role_kept_first() -> None:
    """Two candidates, same role, same theme. Strongest by quality
    score is kept; the other is rejected as duplicate."""
    sid = "planned::strideshield::brave_search_result::aaa"
    cands = [
        _make_candidate(
            candidate_id="weak", primary_role="competitor_user_body_glide",
            source_record_id=sid,
            confidence="medium", evidence_strength="moderate",
        ),
        _make_candidate(
            candidate_id="strong", primary_role="competitor_user_body_glide",
            source_record_id=sid,
            confidence="high", evidence_strength="strong",
        ),
    ]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    kept_ids = {c.candidate_id for c in out.compressed_candidates}
    rej_ids = {r.candidate_id for r in out.rejected_candidates}
    assert "strong" in kept_ids
    assert "weak" in rej_ids


def test_same_role_same_theme_same_provider_is_rejected_as_duplicate() -> None:
    """Two candidates with same (role, theme, provider) → second is
    rejected as duplicate_role_and_theme."""
    sid_a = "planned::strideshield::brave_search_result::aaa"
    sid_b = "planned::strideshield::brave_search_result::bbb"
    cands = [
        _make_candidate(
            candidate_id="c1", primary_role="competitor_user_body_glide",
            source_record_id=sid_a,
        ),
        _make_candidate(
            candidate_id="c2", primary_role="competitor_user_body_glide",
            source_record_id=sid_b,
            trait_names=["preference_a", "objection_c"],
            objections=["different objection words here"],
        ),
    ]
    srs = [
        _make_planned_source(
            sid=sid_a,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        ),
        _make_planned_source(
            sid=sid_b,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        ),
    ]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert len(out.compressed_candidates) == 1
    assert any(
        r.rejection_reason == "duplicate_role_and_theme"
        for r in out.rejected_candidates
    )


# ---------------------------------------------------------------------------
# 8. Second same-role candidate admitted if behavioral differential ≥ threshold
# ---------------------------------------------------------------------------


def test_second_same_role_admitted_with_meaningful_difference() -> None:
    """Two same-role candidates with different theme + different
    provider + different trait names should be admitted."""
    sid_a = "planned::strideshield::brave_search_result::aaa"
    sid_b = "planned::strideshield::youtube_video_result::bbb"
    cands = [
        _make_candidate(
            candidate_id="c1", primary_role="competitor_user_body_glide",
            source_record_id=sid_a,
            trait_names=["price_dimension", "use_case_dimension"],
            objections=["price too high"],
            behaviors=["buys at retail"],
        ),
        _make_candidate(
            candidate_id="c2", primary_role="competitor_user_body_glide",
            source_record_id=sid_b,
            trait_names=["safety_dimension", "performance_dimension"],
            objections=["caused skin irritation"],
            behaviors=["watched comparison videos"],
        ),
    ]
    srs = [
        _make_planned_source(
            sid=sid_a, provider="brave_search",
            matched_terms=["positive:balm", "competitor:Body Glide"],
        ),
        _make_planned_source(
            sid=sid_b, provider="youtube_data_api",
            matched_terms=[
                "positive:balm", "competitor:Body Glide",
                "use_case:long runs",
            ],
        ),
    ]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    # Different provider → different theme keys (use_case::… vs
    # competitor::…) → triple is NOT a duplicate. Then trait names
    # are 100% disjoint → behavioral_differential = 5.
    kept_ids = {c.candidate_id for c in out.compressed_candidates}
    assert kept_ids == {"c1", "c2"}


# ---------------------------------------------------------------------------
# 10. Weak candidates not accepted just for count
# ---------------------------------------------------------------------------


def test_compressor_rejects_below_quality_floor() -> None:
    """A candidate with weak evidence + low confidence is rejected
    even though no role yet exists for it."""
    sid = "planned::strideshield::brave_search_result::aaa"
    cands = [
        _make_candidate(
            candidate_id="cweak", primary_role="competitor_user_trail_toes",
            source_record_id=sid,
            confidence="low", evidence_strength="weak",
        ),
    ]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert out.compressed_candidates == []
    assert any(
        r.rejection_reason == "below_quality_floor"
        for r in out.rejected_candidates
    )


# ---------------------------------------------------------------------------
# 11. Source evidence preserved
# ---------------------------------------------------------------------------


def test_compressor_preserves_source_record_ids() -> None:
    sid = "planned::strideshield::brave_search_result::aaa"
    cands = [_make_candidate(
        candidate_id="c1",
        primary_role="competitor_user_body_glide",
        source_record_id=sid,
    )]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert out.compressed_candidates[0].source_record_ids == [sid]


# ---------------------------------------------------------------------------
# 12. Compressed candidates remain brief-scoped/run-scoped/dry-run-only
# ---------------------------------------------------------------------------


def test_compressed_candidates_keep_brief_run_dry_invariants() -> None:
    sid = "planned::strideshield::brave_search_result::aaa"
    cands = [_make_candidate(
        candidate_id="c1",
        primary_role="competitor_user_body_glide",
        source_record_id=sid,
    )]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    c = out.compressed_candidates[0]
    assert c.scope == "brief_scoped"
    assert c.persistence_status == "dry_run_only"
    assert c.not_global_persona is True
    assert c.target_brief == "strideshield"


# ---------------------------------------------------------------------------
# 13. Rejects fake StrideShield usage claims
# ---------------------------------------------------------------------------


def test_compressor_rejects_fake_target_product_use() -> None:
    sid = "planned::strideshield::brave_search_result::aaa"
    cand = _make_candidate(
        candidate_id="fake", primary_role="competitor_user_body_glide",
        source_record_id=sid,
    )
    cand["evidence_snippets"] = ["I bought StrideShield last week."]
    cand["evidence_summary"] = "I tried StrideShield."
    cands = [cand]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert out.compressed_candidates == []
    assert any(
        r.rejection_reason == "fake_target_product_use"
        for r in out.rejected_candidates
    )


# ---------------------------------------------------------------------------
# 14. Compressor does not create global personas
# ---------------------------------------------------------------------------


def test_compressor_rejects_global_persona_inputs() -> None:
    sid = "planned::strideshield::brave_search_result::aaa"
    cand = _make_candidate(
        candidate_id="global", primary_role="competitor_user_body_glide",
        source_record_id=sid,
    )
    cand["not_global_persona"] = False
    cands = [cand]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert out.compressed_candidates == []
    assert any(
        r.rejection_reason == "non_brief_scoped_or_global_persona"
        for r in out.rejected_candidates
    )


def test_compressor_rejects_below_min_traits() -> None:
    sid = "planned::strideshield::brave_search_result::aaa"
    cand = _make_candidate(
        candidate_id="thin", primary_role="competitor_user_body_glide",
        source_record_id=sid, trait_names=["only_one"],
    )
    cands = [cand]
    srs = [_make_planned_source(sid=sid)]
    out = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert out.compressed_candidates == []
    assert any(
        r.rejection_reason == "below_min_traits"
        for r in out.rejected_candidates
    )


# ---------------------------------------------------------------------------
# 15. Diversity evaluator is rerun after compression (script-level)
# ---------------------------------------------------------------------------


def test_script_reruns_diversity_evaluator_on_compressed_set() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "_evaluate_diversity_on_compressed" in src
    # Result is in the audit
    assert '"diversity_after"' in src or "diversity_after" in src


# ---------------------------------------------------------------------------
# 16 + 17. ready_for_mutating gates
# ---------------------------------------------------------------------------


def test_ready_for_mutating_blocked_when_diversity_not_ready_or_single_provider() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    needle = "ready_for_mutating = ("
    assert needle in src
    snippet = src[src.index(needle):src.index(needle) + 800]
    assert "db_unchanged" in snippet
    assert "fake_use_in_compressed" in snippet
    assert "every_brief_scoped" in snippet
    assert "every_has_evidence" in snippet
    assert "every_has_traits" in snippet
    assert "diversity_ready" in snippet
    assert "multi_provider" in snippet


def test_diversity_ready_means_compressed_evaluator_returns_ready() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "diversity_ready = (" in src
    assert '"READY"' in src


# ---------------------------------------------------------------------------
# 18. Script reads the 8.5D.1D audit
# ---------------------------------------------------------------------------


def test_script_reads_8_5d_1d_audit() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "fresh_product_source_expansion_dry_run_8_5d_1d.json" in src


# ---------------------------------------------------------------------------
# 19 + 20 + 21. No external API calls
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


def test_script_does_not_import_external_api_libs() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "httpx.", "requests.", "aiohttp.",
        "anthropic", "openai", "tavily", "firecrawl",
        "yt_dlp", "pytube", "scrapetube",
    )
    for s in forbidden:
        assert s.lower() not in src.lower(), f"forbidden lib: {s!r}"


# ---------------------------------------------------------------------------
# 22-26. No DB writes
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
# 27. Existing imports still resolve
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.persona_role_planner import (  # noqa: F401
        EffectiveSourceRecord, PersonaCandidate, PersonaCandidatePlanner,
        validate_launch_state_claims,
    )
    from assembly.sources.ingestion_policy import (  # noqa: F401
        UNIVERSAL_GUARDRAILS, apply_diversity_aware_reranking,
        decide_candidates, generate_ingestion_policy,
    )
    from assembly.sources.persona_diversity_evaluator import (  # noqa: F401
        DiversityRecommendation, evaluate_persona_diversity,
    )
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        ProductBriefForPlanning, generate_anchor_plan,
    )
    from assembly.sources.source_expansion_planner import (  # noqa: F401
        generate_source_expansion_plan,
    )
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set, normalize_role_slug,
    )


# ---------------------------------------------------------------------------
# Bonus: determinism
# ---------------------------------------------------------------------------


def test_compression_is_deterministic_for_same_inputs() -> None:
    sid = "planned::strideshield::brave_search_result::aaa"
    cands = [_make_candidate(
        candidate_id="c1",
        primary_role="competitor_user_body_glide",
        source_record_id=sid,
    )]
    srs = [_make_planned_source(sid=sid)]
    o1 = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    o2 = compress_persona_set(
        candidates=cands, planned_source_records=srs,
        target_brief_id="strideshield", product_name="StrideShield",
        launch_state="unlaunched",
    )
    assert o1.plan_id == o2.plan_id
    # Same kept candidate IDs (modulo timestamp)
    assert (
        [c.candidate_id for c in o1.compressed_candidates]
        == [c.candidate_id for c in o2.compressed_candidates]
    )


# ---------------------------------------------------------------------------
# Bonus: extra=forbid
# ---------------------------------------------------------------------------


def test_compressed_persona_set_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        CompressedPersonaSet(
            target_brief_id="x", product_name="x",
            launch_state="unlaunched", generated_for_phase="x",
            plan_id="x",
            policy=CompressionPolicy(
                grouping_dimensions=[], selection_rules=[],
                rejection_rules=[], quality_floor={},
            ),
            compressed_candidates=[], rejected_candidates=[],
            diff_summary={  # type: ignore[arg-type]
                "before_count": 0, "after_count": 0, "rejected_count": 0,
                "roles_before": [], "roles_after": [],
                "duplicate_role_clusters_before": 0,
                "duplicate_role_clusters_after": 0,
                "provider_families_before": [],
                "provider_families_after": [],
                "diversity_score_before": 0.0,
                "diversity_score_after": 0.0,
                "competitor_concentration_before": 0.0,
                "competitor_concentration_after": 0.0,
            },
            rationale=[], caveats=[],
            generated_at="2026-05-06T00:00:00+00:00",
            unexpected_extra="boom",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Bonus: normalization audit shape
# ---------------------------------------------------------------------------


def test_normalize_role_slugs_for_candidates_returns_audit_rows() -> None:
    cands = [
        _make_candidate(
            candidate_id="c1",
            primary_role="competitor_user_squirrel's_nut_butter",
        ),
        _make_candidate(
            candidate_id="c2",
            primary_role="competitor_user_body_glide",  # already clean
        ),
    ]
    role_map, rows = normalize_role_slugs_for_candidates(cands)
    assert role_map["competitor_user_squirrel's_nut_butter"] == (
        "competitor_user_squirrels_nut_butter"
    )
    assert role_map["competitor_user_body_glide"] == (
        "competitor_user_body_glide"
    )
    # Only the changed one should appear in the rows list
    changed_originals = {r.original_role for r in rows}
    assert "competitor_user_squirrel's_nut_butter" in changed_originals
    assert "competitor_user_body_glide" not in changed_originals
    # Audit row carries the affected candidate_id
    sq_row = next(
        r for r in rows
        if r.original_role == "competitor_user_squirrel's_nut_butter"
    )
    assert "c1" in sq_row.affected_candidate_ids
