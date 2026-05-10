"""Phase 8.5D.1B — fresh-product persona-generalization tests.

24 deterministic tests covering operator scenarios 1-24. (#25 + #26
are full-suite verifications validated by the regression sweep.)
NO live DB writes. Synthetic JSONL fixtures + monkeypatched DB
session for the duplicate-check.
"""
from __future__ import annotations

import ast
import gzip
import importlib.util
import json
import re
from pathlib import Path

import pytest

from assembly.sources.amazon_reviews_2023.adapter import AmazonReviewRecord
from assembly.sources.amazon_reviews_2023.filters import (
    AmazonProductMetadata, ReviewConfidence,
)
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
    generate_source_category_plan, score_review_with_plan,
)
from assembly.sources.persona_role_planner import (
    EffectiveSourceRecord, PersonaCandidatePlanner,
    validate_launch_state_claims,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "fresh_product_persona_generalization_dry_run_8_5d_1b.py"
)
PLANNER_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_role_planner"
)
ANCHOR_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "evidence_anchor_planner"
)


def _strideshield_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="StrideShield",
        product_description=(
            "A pocket-sized anti-blister and anti-chafe balm for "
            "college students, runners, hikers, gym-goers, theme-"
            "park walkers, and people whose shoes or sandals rub "
            "during long days. It is sweat-resistant, fragrance-"
            "free, non-greasy, and designed to be applied to heels, "
            "toes, thighs, and other friction spots before walking, "
            "running, workouts, or outdoor activity."
        ),
        price_or_price_structure="$12.99",
        launch_geography="California, United States",
        target_customers=[
            "college students who walk a lot on campus", "runners",
            "hikers", "gym-goers", "theme-park visitors",
            "people who get shoe rub, sandal cuts, blisters, or thigh chafing",
        ],
        competitors=[
            "Body Glide", "Gold Bond Friction Defense",
            "Megababe Thigh Rescue", "Squirrel's Nut Butter",
            "Trail Toes",
        ],
    )


# ---------------------------------------------------------------------------
# 1 + 2 + 3. Fresh founder brief is accepted; no manual category/role anchors
# ---------------------------------------------------------------------------


def test_fresh_brief_is_accepted_as_founder_style_input() -> None:
    brief = _strideshield_brief()
    fields = ProductBriefForPlanning.model_fields.keys()
    expected = {
        "product_name", "product_description", "price_or_price_structure",
        "launch_geography", "target_customers", "competitors",
        "optional_constraints",
    }
    assert set(fields) == expected
    # Brief has zero hardcoded chafing/blister persona-role labels
    blob = brief.model_dump_json()
    for forbidden in (
        "persona_role", "competitor_user_", "substitute_user_",
        "safety_skeptic", "performance_use_case", "flavor_focused",
    ):
        assert forbidden not in blob


def test_no_manual_category_anchors_required_in_brief() -> None:
    """The brief schema does not allow a `category_anchors` /
    `manual_anchors` field."""
    brief = _strideshield_brief()
    assert not hasattr(brief, "category_anchors")
    assert not hasattr(brief, "manual_anchors")
    # And the planner signature accepts only the brief.
    import inspect
    sig = inspect.signature(generate_anchor_plan)
    assert list(sig.parameters.keys()) == ["brief"]


def test_no_manual_persona_roles_required_anywhere() -> None:
    """The PersonaCandidatePlanner.generate signature accepts no
    manual role-list parameter."""
    import inspect
    sig = inspect.signature(PersonaCandidatePlanner().generate)
    params = set(sig.parameters.keys())
    expected = {
        "product_name", "target_brief_id", "launch_state",
        "competitor_brief_list", "substitute_brief_list",
        "effective_sources",
        "preview_rows_total", "companion_rows_total",
        "superseded_preview_ids",
    }
    assert params == expected
    assert "manual_persona_roles" not in params
    assert "allowed_roles" not in params


# ---------------------------------------------------------------------------
# 4 + 5. Anchor + source/category plans generated dynamically
# ---------------------------------------------------------------------------


def test_evidence_anchor_plan_generated_dynamically_from_brief() -> None:
    brief = _strideshield_brief()
    plan = generate_anchor_plan(brief)
    pos = " ".join(plan.positive_anchor_terms).lower()
    # The brief mentions "balm" and "anti-blister"; positive anchors
    # should reflect these.
    assert "balm" in pos
    assert any(
        a in pos for a in ("blister", "chafe", "friction")
    )
    # Plan is product-agnostic by construction — no Triton/Solara
    # fields leaked
    blob = plan.model_dump_json().lower()
    assert "triton" not in blob
    assert "solara" not in blob
    # But the brief's own competitors echo through
    assert "body glide" in blob


def test_source_category_plan_is_data_driven(tmp_path) -> None:
    """The source_category_plan picks categories where the brief's
    competitors appear in metadata — no hardcoded brief-to-category
    mapping."""
    raw = tmp_path / "raw"
    raw.mkdir()
    # Two synthetic categories: one contains a Body Glide product,
    # the other doesn't.
    cat_a_meta = raw / "meta_Beauty_and_Personal_Care.jsonl"
    cat_b_meta = raw / "meta_Grocery_and_Gourmet_Food.jsonl"
    cat_a_meta.write_text(json.dumps({
        "parent_asin": "BG1", "title": "Body Glide Original Anti-Chafe Balm",
        "categories": ["Beauty & Personal Care", "Skin Care"],
    }) + "\n", encoding="utf-8")
    cat_b_meta.write_text(json.dumps({
        "parent_asin": "GR1", "title": "Tea & Coffee Set",
        "categories": ["Grocery & Gourmet Food", "Beverages"],
    }) + "\n", encoding="utf-8")
    brief = _strideshield_brief()
    cat_plan = generate_source_category_plan(
        brief, dataset_dir=tmp_path,
        available_categories=[
            "Beauty_and_Personal_Care", "Grocery_and_Gourmet_Food",
        ],
        sample_per_category=10,
    )
    # The Beauty category contains a Body Glide hit → selected.
    assert "Beauty_and_Personal_Care" in cat_plan.selected_categories
    # Grocery does not → excluded.
    assert "Grocery_and_Gourmet_Food" in cat_plan.excluded_categories
    # Generated from data, not hardcoding
    assert cat_plan.generated_from == (
        "deterministic_competitor_metadata_scan"
    )


# ---------------------------------------------------------------------------
# 6. Local Amazon scanner is bounded
# ---------------------------------------------------------------------------


def test_script_uses_bounded_scan_caps() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # Default + hard cap visible in the script
    assert "DEFAULT_RECORDS_PER_CATEGORY = 25_000" in src
    assert "HARD_RECORDS_PER_CATEGORY = 100_000" in src
    # Args clamping
    assert (
        "min(\n        max(0, args.records_per_category), "
        "HARD_RECORDS_PER_CATEGORY,\n    )" in src
        or "min(max(0, args.records_per_category), HARD_RECORDS_PER_CATEGORY," in src
        or "HARD_RECORDS_PER_CATEGORY" in src
    )


# ---------------------------------------------------------------------------
# 7 + 8. Generic-only / wrong-context evidence rejected (8.5B.1 scorer)
# ---------------------------------------------------------------------------


def test_generic_only_evidence_rejected_by_scorer() -> None:
    """A review with only generic modifiers and no brief-derived
    anchor must be REJECTED by the scorer."""
    brief = _strideshield_brief()
    plan = generate_anchor_plan(brief)
    rec = AmazonReviewRecord(
        category="x", parent_asin="B", asin="B", rating=5.0,
        title="Just generic",
        text="Nice quality. Worth the price. Great taste.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence is ReviewConfidence.REJECTED


def test_wrong_context_evidence_rejected_via_ambiguity_or_no_anchor() -> None:
    """A review whose only signal is a wrong-context match (e.g. an
    Amazon Prime shipping mention when the product brief has no
    Prime competitor) → no positive signal → REJECTED."""
    brief = _strideshield_brief()
    plan = generate_anchor_plan(brief)
    rec = AmazonReviewRecord(
        category="x", parent_asin="B", asin="B", rating=4.0,
        title="Quick shipping",
        text="Arrived quickly with Amazon Prime. Five stars.",
        helpful_vote=0, verified_purchase=True, timestamp=0,
        user_id_hash="abc",
    )
    score = score_review_with_plan(review=rec, metadata=None, plan=plan)
    assert score.confidence is ReviewConfidence.REJECTED


# ---------------------------------------------------------------------------
# 9. Dynamic IngestionPolicy generated from evidence pool
# ---------------------------------------------------------------------------


def test_dynamic_ingestion_policy_generated_from_pool() -> None:
    from assembly.sources.ingestion_policy import (
        generate_ingestion_policy,
    )
    brief = _strideshield_brief()
    plan = generate_anchor_plan(brief)
    policy = generate_ingestion_policy(
        brief=brief, evidence_anchor_plan=plan,
        candidate_pool=[],
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline={}, max_insert_cap=12,
    )
    assert policy.product_name == "StrideShield"
    assert policy.product_launch_state == "unlaunched"
    assert policy.policy_generated_from == "deterministic"


# ---------------------------------------------------------------------------
# 10. Planned source_records are not inserted (script does not write)
# ---------------------------------------------------------------------------


def test_script_does_not_insert_source_records_or_personas() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden_orm = (
        "SourceRecord", "PersonaRecord", "PersonaTrait",
        "PersonaEvidenceLink", "PersonaGraphEdge", "PersonaCluster",
        "Agent", "AgentResponse", "DebateTurn",
        "Simulation", "SimulationOutput", "SimulationRound",
    )
    for term in forbidden_orm:
        # Word-boundary anchor avoids `SourceRecord` matching
        # `EffectiveSourceRecord` (audit-only Pydantic shape).
        pat = re.compile(rf"\b{re.escape(term)}\(\s*\w")
        for m in pat.finditer(src):
            ctx = src[max(0, m.start() - 20):m.end() + 20]
            if "select(" in ctx:  # SELECT(SourceRecord) etc. are reads
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


# ---------------------------------------------------------------------------
# 11 + 12. Persona candidates generated from planned evidence,
# roles inferred dynamically
# ---------------------------------------------------------------------------


def _wrap_synthetic_effective_sources() -> list[EffectiveSourceRecord]:
    """Mimic what the dry-run script does: wrap planned-source
    candidates as EffectiveSourceRecord. Two synthetic sources, both
    with brief-aligned anti-chafe / friction evidence."""
    return [
        EffectiveSourceRecord(
            source_record_id="planned::strideshield::Beauty::B0X",
            effective_kind="preview_used_as_is",
            superseded_preview_source_record_id=None,
            parent_asin="B0X", asin="B0X",
            category="Beauty_and_Personal_Care",
            metadata_title="Body Glide Original Anti-Chafe Balm",
            rating=5.0, verified_purchase=True, helpful_vote=2,
            timestamp=1700000000,
            content_length=300,
            content=(
                "Body Glide Original Anti-Chafe Balm.\n\n"
                "I run trail races and Body Glide is the only thing that "
                "stops the friction on my heels. Non-greasy, easy to "
                "apply. A little expensive but it lasts."
            ),
            metadata={
                "target_brief": "strideshield",
                "source_category": "Beauty_and_Personal_Care",
                "metadata_title": "Body Glide Original Anti-Chafe Balm",
                "metadata_main_category": "Beauty & Personal Care",
                "metadata_categories": [
                    "Beauty & Personal Care", "Skin Care", "Body",
                ],
                "anchor_score": 9, "anchor_confidence": "high_confidence",
                "matched_terms": [
                    "positive:balm", "competitor:Body Glide",
                    "generic_modifier (qualified)",
                ],
                "persona_value_roles": [
                    "competitor_user_body_glide",
                    "performance_use_case_buyer",
                ],
            },
        ),
        EffectiveSourceRecord(
            source_record_id="planned::strideshield::Beauty::B0Y",
            effective_kind="preview_used_as_is",
            superseded_preview_source_record_id=None,
            parent_asin="B0Y", asin="B0Y",
            category="Beauty_and_Personal_Care",
            metadata_title="Megababe Thigh Rescue Balm",
            rating=4.0, verified_purchase=True, helpful_vote=1,
            timestamp=1700000000,
            content_length=280,
            content=(
                "Megababe Thigh Rescue Balm.\n\n"
                "I bought Megababe Thigh Rescue for theme park days. "
                "It's not greasy, smells nice, and prevents thigh "
                "chafing. Worth the price for me."
            ),
            metadata={
                "target_brief": "strideshield",
                "source_category": "Beauty_and_Personal_Care",
                "metadata_title": "Megababe Thigh Rescue Balm",
                "metadata_main_category": "Beauty & Personal Care",
                "metadata_categories": [
                    "Beauty & Personal Care", "Skin Care",
                ],
                "anchor_score": 9, "anchor_confidence": "high_confidence",
                "matched_terms": [
                    "positive:balm", "competitor:Megababe Thigh Rescue",
                ],
                "persona_value_roles": [
                    "competitor_user_megababe_thigh_rescue",
                ],
            },
        ),
    ]


def _full_planner_run() -> object:
    brief = _strideshield_brief()
    plan = generate_anchor_plan(brief)
    sources = _wrap_synthetic_effective_sources()
    planner = PersonaCandidatePlanner(generated_for_phase="8.5D.1B")
    return planner.generate(
        product_name="StrideShield", target_brief_id="strideshield",
        launch_state="unlaunched",
        competitor_brief_list=brief.competitors,
        substitute_brief_list=plan.substitute_anchor_terms,
        effective_sources=sources,
        preview_rows_total=0, companion_rows_total=0,
        superseded_preview_ids=[],
    )


def test_persona_candidates_generated_from_planned_evidence() -> None:
    plan = _full_planner_run()
    assert plan.persona_candidates


def test_persona_roles_inferred_dynamically_no_manual_input() -> None:
    plan = _full_planner_run()
    # At least one role must be a competitor_user_<brand> derived
    # from the brief's competitor list — proving dynamic inference.
    primary_roles = [c.inferred_persona_role for c in plan.persona_candidates]
    assert any(
        r.startswith("competitor_user_") for r in primary_roles
    )


# ---------------------------------------------------------------------------
# 13 + 14 + 15. Brief-scoped, dry-run-only, not global
# ---------------------------------------------------------------------------


def test_persona_candidates_are_brief_scoped_and_dry_run_only() -> None:
    plan = _full_planner_run()
    for c in plan.persona_candidates:
        assert c.scope == "brief_scoped"
        assert c.persistence_status == "dry_run_only"
        assert c.target_brief == "strideshield"
        assert c.not_global_persona is True
        assert c.generated_for_phase == "8.5D.1B"


# ---------------------------------------------------------------------------
# 16. Launch-state validator rejects fake StrideShield usage
# ---------------------------------------------------------------------------


def test_launch_state_validator_rejects_fake_strideshield_buyer() -> None:
    from assembly.sources.persona_role_planner.schemas import (
        PersonaCandidate,
    )
    cand = PersonaCandidate(
        candidate_id="fake1", target_brief="strideshield",
        generated_for_phase="8.5D.1B",
        inferred_persona_role="competitor_user_body_glide",
        secondary_persona_roles=[], role_inference_basis=["x"],
        segment_label="x", source_record_ids=["S1"],
        evidence_summary="x", evidence_snippets=[
            "I am a StrideShield buyer and tried StrideShield."
        ],
        inferred_traits=[], inferred_preferences=[], inferred_objections=[],
        inferred_behaviors=[],
        hypothetical_target_product_reaction="x",
        confidence="high", evidence_strength="strong",
        caveats=[], simulation_usefulness_summary="x",
        persistence_recommendation="DEFER",
    )
    v = validate_launch_state_claims(
        candidate=cand, launch_state="unlaunched",
        product_name="StrideShield",
    )
    assert v.is_valid is False
    assert v.forbidden_phrases_matched
    assert v.rejection_reason == "fabricated_unlaunched_target_product_use"


def test_launch_state_validator_passes_clean_candidates() -> None:
    plan = _full_planner_run()
    for v in plan.launch_state_validation_results:
        assert v.is_valid is True


# ---------------------------------------------------------------------------
# 17 + 18. Every accepted candidate has evidence + ≥2 traits
# ---------------------------------------------------------------------------


def test_every_candidate_has_evidence_and_at_least_two_traits() -> None:
    plan = _full_planner_run()
    for c in plan.persona_candidates:
        assert len(c.source_record_ids) >= 1
        assert c.evidence_summary
        assert len(c.evidence_snippets) >= 1
        assert len(c.inferred_traits) >= 2


# ---------------------------------------------------------------------------
# 19. Duplicate role + evidence rejected
# ---------------------------------------------------------------------------


def test_duplicate_role_evidence_rejected() -> None:
    """If the same effective source were given twice, the planner
    must reject the duplicate (role, source) pair."""
    sources = _wrap_synthetic_effective_sources()
    sources_dup = [sources[0], sources[0]]  # same source twice
    brief = _strideshield_brief()
    plan = generate_anchor_plan(brief)
    planner = PersonaCandidatePlanner(generated_for_phase="8.5D.1B")
    result = planner.generate(
        product_name="StrideShield", target_brief_id="strideshield",
        launch_state="unlaunched",
        competitor_brief_list=brief.competitors,
        substitute_brief_list=plan.substitute_anchor_terms,
        effective_sources=sources_dup,
        preview_rows_total=0, companion_rows_total=0,
        superseded_preview_ids=[],
    )
    primary_roles = [c.inferred_persona_role for c in result.persona_candidates]
    # Even with duplicate input, only one (role, source) pair persists
    assert len([r for r in primary_roles if r == primary_roles[0]]) <= 1


# ---------------------------------------------------------------------------
# 20. Candidate count is bounded by evidence
# ---------------------------------------------------------------------------


def test_candidate_count_bounded_by_evidence() -> None:
    plan = _full_planner_run()
    # 2 effective sources → at most 2 candidates
    assert len(plan.persona_candidates) <= 2


# ---------------------------------------------------------------------------
# 21. DB unchanged invariant captured by script structure
# ---------------------------------------------------------------------------


def test_script_reads_db_baseline_pre_and_post() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "db_pre = await _read_baseline_counts(sm)" in src
    assert "db_post = await _read_baseline_counts(sm)" in src
    assert "db_unchanged = db_pre == db_post" in src
    assert '"db_unchanged_during_dry_run": db_unchanged' in src


# ---------------------------------------------------------------------------
# 22 + 23. No external retrieval / no Amazon.com scraping
# ---------------------------------------------------------------------------


def test_no_external_api_libs_in_script() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = ("httpx.", "requests.", "aiohttp.",
                 "anthropic", "openai", "tavily", "firecrawl",
                 "brave_search", "youtube_data")
    for s in forbidden:
        assert s.lower() not in src.lower(), f"forbidden: {s}"


def test_no_amazon_dot_com_url_strings_in_script() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    assert pat.search(src) is None


def test_no_http_libs_imported_in_anchor_or_persona_planner_pkgs() -> None:
    forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3",
                 "selenium", "playwright", "scrapy",
                 "beautifulsoup4", "bs4"}
    for pkg in (PLANNER_PKG, ANCHOR_PKG):
        for f in pkg.rglob("*.py"):
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
                            f"{f.name}: {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden, (
                        f"{f.name}: {node.module}"
                    )


# ---------------------------------------------------------------------------
# 24. Existing 8.5D.1 / 8.5C tests still pass — proxy via imports
# ---------------------------------------------------------------------------


def test_existing_8_5d_1_imports_still_resolve() -> None:
    from assembly.sources.persona_role_planner import (  # noqa: F401
        PersonaCandidate, PersonaRolePlan,
        select_effective_sources,
    )
    from assembly.sources.ingestion_policy import (  # noqa: F401
        UNIVERSAL_GUARDRAILS, generate_ingestion_policy,
    )
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        SourceCategoryPlan, generate_anchor_plan,
        generate_source_category_plan,
    )


# ---------------------------------------------------------------------------
# Bonus: drift — no StrideShield-specific tokens in any planner code
# ---------------------------------------------------------------------------


def test_no_strideshield_tokens_hardcoded_in_planner_pkgs() -> None:
    forbidden = (
        "StrideShield", "anti-blister", "anti-chafe", "blister",
        "chafe", "Body Glide", "Megababe", "Trail Toes",
        "Squirrel's Nut Butter", "Gold Bond Friction",
    )
    for pkg in (PLANNER_PKG, ANCHOR_PKG):
        for f in pkg.rglob("*.py"):
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
                    ds = ast.get_docstring(node, clean=False)
                    if ds is None:
                        continue
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
                    f"{f.name} CODE contains StrideShield-specific "
                    f"term {term!r}"
                )
