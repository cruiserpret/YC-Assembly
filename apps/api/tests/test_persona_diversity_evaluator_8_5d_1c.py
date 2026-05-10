"""Phase 8.5D.1C — persona-diversity evaluator + diversity-aware
ingestion-policy reranker tests.

25 deterministic operator tests covering:
  * PersonaDiversityEvaluator universal correctness (12)
  * Diversity-aware reranker quality discipline (7)
  * Script wiring + raised caps + DB-read-only invariants (6)

NO live DB writes. NO LLM. NO network. Pure unit tests over the
pure-function planners + a static read of the dry-run script.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning,
)
from assembly.sources.ingestion_policy import (
    apply_diversity_aware_reranking,
)
from assembly.sources.ingestion_policy.schemas import (
    CandidateDecision, PlannedSourceRecordPreview,
)
from assembly.sources.persona_diversity_evaluator import (
    DiversityRecommendation, PersonaDiversityEvaluation,
    evaluate_persona_diversity,
)
from assembly.sources.persona_role_planner.schemas import (
    InferredPersonaTrait, PersonaCandidate,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "fresh_product_persona_generalization_dry_run_8_5d_1c.py"
)
EVAL_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "persona_diversity_evaluator"
)
INGESTION_PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "ingestion_policy"
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _strideshield_brief() -> ProductBriefForPlanning:
    return ProductBriefForPlanning(
        product_name="StrideShield",
        product_description=(
            "A pocket-sized anti-blister and anti-chafe balm for runners, "
            "hikers, and gym-goers."
        ),
        price_or_price_structure="$12.99",
        launch_geography="California, United States",
        target_customers=[
            "runners", "hikers", "gym-goers",
        ],
        competitors=[
            "Body Glide", "Gold Bond Friction Defense",
            "Megababe Thigh Rescue", "Squirrel's Nut Butter",
            "Trail Toes",
        ],
    )


def _make_candidate(
    *,
    candidate_id: str,
    primary_role: str,
    secondary_roles: list[str] | None = None,
    source_record_id: str = "S1",
) -> PersonaCandidate:
    return PersonaCandidate(
        candidate_id=candidate_id,
        target_brief="strideshield",
        generated_for_phase="8.5D.1C",
        inferred_persona_role=primary_role,
        secondary_persona_roles=secondary_roles or [],
        role_inference_basis=["evidence-tied basis"],
        segment_label=primary_role.replace("_", " "),
        source_record_ids=[source_record_id],
        evidence_summary="brief evidence summary",
        evidence_snippets=["I tried Body Glide on my heels."],
        inferred_traits=[
            InferredPersonaTrait(
                trait_name="trait_a", trait_value="value_a",
                evidence_source_record_id=source_record_id,
                evidence_excerpt="excerpt", confidence="high",
            ),
            InferredPersonaTrait(
                trait_name="trait_b", trait_value="value_b",
                evidence_source_record_id=source_record_id,
                evidence_excerpt="excerpt", confidence="medium",
            ),
        ],
        inferred_preferences=["pref a"],
        inferred_objections=["obj a"],
        inferred_behaviors=["beh a"],
        hypothetical_target_product_reaction="hypothetical reaction",
        confidence="high", evidence_strength="strong",
        caveats=[], simulation_usefulness_summary="useful",
        persistence_recommendation="DEFER",
    )


def _make_decision(
    *,
    candidate_id: str,
    decision: str = "SELECTED",
    selection_rank: int | None = 1,
    matched_terms: list[str] | None = None,
    rejection_reasons: list[str] | None = None,
) -> CandidateDecision:
    if matched_terms is None:
        matched_terms = ["positive:balm", "competitor:Body Glide"]
    if rejection_reasons is None:
        rejection_reasons = []
    preview = PlannedSourceRecordPreview(
        source_kind="amazon_reviews_2023_local",
        source_url=(
            f"local://amazon_reviews_2023/Beauty_and_Personal_Care/"
            f"{candidate_id}"
        ),
        content_preview="content preview",
        content_length=100,
        content_hash="0" * 64,
        language="en",
        metadata={
            "matched_terms": matched_terms,
            "persona_value_roles": [],
        },
        ingested_by="dry_run",
        compliance_tag="open_dataset",
        captured_at="2026-05-04T00:00:00+00:00",
        pii_redaction_status="passed",
        sensitive_scan_status="passed",
        user_handle_hash=None,
    )
    return CandidateDecision(
        candidate_id=candidate_id,
        decision=decision,  # type: ignore[arg-type]
        selection_rank=selection_rank if decision == "SELECTED" else None,
        evidence_strength_label="strong",
        source_relevance_label="primary",
        persona_value_label="high",
        selected_for_persona_roles=[],
        decision_reasons=["selected"] if decision == "SELECTED" else [],
        rejection_reasons=rejection_reasons,
        scanner_results={},
        duplicate_check="unique",
        planned_source_record_preview=preview if decision == "SELECTED" or rejection_reasons else None,
    )


# ---------------------------------------------------------------------------
# 1. PersonaDiversityEvaluator exists + correct schema shape
# ---------------------------------------------------------------------------


def test_persona_diversity_evaluator_exists_and_callable() -> None:
    import inspect
    sig = inspect.signature(evaluate_persona_diversity)
    params = set(sig.parameters.keys())
    assert "brief" in params
    assert "candidates" in params
    assert "plan" in params
    # Result schema is closed
    assert PersonaDiversityEvaluation.model_config.get("extra") == "forbid"


def test_diversity_recommendation_is_closed_set() -> None:
    """`DiversityRecommendation` Literal must be exactly the four
    operator-known states. Adding a new state is a schema-breaking
    change."""
    from typing import get_args
    expected = {
        "READY", "DEFER_DIVERSIFY",
        "DEFER_SOURCE_COVERAGE", "DEFER_NO_CANDIDATES",
    }
    actual = set(get_args(DiversityRecommendation))
    assert actual == expected


# ---------------------------------------------------------------------------
# 2 + 3. Detect all-same-role collapse → not ready
# ---------------------------------------------------------------------------


def test_evaluator_detects_all_candidates_same_primary_role_collapse() -> None:
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id=f"c{i}",
            primary_role="competitor_user_body_glide",
        )
        for i in range(4)
    ]
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert ev.unique_primary_roles == ["competitor_user_body_glide"]
    assert ev.duplicate_role_cluster_count == 1
    assert ev.narrow_source_proof_only is True


def test_same_role_cluster_marked_not_ready_for_mutation() -> None:
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id=f"c{i}",
            primary_role="competitor_user_body_glide",
        )
        for i in range(4)
    ]
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert ev.mutating_persistence_recommendation == "DEFER_SOURCE_COVERAGE"
    # Specifically, NOT READY
    assert ev.mutating_persistence_recommendation != "READY"


# ---------------------------------------------------------------------------
# 4. Passes when 2+ distinct evidence-supported roles exist
# ---------------------------------------------------------------------------


def test_evaluator_passes_when_two_or_more_distinct_roles() -> None:
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id="c1",
            primary_role="competitor_user_body_glide",
        ),
        _make_candidate(
            candidate_id="c2",
            primary_role="competitor_user_megababe_thigh_rescue",
        ),
        _make_candidate(
            candidate_id="c3",
            primary_role="performance_use_case_buyer",
        ),
    ]
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert len(ev.unique_primary_roles) == 3
    assert ev.diversity_score >= 0.5
    assert ev.mutating_persistence_recommendation == "READY"
    assert ev.narrow_source_proof_only is False


# ---------------------------------------------------------------------------
# 5. Diversity-aware evidence selection prefers distinct themes
# ---------------------------------------------------------------------------


def test_diversity_reranker_prefers_distinct_evidence_themes() -> None:
    """Three SELECTED Body-Glide candidates + one cap-rejected
    Megababe candidate. The reranker should swap one Body-Glide out
    for Megababe to introduce a fresh role."""
    selected = [
        _make_decision(
            candidate_id=f"sel_bg_{i}", selection_rank=i + 1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        )
        for i in range(3)
    ]
    cap_rej = _make_decision(
        candidate_id="cap_megababe",
        decision="REJECTED",
        rejection_reasons=["max_insert_cap=3 reached"],
        matched_terms=[
            "positive:balm", "competitor:Megababe Thigh Rescue",
        ],
    )
    new_decisions, swap_log = apply_diversity_aware_reranking(
        selected + [cap_rej],
        target_min_unique_roles=2,
    )
    new_selected = [d for d in new_decisions if d.decision == "SELECTED"]
    new_selected_ids = {d.candidate_id for d in new_selected}
    # The swap must have happened: cap_megababe is now SELECTED
    assert "cap_megababe" in new_selected_ids
    # Exactly one swap
    assert len(swap_log) == 1
    # Roles diversified
    roles = {
        d.planned_source_record_preview.metadata["matched_terms"][1]
        for d in new_selected
        if d.planned_source_record_preview
    }
    assert any("Megababe" in r for r in roles)


# ---------------------------------------------------------------------------
# 6. Diversity reranker NEVER promotes weak / quality-failed candidates
# ---------------------------------------------------------------------------


def test_diversity_reranker_does_not_accept_pii_failed_candidates() -> None:
    """A REJECTED candidate whose rejection includes a PII reason is
    NEVER promoted, even if it has a fresh role."""
    selected = [
        _make_decision(
            candidate_id=f"sel_bg_{i}", selection_rank=i + 1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        )
        for i in range(3)
    ]
    pii_rej = _make_decision(
        candidate_id="pii_megababe",
        decision="REJECTED",
        rejection_reasons=[
            "reject_pii_hit: phone-number-found",
            "max_insert_cap=3 reached",
        ],
        matched_terms=[
            "positive:balm", "competitor:Megababe Thigh Rescue",
        ],
    )
    new_decisions, swap_log = apply_diversity_aware_reranking(
        selected + [pii_rej], target_min_unique_roles=4,
    )
    new_selected_ids = {
        d.candidate_id for d in new_decisions if d.decision == "SELECTED"
    }
    # PII-failed candidate never promoted
    assert "pii_megababe" not in new_selected_ids
    assert swap_log == []


def test_diversity_reranker_does_not_accept_fake_buyer_or_scanner_failed() -> None:
    selected = [
        _make_decision(
            candidate_id=f"sel_bg_{i}", selection_rank=i + 1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        )
        for i in range(3)
    ]
    fake_buyer_rej = _make_decision(
        candidate_id="fb_megababe",
        decision="REJECTED",
        rejection_reasons=[
            "reject_fake_buyer_for_unlaunched: claims to have used product",
        ],
        matched_terms=[
            "positive:balm", "competitor:Megababe Thigh Rescue",
        ],
    )
    no_anchor_rej = _make_decision(
        candidate_id="na_trail_toes",
        decision="REJECTED",
        rejection_reasons=["reject_no_strong_anchor"],
        matched_terms=[
            "positive:balm", "competitor:Trail Toes",
        ],
    )
    dup_rej = _make_decision(
        candidate_id="dup_squirrel",
        decision="REJECTED",
        rejection_reasons=[
            "reject_duplicate_content_hash",
            "max_insert_cap=3 reached",
        ],
        matched_terms=[
            "positive:balm", "competitor:Squirrel's Nut Butter",
        ],
    )
    new_decisions, swap_log = apply_diversity_aware_reranking(
        selected + [fake_buyer_rej, no_anchor_rej, dup_rej],
        target_min_unique_roles=4,
    )
    promoted_ids = {
        d.candidate_id for d in new_decisions if d.decision == "SELECTED"
    }
    assert "fb_megababe" not in promoted_ids
    assert "na_trail_toes" not in promoted_ids
    assert "dup_squirrel" not in promoted_ids
    assert swap_log == []


def test_diversity_reranker_only_promotes_cap_rejected() -> None:
    """A candidate REJECTED for `per_category_diversity_cap` (a cap
    reason) IS eligible for promotion. A candidate REJECTED for any
    other reason is NOT eligible."""
    selected = [
        _make_decision(
            candidate_id=f"sel_bg_{i}", selection_rank=i + 1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        )
        for i in range(3)
    ]
    diversity_cap_rej = _make_decision(
        candidate_id="div_cap_megababe",
        decision="REJECTED",
        rejection_reasons=[
            "per_category_diversity_cap=2 reached for "
            "category=Beauty_and_Personal_Care",
        ],
        matched_terms=[
            "positive:balm", "competitor:Megababe Thigh Rescue",
        ],
    )
    new_decisions, swap_log = apply_diversity_aware_reranking(
        selected + [diversity_cap_rej], target_min_unique_roles=2,
    )
    promoted_ids = {
        d.candidate_id for d in new_decisions if d.decision == "SELECTED"
    }
    # per_category_diversity_cap IS cap-only → promoted
    assert "div_cap_megababe" in promoted_ids
    assert len(swap_log) == 1


# ---------------------------------------------------------------------------
# 7. Reranker is a no-op when SELECTED set is already diverse
# ---------------------------------------------------------------------------


def test_diversity_reranker_no_op_when_already_diverse() -> None:
    selected = [
        _make_decision(
            candidate_id="sel_bg", selection_rank=1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        ),
        _make_decision(
            candidate_id="sel_megababe", selection_rank=2,
            matched_terms=[
                "positive:balm",
                "competitor:Megababe Thigh Rescue",
            ],
        ),
        _make_decision(
            candidate_id="sel_trail_toes", selection_rank=3,
            matched_terms=["positive:balm", "competitor:Trail Toes"],
        ),
    ]
    cap_rej = _make_decision(
        candidate_id="cap_squirrel",
        decision="REJECTED",
        rejection_reasons=["max_insert_cap=3 reached"],
        matched_terms=[
            "positive:balm", "competitor:Squirrel's Nut Butter",
        ],
    )
    new_decisions, swap_log = apply_diversity_aware_reranking(
        selected + [cap_rej], target_min_unique_roles=3,
    )
    # Already 3 distinct roles → target met → no swap needed
    assert swap_log == []
    new_selected_ids = {
        d.candidate_id for d in new_decisions if d.decision == "SELECTED"
    }
    assert new_selected_ids == {"sel_bg", "sel_megababe", "sel_trail_toes"}


# ---------------------------------------------------------------------------
# 8 + 9. Empty / single-candidate edge cases
# ---------------------------------------------------------------------------


def test_evaluator_zero_candidates_returns_defer_no_candidates() -> None:
    brief = _strideshield_brief()
    ev = evaluate_persona_diversity(brief=brief, candidates=[])
    assert ev.primary_role_count == 0
    assert ev.unique_primary_roles == []
    assert ev.diversity_score == 0.0
    assert ev.mutating_persistence_recommendation == "DEFER_NO_CANDIDATES"
    assert ev.persona_similarity_warnings


def test_evaluator_single_candidate_defers_source_coverage() -> None:
    brief = _strideshield_brief()
    cands = [_make_candidate(
        candidate_id="c1",
        primary_role="competitor_user_body_glide",
    )]
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert ev.unique_primary_roles == ["competitor_user_body_glide"]
    assert ev.narrow_source_proof_only is True
    assert ev.mutating_persistence_recommendation == "DEFER_SOURCE_COVERAGE"


# ---------------------------------------------------------------------------
# 10. Competitor concentration detection
# ---------------------------------------------------------------------------


def test_evaluator_detects_high_competitor_concentration() -> None:
    """5 candidates: 4× competitor_user_body_glide,
    1× performance_use_case_buyer (no competitor). Concentration
    is 4/5 = 0.8 → above warning threshold."""
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id=f"c{i}",
            primary_role="competitor_user_body_glide",
        )
        for i in range(4)
    ]
    cands.append(_make_candidate(
        candidate_id="c5",
        primary_role="performance_use_case_buyer",
    ))
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert ev.competitor_concentration >= 0.6
    # Warning should call out the dominant competitor
    blob = " | ".join(ev.persona_similarity_warnings)
    assert "competitor" in blob.lower() or "body_glide" in blob.lower()


def test_evaluator_hard_competitor_concentration_defers() -> None:
    """When a single competitor dominates ≥85% of candidates →
    DEFER_DIVERSIFY regardless of role count."""
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id=f"c{i}",
            primary_role="competitor_user_body_glide",
            secondary_roles=[],
        )
        for i in range(9)
    ]
    cands.append(_make_candidate(
        candidate_id="c10",
        primary_role="performance_use_case_buyer",
    ))
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert ev.competitor_concentration >= 0.85
    assert ev.mutating_persistence_recommendation == "DEFER_DIVERSIFY"


# ---------------------------------------------------------------------------
# 11. Undercovered evidence themes reported
# ---------------------------------------------------------------------------


def test_evaluator_reports_undercovered_competitor_themes() -> None:
    """Brief has 5 competitors. Candidates only cover 1 of them.
    The other 4 should be flagged as undercovered themes."""
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id=f"c{i}",
            primary_role="competitor_user_body_glide",
        )
        for i in range(2)
    ]
    cands.append(_make_candidate(
        candidate_id="c3",
        primary_role="performance_use_case_buyer",
    ))
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    # Body Glide is covered; Megababe / Gold Bond / Squirrel's /
    # Trail Toes are not.
    blob = " | ".join(ev.undercovered_evidence_themes).lower()
    assert "megababe" in blob
    assert "trail toes" in blob
    # At least 4 themes flagged
    assert len(ev.undercovered_evidence_themes) >= 4


# ---------------------------------------------------------------------------
# 12. Determinism — same inputs → same evaluation
# ---------------------------------------------------------------------------


def test_evaluator_is_deterministic() -> None:
    brief = _strideshield_brief()
    cands = [
        _make_candidate(
            candidate_id="c1",
            primary_role="competitor_user_body_glide",
        ),
        _make_candidate(
            candidate_id="c2",
            primary_role="competitor_user_megababe_thigh_rescue",
        ),
        _make_candidate(
            candidate_id="c3",
            primary_role="performance_use_case_buyer",
        ),
    ]
    ev1 = evaluate_persona_diversity(brief=brief, candidates=cands)
    ev2 = evaluate_persona_diversity(brief=brief, candidates=cands)
    assert ev1.model_dump_json() == ev2.model_dump_json()


# ---------------------------------------------------------------------------
# 13. Diversity score composition
# ---------------------------------------------------------------------------


def test_diversity_score_composes_uniqueness_and_balance() -> None:
    """diversity_score = 0.6 * role_uniqueness + 0.4 * (1 - concentration).
    Verify the formula at a known input."""
    brief = _strideshield_brief()
    # 4 candidates, 4 distinct roles, no competitor overlap (all
    # different competitors → concentration = 1/4 = 0.25):
    cands = [
        _make_candidate(
            candidate_id="c1",
            primary_role="competitor_user_body_glide",
        ),
        _make_candidate(
            candidate_id="c2",
            primary_role="competitor_user_megababe_thigh_rescue",
        ),
        _make_candidate(
            candidate_id="c3",
            primary_role="competitor_user_trail_toes",
        ),
        _make_candidate(
            candidate_id="c4",
            primary_role="performance_use_case_buyer",
        ),
    ]
    ev = evaluate_persona_diversity(brief=brief, candidates=cands)
    # role_uniqueness = 4/4 = 1.0
    # concentration: 3 distinct competitors of 4 candidates →
    # max competitor cluster = 1, concentration = 1/4 = 0.25
    # diversity_score = 0.6 * 1.0 + 0.4 * 0.75 = 0.9
    assert ev.diversity_score == pytest.approx(0.9, abs=0.01)


# ---------------------------------------------------------------------------
# 14. Universal — no hardcoded brand / role / category names
# ---------------------------------------------------------------------------


def _post_docstring_code(pkg: Path) -> dict[str, str]:
    """Strip docstrings + comments, return {filename: lower-cased code}."""
    out: dict[str, str] = {}
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
        out[f.name] = "\n".join(kept).lower()
    return out


def test_evaluator_pkg_has_no_strideshield_or_triton_tokens() -> None:
    forbidden = (
        "strideshield", "triton", "solara", "amboras",
        "body glide", "megababe", "trail toes",
        "squirrel's nut butter", "gold bond", "red bull",
        "monster", "celsius", "gatorade",
        "anti-blister", "anti-chafe", "energy drink",
        "pre-workout", "caffeine",
    )
    code_by_file = _post_docstring_code(EVAL_PKG)
    for fname, code in code_by_file.items():
        for term in forbidden:
            assert term not in code, (
                f"hardcoded {term!r} in evaluator pkg {fname}"
            )


def test_diversity_reranker_pkg_has_no_brand_or_category_tokens() -> None:
    forbidden = (
        "strideshield", "triton", "solara",
        "body glide", "megababe", "trail toes",
        "squirrel's nut butter", "gold bond", "red bull",
        "monster", "celsius", "gatorade",
        "beauty_and_personal_care", "health_and_household",
        "grocery_and_gourmet_food",
        "anti-blister", "anti-chafe", "energy drink",
    )
    # Only the new diversity.py + the package as a whole.
    code_by_file = _post_docstring_code(INGESTION_PKG)
    for fname, code in code_by_file.items():
        for term in forbidden:
            assert term not in code, (
                f"hardcoded {term!r} in ingestion_policy pkg {fname}"
            )


# ---------------------------------------------------------------------------
# 15. Reranker preserves all input candidates (no drop, no dup)
# ---------------------------------------------------------------------------


def test_reranker_preserves_total_candidate_count() -> None:
    """Whatever swaps happen, the union of SELECTED + REJECTED in the
    output equals the input set (no candidates dropped, none duplicated)."""
    selected = [
        _make_decision(
            candidate_id=f"sel_bg_{i}", selection_rank=i + 1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        )
        for i in range(3)
    ]
    cap_rej = [
        _make_decision(
            candidate_id=f"cap_megababe_{i}",
            decision="REJECTED",
            rejection_reasons=["max_insert_cap=3 reached"],
            matched_terms=[
                "positive:balm", "competitor:Megababe Thigh Rescue",
            ],
        )
        for i in range(2)
    ]
    quality_rej = _make_decision(
        candidate_id="qrej_bad",
        decision="REJECTED",
        rejection_reasons=["reject_pii_hit: email"],
        matched_terms=["positive:balm", "competitor:Trail Toes"],
    )
    inputs = selected + cap_rej + [quality_rej]
    new_decisions, _ = apply_diversity_aware_reranking(
        inputs, target_min_unique_roles=2,
    )
    in_ids = {d.candidate_id for d in inputs}
    out_ids = {d.candidate_id for d in new_decisions}
    assert in_ids == out_ids
    assert len(new_decisions) == len(inputs)


def test_reranker_renumbers_selection_rank_uniquely() -> None:
    selected = [
        _make_decision(
            candidate_id=f"sel_bg_{i}", selection_rank=i + 1,
            matched_terms=["positive:balm", "competitor:Body Glide"],
        )
        for i in range(3)
    ]
    cap_rej = _make_decision(
        candidate_id="cap_megababe",
        decision="REJECTED",
        rejection_reasons=["max_insert_cap=3 reached"],
        matched_terms=[
            "positive:balm", "competitor:Megababe Thigh Rescue",
        ],
    )
    new_decisions, _ = apply_diversity_aware_reranking(
        [*selected, cap_rej], target_min_unique_roles=2,
    )
    sel_ranks = [
        d.selection_rank for d in new_decisions
        if d.decision == "SELECTED"
    ]
    assert all(r is not None for r in sel_ranks)
    assert len(sel_ranks) == len(set(sel_ranks))  # unique


# ---------------------------------------------------------------------------
# 16-21. Script-level invariants (raised caps, DB-read-only, no LLM)
# ---------------------------------------------------------------------------


def test_script_uses_raised_bounded_scan_caps() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "DEFAULT_RECORDS_PER_CATEGORY = 50_000" in src
    assert "DEFAULT_CATEGORY_SAMPLE = 25_000" in src
    assert "HARD_RECORDS_PER_CATEGORY = 100_000" in src
    assert "HARD_CATEGORY_SAMPLE = 100_000" in src


def test_script_invokes_diversity_aware_reranker() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "apply_diversity_aware_reranking" in src


def test_script_invokes_persona_diversity_evaluator() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "evaluate_persona_diversity" in src
    assert "persona_diversity_evaluation" in src


def test_script_reads_db_baseline_pre_and_post() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "db_pre = await _read_baseline_counts(sm)" in src
    assert "db_post = await _read_baseline_counts(sm)" in src
    assert "db_unchanged = db_pre == db_post" in src


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
            ctx = src[max(0, m.start() - 20):m.end() + 20]
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


def test_script_no_external_api_libs() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "httpx.", "requests.", "aiohttp.",
        "anthropic", "openai", "tavily", "firecrawl",
        "brave_search", "youtube_data",
    )
    for s in forbidden:
        assert s.lower() not in src.lower(), f"forbidden: {s}"


def test_script_no_amazon_dot_com_url_strings() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    pat = re.compile(r"['\"]https?://[^'\"]*amazon\.com", re.IGNORECASE)
    assert pat.search(src) is None


# ---------------------------------------------------------------------------
# 22. ready_for_mutating_phase requires READY + planner-ready + db-unchanged
# ---------------------------------------------------------------------------


def test_script_ready_for_mutating_requires_three_conditions() -> None:
    """The dry-run script must compute `ready_for_mutating` as the
    AND of (a) diversity evaluator READY, (b) persona planner
    structural readiness, (c) db_unchanged. Static check on script."""
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # Search for the assignment with the three conjuncts present.
    needle = "ready_for_mutating = ("
    assert needle in src
    snippet = src[src.index(needle):src.index(needle) + 400]
    assert "mutating_persistence_recommendation" in snippet
    assert '"READY"' in snippet
    assert "ready_for_8_5d_2" in snippet
    assert "db_unchanged" in snippet


# ---------------------------------------------------------------------------
# 23. Existing 8.5D.1B / 8.5C imports still resolve
# ---------------------------------------------------------------------------


def test_existing_imports_still_resolve() -> None:
    from assembly.sources.persona_role_planner import (  # noqa: F401
        EffectiveSourceRecord, PersonaCandidate, PersonaCandidatePlanner,
        select_effective_sources, validate_launch_state_claims,
    )
    from assembly.sources.ingestion_policy import (  # noqa: F401
        UNIVERSAL_GUARDRAILS, decide_candidates,
        generate_ingestion_policy,
    )
    from assembly.sources.evidence_anchor_planner import (  # noqa: F401
        ProductBriefForPlanning, generate_anchor_plan,
        generate_source_category_plan, score_review_with_plan,
    )


# ---------------------------------------------------------------------------
# 24. Evaluator + reranker package have no http / network deps
# ---------------------------------------------------------------------------


def test_no_http_libs_imported_in_diversity_pkgs() -> None:
    forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3",
                 "selenium", "playwright", "scrapy",
                 "beautifulsoup4", "bs4"}
    for pkg in (EVAL_PKG, INGESTION_PKG):
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
# 25. Bonus: PersonaDiversityEvaluation rejects extra fields (strict shape)
# ---------------------------------------------------------------------------


def test_persona_diversity_evaluation_rejects_extra_fields() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        PersonaDiversityEvaluation(
            diversity_score=0.5,
            primary_role_count=1,
            unique_primary_roles=["x"],
            unique_secondary_roles=[],
            evidence_source_count=1,
            competitor_concentration=0.0,
            duplicate_role_cluster_count=0,
            persona_similarity_warnings=[],
            undercovered_evidence_themes=[],
            mutating_persistence_recommendation="READY",
            narrow_source_proof_only=False,
            rationale=[],
            unexpected_extra_field="boom",  # type: ignore[call-arg]
        )
