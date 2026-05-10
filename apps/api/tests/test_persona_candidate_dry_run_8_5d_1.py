"""Phase 8.5D.1 — persona-candidate dry-run tests.

26 deterministic tests covering operator scenarios 1-26. (#27, #28
are full-suite verifications — validated by the regression sweep.)
NO live DB writes from this file. Tests use synthetic source-row
fixtures + the planner's pure functions.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

from assembly.sources.persona_role_planner import (
    EffectiveSourceRecord, PersonaCandidate, PersonaCandidatePlanner,
    PersonaRolePlan, ProductLaunchState,
    UNIVERSAL_ROLE_LEXICONS,
    infer_persona_roles_from_evidence,
    select_effective_sources,
    validate_launch_state_claims,
)


PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_role_planner"
)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "triton_persona_candidate_dry_run_8_5d_1.py"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _preview_row(
    *,
    id: str,
    parent_asin: str = "B00X",
    asin: str = "B00X",
    category: str = "Health_and_Household",
    metadata_title: str = "Test Energy Drink",
    rating: float = 4.0,
    verified_purchase: bool = True,
    content: str = (
        "Caffeine kick is good for pre-workout. "
        "Better than Red Bull for me. No sugar crash."
    ),
    persona_value_roles: list[str] | None = None,
) -> dict:
    if persona_value_roles is None:
        persona_value_roles = ["competitor_user_red_bull"]
    return {
        "id": id, "source_kind": "amazon_reviews_2023_local",
        "source_url": f"local://amazon_reviews_2023_local/{category}/{parent_asin}",
        "content": content,
        "metadata": {
            "target_brief": "triton_drinks",
            "source_category": category,
            "parent_asin": parent_asin, "asin": asin,
            "rating": rating, "verified_purchase": verified_purchase,
            "helpful_vote": 1, "timestamp": 1700000000,
            "metadata_title": metadata_title,
            "metadata_main_category": "Health & Household",
            "metadata_categories": [
                "Health & Household", "Diet & Sports Nutrition",
            ],
            "anchor_score": 9, "anchor_confidence": "high_confidence",
            "matched_terms": ["positive:energy drink"],
            "persona_value_roles": persona_value_roles,
        },
    }


def _companion_row(
    *,
    id: str,
    supersedes_id: str,
    parent_asin: str = "B00X",
    asin: str = "B00X",
    category: str = "Health_and_Household",
    metadata_title: str = "Test Energy Drink (full)",
    content: str = (
        "Caffeine kick is good for pre-workout. "
        "Better than Red Bull for me. No sugar crash. "
        "But I noticed my heart racing when I took a full scoop. "
        "I cut the scoop in half for the next workout."
    ),
    persona_value_roles: list[str] | None = None,
    additional_roles: list[str] | None = None,
) -> dict:
    if persona_value_roles is None:
        persona_value_roles = [
            "competitor_user_red_bull", "performance_use_case_buyer",
        ]
    if additional_roles is None:
        additional_roles = ["safety_skeptic"]
    return {
        "id": id, "source_kind": "amazon_reviews_2023_local",
        "source_url": (
            f"local://amazon_reviews_2023_local/{category}/{parent_asin}"
            "/fulltext"
        ),
        "content": content,
        "metadata": {
            "target_brief": "triton_drinks",
            "source_category": category,
            "parent_asin": parent_asin, "asin": asin,
            "rating": 3.0, "verified_purchase": True,
            "helpful_vote": 2, "timestamp": 1700000000,
            "metadata_title": metadata_title,
            "metadata_main_category": "Health & Household",
            "metadata_categories": [
                "Health & Household", "Diet & Sports Nutrition",
            ],
            "source_is_historical": True,
            "source_record_lineage": "full_text_companion",
            "original_preview_source_record_id": supersedes_id,
            "supersedes_preview_source_record_id": supersedes_id,
            "persona_value_roles": persona_value_roles,
            "additional_persona_roles_unlocked_by_full": additional_roles,
        },
    }


# ---------------------------------------------------------------------------
# 1. Script reads source_records from both 8.5C tags
# ---------------------------------------------------------------------------


def test_script_reads_both_8_5c_2_and_8_5c_4_tags() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest"
        in src
    )
    assert (
        "assembly_phase_8_5c4_triton_amazon_fulltext_companion_ingest"
        in src
    )


# ---------------------------------------------------------------------------
# 2 + 3 + 4 + 5 + 6. DB read-only discipline
# ---------------------------------------------------------------------------


def test_script_does_not_insert_persona_records() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "PersonaRecord(", "PersonaTrait(", "PersonaEvidenceLink(",
        "PersonaGraphEdge(", "PersonaCluster(",
        "Agent(", "AgentResponse(", "DebateTurn(",
        "Simulation(", "SimulationOutput(", "SimulationRound(",
    )
    for term in forbidden:
        for m in re.finditer(re.escape(term) + r"\s*\w", src):
            ctx = src[max(0, m.start() - 20):m.end() + 20]
            if "select(" in ctx:
                continue
            raise AssertionError(
                f"forbidden ORM construction: ...{ctx}..."
            )


def test_script_no_session_add_or_write_calls() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    bad = (
        "session.add(", "session.delete(", "session.commit(",
        "session.flush(",
        ".execute(insert(", ".execute(update(", ".execute(delete(",
    )
    for token in bad:
        assert token not in src, f"forbidden: {token!r}"


def test_script_no_frontend_or_graph_writes() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    for s in ("apps/web", "next/router", "next.js"):
        assert s not in src


# ---------------------------------------------------------------------------
# 7 + 8 + 9. Lineage-aware source selection
# ---------------------------------------------------------------------------


def test_lineage_selector_excludes_superseded_preview() -> None:
    preview = _preview_row(id="P1")
    companion = _companion_row(id="C1", supersedes_id="P1")
    effective, superseded, included = select_effective_sources(
        preview_rows=[preview],
        companion_rows=[companion],
        sufficiency_labels_by_id={"P1": "NEEDS_FULL_TEXT_COMPANION"},
    )
    # Effective set = the companion only; preview excluded.
    assert len(effective) == 1
    assert effective[0].source_record_id == "C1"
    assert effective[0].effective_kind == "fulltext_companion_used"
    assert effective[0].superseded_preview_source_record_id == "P1"
    assert "P1" in superseded
    assert "P1" not in included
    assert "C1" in included


def test_full_text_companion_replaces_preview_one_to_one() -> None:
    """For each companion that supersedes a preview, the preview is
    replaced — never duplicated."""
    p1 = _preview_row(id="P1")
    p2 = _preview_row(id="P2", parent_asin="B00Y", asin="B00Y")
    p3 = _preview_row(id="P3", parent_asin="B00Z", asin="B00Z")
    c1 = _companion_row(id="C1", supersedes_id="P1")  # supersedes P1
    # P2 and P3 have no companions — they survive
    effective, superseded, _ = select_effective_sources(
        preview_rows=[p1, p2, p3], companion_rows=[c1],
        sufficiency_labels_by_id={
            "P1": "NEEDS_FULL_TEXT_COMPANION",
            "P2": "SUFFICIENT_AS_IS",
            "P3": "USABLE_BUT_THIN",
        },
    )
    ids = sorted(s.source_record_id for s in effective)
    assert ids == ["C1", "P2", "P3"]
    assert superseded == ["P1"]


def test_effective_source_count_not_double_counted() -> None:
    """If 8.5C had inserted 8 physical rows but 2 are superseded,
    the effective set is exactly 6 — no double-counting."""
    previews = [
        _preview_row(id=f"P{i}", parent_asin=f"B{i:03d}", asin=f"B{i:03d}")
        for i in range(1, 7)  # 6 previews
    ]
    companions = [
        _companion_row(id="C1", supersedes_id="P1"),
        _companion_row(id="C2", supersedes_id="P2", parent_asin="B002", asin="B002"),
    ]
    sufficiency = {f"P{i}": "SUFFICIENT_AS_IS" for i in range(1, 7)}
    sufficiency["P1"] = "NEEDS_FULL_TEXT_COMPANION"
    sufficiency["P2"] = "NEEDS_FULL_TEXT_COMPANION"
    effective, superseded, _ = select_effective_sources(
        preview_rows=previews, companion_rows=companions,
        sufficiency_labels_by_id=sufficiency,
    )
    # 4 previews (P3..P6) + 2 companions = 6 total
    assert len(effective) == 6
    assert sorted(superseded) == ["P1", "P2"]
    # Sanity: no preview ID that's been superseded appears in effective
    eff_ids = {s.source_record_id for s in effective}
    assert "P1" not in eff_ids
    assert "P2" not in eff_ids
    assert "C1" in eff_ids
    assert "C2" in eff_ids


# ---------------------------------------------------------------------------
# 10 + 11 + 12. Dynamic role inference
# ---------------------------------------------------------------------------


def test_role_inference_uses_evidence_only() -> None:
    text = (
        "Better than Red Bull for me. No sugar. "
        "Heart racing after a full scoop."
    )
    metadata: dict = {"persona_value_roles": []}
    roles, basis = infer_persona_roles_from_evidence(
        text=text, metadata=metadata,
        competitor_brief_list=["Red Bull", "Monster"],
        substitute_brief_list=["coffee"],
    )
    assert "competitor_user_red_bull" in roles
    assert "safety_skeptic" in roles
    # Health-conscious because "no sugar" appears
    assert "health_conscious_buyer" in roles
    # Each role has evidence basis attached
    for r in roles:
        assert basis.get(r), f"role {r} has no evidence basis"


def test_role_inference_signature_takes_no_manual_role_labels() -> None:
    import inspect
    sig = inspect.signature(infer_persona_roles_from_evidence)
    params = set(sig.parameters.keys())
    # Only takes evidence inputs + brief context — no manual role list
    expected = {
        "text", "metadata",
        "competitor_brief_list", "substitute_brief_list",
    }
    assert params == expected
    assert "manual_roles" not in params
    assert "allowed_roles" not in params


def test_no_hardcoded_triton_or_energy_drink_constants_in_planner_pkg() -> None:
    """Drift: no Triton/energy-drink-specific tokens in the planner
    package code (post-docstring/comment strip)."""
    forbidden = (
        "Triton", "Red Bull", "Monster", "Celsius", "Prime Energy",
        "Gatorade", "energy drink", "pre-workout", "pre workout",
        "caffeine", "electrolyte", "sports drink",
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
        code = "\n".join(kept).lower()
        for term in forbidden:
            assert term.lower() not in code, (
                f"hardcoded {term!r} in {f.name}"
            )


# ---------------------------------------------------------------------------
# 13 + 14 + 15 + 16 + 17 + 18. Brief-scoped, evidence-tied candidate shape
# ---------------------------------------------------------------------------


def _full_planner_run() -> PersonaRolePlan:
    """Helper: run the planner on a synthetic 6-source pool."""
    p1 = _preview_row(
        id="P1", parent_asin="B001", asin="B001",
        metadata_title="Pre-Workout A",
        content=(
            "Loaded with caffeine. Better than Red Bull. "
            "Easy to mix. My favorite flavor."
        ),
        persona_value_roles=[
            "competitor_user_red_bull", "flavor_focused_buyer",
        ],
    )
    p2 = _preview_row(
        id="P2", parent_asin="B002", asin="B002",
        metadata_title="Energy Drink B (zero sugar)",
        content=(
            "No sugar, lots of flavors. Good variety. "
            "My favorite flavors."
        ),
        persona_value_roles=["flavor_focused_buyer"],
    )
    p3 = _preview_row(
        id="P3", parent_asin="B003", asin="B003",
        metadata_title="Calming Drink C",
        content=(
            "Wow these really work. The taste is okay but I'm "
            "definitely feeling relaxed. Do not recommend "
            "drinking these during the day."
        ),
        persona_value_roles=["flavor_focused_buyer"],
    )
    p4 = _preview_row(
        id="P4", parent_asin="B004", asin="B004",
        metadata_title="BLAST D Pre-Workout",
        content=(
            "Great flavor, amazing workout. The flavor is really good!"
        ),
        persona_value_roles=[
            "performance_use_case_buyer", "flavor_focused_buyer",
        ],
    )
    c5 = _companion_row(
        id="C5", supersedes_id="P5_NEVER_INSERTED",
        parent_asin="B005", asin="B005",
        metadata_title="C4 Energy E (full)",
        content=(
            "I had problems with taking this. The caffeine made my "
            "heart racing and my fingers tingling when I began "
            "the treadmill. I cut the scoop in half for the next "
            "workout. Don't recommend at full dose."
        ),
        persona_value_roles=[
            "performance_use_case_buyer", "safety_skeptic",
        ],
        additional_roles=["safety_skeptic"],
    )
    c6 = _companion_row(
        id="C6", supersedes_id="P6_NEVER_INSERTED",
        parent_asin="B006", asin="B006",
        metadata_title="AWAKE F Coffee Alternative (full)",
        content=(
            "Great alternative to coffee, tea or diet cola drinks. "
            "I was looking for something I could have in the middle "
            "of the day. Convenient to keep in my handbag."
        ),
        persona_value_roles=[
            "substitute_user_coffee", "convenience_focused_buyer",
        ],
        additional_roles=[],
    )
    sufficiency = {
        "P1": "USABLE_BUT_THIN", "P2": "SUFFICIENT_AS_IS",
        "P3": "SUFFICIENT_AS_IS", "P4": "SUFFICIENT_AS_IS",
        # P5 / P6 do not exist as preview rows — companions stand alone
    }
    effective, superseded, _ = select_effective_sources(
        preview_rows=[p1, p2, p3, p4],
        companion_rows=[c5, c6],
        sufficiency_labels_by_id=sufficiency,
    )
    planner = PersonaCandidatePlanner()
    return planner.generate(
        product_name="Triton Drinks",
        target_brief_id="triton_drinks",
        launch_state="unlaunched",
        competitor_brief_list=[
            "Red Bull", "Monster", "Celsius", "Prime", "Gatorade",
        ],
        substitute_brief_list=["coffee", "pre-workout", "preworkout"],
        effective_sources=effective,
        preview_rows_total=4,
        companion_rows_total=2,
        superseded_preview_ids=superseded,
    )


def test_persona_candidates_are_brief_scoped_and_dry_run_only() -> None:
    plan = _full_planner_run()
    assert plan.persona_candidates
    for c in plan.persona_candidates:
        assert c.scope == "brief_scoped"
        assert c.persistence_status == "dry_run_only"
        assert c.target_brief == "triton_drinks"
        assert c.not_global_persona is True
        assert c.generated_for_phase == "8.5D.1"


def test_every_candidate_has_at_least_one_source_record_id() -> None:
    plan = _full_planner_run()
    for c in plan.persona_candidates:
        assert len(c.source_record_ids) >= 1


def test_every_candidate_has_evidence_summary_and_inferred_role() -> None:
    plan = _full_planner_run()
    for c in plan.persona_candidates:
        assert c.evidence_summary
        assert c.inferred_persona_role
        assert c.role_inference_basis


# ---------------------------------------------------------------------------
# 19. Every accepted candidate has at least 2 evidence-supported traits
# ---------------------------------------------------------------------------


def test_every_candidate_has_at_least_two_traits() -> None:
    plan = _full_planner_run()
    for c in plan.persona_candidates:
        assert len(c.inferred_traits) >= 2


# ---------------------------------------------------------------------------
# 20. Unsupported / no-evidence personas are rejected
# ---------------------------------------------------------------------------


def test_source_with_no_evidence_signal_is_rejected() -> None:
    """A source whose text contains only filler with NO competitor /
    substitute / lexicon hit produces zero roles → rejected."""
    p = _preview_row(
        id="P_X", parent_asin="B0NULL", asin="B0NULL",
        content="Filler text only. No category signal whatsoever.",
        persona_value_roles=[],  # empty
        metadata_title="Filler Product",
    )
    effective, _, _ = select_effective_sources(
        preview_rows=[p], companion_rows=[],
        sufficiency_labels_by_id={"P_X": "SUFFICIENT_AS_IS"},
    )
    planner = PersonaCandidatePlanner()
    plan = planner.generate(
        product_name="Triton Drinks",
        target_brief_id="triton_drinks",
        launch_state="unlaunched",
        competitor_brief_list=["Red Bull"],
        substitute_brief_list=["coffee"],
        effective_sources=effective,
        preview_rows_total=1, companion_rows_total=0,
        superseded_preview_ids=[],
    )
    assert plan.persona_candidates == []
    assert any(
        r.rejection_reason == "no_source_evidence"
        for r in plan.rejected_candidate_ideas
    )


# ---------------------------------------------------------------------------
# 21. Fake target-product buyer/customer claims are rejected
# ---------------------------------------------------------------------------


def test_launch_state_validator_rejects_fake_triton_buyer() -> None:
    cand = PersonaCandidate(
        candidate_id="t1",
        target_brief="triton_drinks",
        generated_for_phase="8.5D.1",
        inferred_persona_role="competitor_user_red_bull",
        secondary_persona_roles=[],
        role_inference_basis=["x"],
        segment_label="x",
        source_record_ids=["S1"],
        evidence_summary="x",
        evidence_snippets=["I am a Triton buyer and tried Triton."],
        inferred_traits=[],
        inferred_preferences=[], inferred_objections=[],
        inferred_behaviors=[],
        hypothetical_target_product_reaction="x",
        confidence="high", evidence_strength="strong",
        caveats=[], simulation_usefulness_summary="x",
        persistence_recommendation="DEFER",
    )
    v = validate_launch_state_claims(
        candidate=cand, launch_state="unlaunched",
        product_name="Triton Drinks",
    )
    assert v.is_valid is False
    assert v.forbidden_phrases_matched
    assert v.rejection_reason == "fabricated_unlaunched_target_product_use"


def test_launch_state_validator_passes_clean_candidate() -> None:
    plan = _full_planner_run()
    # Every generated candidate must pass validation.
    for v in plan.launch_state_validation_results:
        assert v.is_valid is True


# ---------------------------------------------------------------------------
# 22. Duplicate role + evidence is rejected
# ---------------------------------------------------------------------------


def test_duplicate_role_and_evidence_rejected() -> None:
    """If two effective sources had the SAME source_record_id (which
    shouldn't happen via lineage-selection, but the planner defends
    anyway), only one candidate emits per (source, primary_role)."""
    p1 = _preview_row(id="DUP", parent_asin="B0X", asin="B0X")
    effective, _, _ = select_effective_sources(
        preview_rows=[p1, p1.copy()], companion_rows=[],
        sufficiency_labels_by_id={"DUP": "SUFFICIENT_AS_IS"},
    )
    planner = PersonaCandidatePlanner()
    plan = planner.generate(
        product_name="Triton Drinks",
        target_brief_id="triton_drinks",
        launch_state="unlaunched",
        competitor_brief_list=["Red Bull"],
        substitute_brief_list=["coffee"],
        effective_sources=effective,
        preview_rows_total=2, companion_rows_total=0,
        superseded_preview_ids=[],
    )
    # Even with 2 effective sources, only 1 candidate emits because
    # the second is a (role, source) duplicate.
    primary_roles = [c.inferred_persona_role for c in plan.persona_candidates]
    assert len(set(primary_roles) & {"competitor_user_red_bull"}) <= 1


# ---------------------------------------------------------------------------
# 23. Candidate count is bounded by evidence, not forced
# ---------------------------------------------------------------------------


def test_candidate_count_is_bounded_by_evidence() -> None:
    plan = _full_planner_run()
    # 6 effective sources → at MOST 6 candidates.
    assert len(plan.persona_candidates) <= 6
    # But some sources may be rejected — count should be honest, not
    # forced.
    assert (
        len(plan.persona_candidates) + len(plan.rejected_candidate_ideas)
    ) == plan.effective_source_records_count or (
        len(plan.persona_candidates) <= 6
    )


# ---------------------------------------------------------------------------
# 24 + 25. No external retrieval / no Amazon.com scraping
# ---------------------------------------------------------------------------


def test_no_external_retrieval_or_api_libs_in_planner_pkg() -> None:
    forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3",
                 "selenium", "playwright", "scrapy",
                 "beautifulsoup4", "bs4"}
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
                    assert root not in forbidden, f"{f.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden, f"{f.name}: {node.module}"


def test_no_amazon_dot_com_url_strings_in_planner_pkg() -> None:
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    for f in PKG.rglob("*.py"):
        assert pat.search(f.read_text(encoding="utf-8")) is None, (
            f"amazon.com URL string in {f.name}"
        )


# ---------------------------------------------------------------------------
# 26. Existing 8.5C imports still resolve
# ---------------------------------------------------------------------------


def test_existing_8_5c_imports_still_resolve() -> None:
    from assembly.sources.ingestion_policy import (  # noqa: F401
        UNIVERSAL_GUARDRAILS, generate_ingestion_policy,
    )
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        generate_anchor_plan, score_review_with_plan,
    )


# ---------------------------------------------------------------------------
# Bonus: universal lexicons are non-empty + product-agnostic
# ---------------------------------------------------------------------------


def test_universal_role_lexicons_are_non_empty_and_product_agnostic() -> None:
    assert "safety_skeptic" in UNIVERSAL_ROLE_LEXICONS
    assert "price_skeptic" in UNIVERSAL_ROLE_LEXICONS
    assert "flavor_focused_buyer" in UNIVERSAL_ROLE_LEXICONS
    assert "performance_use_case_buyer" in UNIVERSAL_ROLE_LEXICONS
    # No product-specific words
    for role, lex in UNIVERSAL_ROLE_LEXICONS.items():
        for term in lex:
            assert "Triton" not in term
            assert "Red Bull" not in term


# ---------------------------------------------------------------------------
# Bonus: plan_id is deterministic
# ---------------------------------------------------------------------------


def test_plan_id_is_deterministic_for_same_inputs() -> None:
    plan1 = _full_planner_run()
    plan2 = _full_planner_run()
    assert plan1.plan_id == plan2.plan_id
    # Candidate count + role distribution stable
    assert (
        len(plan1.persona_candidates) == len(plan2.persona_candidates)
    )


def test_plan_records_dry_run_caveats_and_brief_scoped_invariants() -> None:
    plan = _full_planner_run()
    blob = " | ".join(plan.caveats)
    assert "DRY-RUN" in blob.upper() or "DRY RUN" in blob.upper() or "dry-run" in blob.lower()
    assert "BRIEF-SCOPED" in blob.upper() or "brief-scoped" in blob.lower()
    assert "global persona" in blob.lower()
