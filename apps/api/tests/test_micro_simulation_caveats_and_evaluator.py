"""Phase 8.4B.1 — generic caveat builder + output quality evaluator tests.

Covers all 15 operator scenarios:
  1. No hardcoded "Amboras" appears in generic Triton caveats.
  2. No hardcoded "Triton" appears in generic caveat builder code.
  3. Caveat builder uses active product name dynamically.
  4. Market-entry unlaunched product gets unlaunched-product caveat.
  5. Classic launched product does not get unlaunched-product caveat.
  6. Adjacent-tier caveat appears only when adjacent_count > 0.
  7. Geography caveat appears when geography_strength is soft/thin.
  8. Output quality evaluator detects evidence-grounded objections.
  9. Output quality evaluator detects competitor/substitute comparisons.
 10. Output quality evaluator detects missing caveats.
 11. Output quality evaluator rejects fake forecast / buy percentage.
 12. Output quality evaluator returns expansion_readiness.
 13. Existing Phase 8.4B live JSON scores as useful but not full-society.
 14. Full unit tests pass (verified by harness regression).
 15. Full integration tests pass (verified by harness regression).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assembly.pipeline.micro_simulation.caveats import (
    build_micro_simulation_caveats,
    detect_geography_strength,
)
from assembly.pipeline.micro_simulation.quality_evaluator import (
    ExpansionReadiness,
    QualityDimensionStatus,
    evaluate_micro_simulation_quality,
    report_to_dict,
)


# ---------------------------------------------------------------------------
# 1. No hardcoded "Amboras" in generic Triton caveats
# ---------------------------------------------------------------------------


def test_triton_caveats_contain_no_amboras_label() -> None:
    caveats = build_micro_simulation_caveats(
        product_name="Triton Drinks",
        product_type="caffeinated sports/energy drink",
        geography="California, United States",
        total_categories=23,
        represented_categories=4,
        sample_size=7,
        core_count=5,
        adjacent_count=2,
        is_market_entry=True,
        is_unlaunched=True,
        geography_strength="soft",
    )
    blob = " | ".join(caveats)
    assert "Amboras" not in blob
    assert "amboras" not in blob.lower()


def test_amboras_caveats_contain_no_triton_label() -> None:
    caveats = build_micro_simulation_caveats(
        product_name="Amboras",
        product_type="Shopify tool",
        geography=None,
        total_categories=8,
        represented_categories=2,
        sample_size=2,
        core_count=2,
        adjacent_count=0,
        is_market_entry=False,
        is_unlaunched=False,
        geography_strength="absent",
    )
    blob = " | ".join(caveats)
    assert "Triton" not in blob


# ---------------------------------------------------------------------------
# 2. No hardcoded "Triton" in caveat builder MODULE code
# ---------------------------------------------------------------------------


def _strip_docstrings_and_comments(src: str) -> str:
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    docstring_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (
            ast.FunctionDef, ast.AsyncFunctionDef,
            ast.ClassDef, ast.Module,
        )):
            ds = ast.get_docstring(node, clean=False)
            if ds is None:
                continue
            if (
                node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                ds_node = node.body[0]
                docstring_ranges.append(
                    (ds_node.lineno, ds_node.end_lineno or ds_node.lineno)
                )
    lines = src.splitlines()
    in_docstring = [False] * (len(lines) + 1)
    for start, end in docstring_ranges:
        for i in range(start, end + 1):
            if 0 <= i - 1 < len(in_docstring):
                in_docstring[i - 1] = True
    kept: list[str] = []
    for i, line in enumerate(lines):
        if in_docstring[i]:
            continue
        comment_idx = line.find("#")
        if comment_idx >= 0:
            line = line[:comment_idx]
        kept.append(line)
    return "\n".join(kept)


def test_no_hardcoded_brand_names_in_caveat_builder_code() -> None:
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline" / "micro_simulation"
        / "caveats.py"
    ).read_text(encoding="utf-8")
    code_only = _strip_docstrings_and_comments(src)
    forbidden = ("Amboras", "Triton", "Red Bull", "Monster", "Celsius")
    for term in forbidden:
        assert term not in code_only, (
            f"caveats.py CODE must not hardcode {term!r}"
        )


def test_no_hardcoded_brand_names_in_evaluator_code() -> None:
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline" / "micro_simulation"
        / "quality_evaluator.py"
    ).read_text(encoding="utf-8")
    code_only = _strip_docstrings_and_comments(src)
    # evaluator IS allowed to reference Amboras + Triton in its
    # closed brand-leakage-detection list (those ARE the brands we
    # want to detect leakage of). All OTHER product names must NOT
    # appear in code.
    forbidden = ("Red Bull", "Monster", "Celsius", "ShopBot", "Solara")
    for term in forbidden:
        assert term not in code_only, (
            f"quality_evaluator.py CODE must not hardcode {term!r}"
        )


# ---------------------------------------------------------------------------
# 3. Caveat builder uses active product name dynamically
# ---------------------------------------------------------------------------


def test_caveat_builder_uses_active_product_name() -> None:
    for name in ("Triton Drinks", "Solara", "ShopBot", "Acme Bottled Water"):
        caveats = build_micro_simulation_caveats(
            product_name=name,
            total_categories=10,
            represented_categories=3,
            sample_size=5,
            core_count=4,
            adjacent_count=1,
            is_market_entry=True,
            is_unlaunched=True,
        )
        blob = " | ".join(caveats)
        assert name in blob, (
            f"caveat list must reference active product {name!r}"
        )


# ---------------------------------------------------------------------------
# 4. Market-entry unlaunched product gets unlaunched-product caveat
# ---------------------------------------------------------------------------


def test_market_entry_brief_gets_unlaunched_caveat() -> None:
    caveats = build_micro_simulation_caveats(
        product_name="ProductX",
        total_categories=15,
        represented_categories=4,
        sample_size=7,
        core_count=5,
        adjacent_count=2,
        is_market_entry=True,
        is_unlaunched=True,
    )
    blob = " | ".join(caveats).lower()
    assert "unlaunched-product caveat" in blob


# ---------------------------------------------------------------------------
# 5. Classic launched product does NOT get unlaunched-product caveat
# ---------------------------------------------------------------------------


def test_classic_launched_brief_does_not_get_unlaunched_caveat() -> None:
    caveats = build_micro_simulation_caveats(
        product_name="Established Inc",
        total_categories=8,
        represented_categories=4,
        sample_size=10,
        core_count=10,
        adjacent_count=0,
        is_market_entry=False,
        is_unlaunched=False,
    )
    blob = " | ".join(caveats).lower()
    assert "unlaunched-product caveat" not in blob


# ---------------------------------------------------------------------------
# 6. Adjacent-tier caveat appears only when adjacent_count > 0
# ---------------------------------------------------------------------------


def test_adjacent_caveat_only_when_adjacent_count_positive() -> None:
    with_adjacent = build_micro_simulation_caveats(
        product_name="X",
        total_categories=5, represented_categories=3,
        sample_size=5, core_count=3, adjacent_count=2,
    )
    without_adjacent = build_micro_simulation_caveats(
        product_name="X",
        total_categories=5, represented_categories=3,
        sample_size=5, core_count=5, adjacent_count=0,
    )
    assert any(
        "adjacent-tier caveat" in c.lower() for c in with_adjacent
    )
    assert not any(
        "adjacent-tier caveat" in c.lower() for c in without_adjacent
    )


# ---------------------------------------------------------------------------
# 7. Geography caveat appears when geography_strength is soft / absent
# ---------------------------------------------------------------------------


def test_geography_caveat_appears_when_soft() -> None:
    caveats_soft = build_micro_simulation_caveats(
        product_name="X",
        geography="California, United States",
        total_categories=5, represented_categories=3,
        sample_size=5, core_count=5, adjacent_count=0,
        geography_strength="soft",
    )
    blob = " | ".join(caveats_soft).lower()
    assert "geography caveat" in blob


def test_geography_caveat_omitted_when_strong() -> None:
    caveats_strong = build_micro_simulation_caveats(
        product_name="X",
        geography="California, United States",
        total_categories=5, represented_categories=3,
        sample_size=5, core_count=5, adjacent_count=0,
        geography_strength="strong",
    )
    blob = " | ".join(caveats_strong).lower()
    assert "geography caveat" not in blob


def test_detect_geography_strength_strong_when_local_evidence_thick() -> None:
    assert detect_geography_strength(
        geography="California",
        geography_categories_in_audience=5,
        total_geography_categories=1,
    ) == "strong"


def test_detect_geography_strength_soft_when_geography_present_but_thin() -> None:
    assert detect_geography_strength(
        geography="California",
        geography_categories_in_audience=0,
        total_geography_categories=1,
    ) == "soft"


def test_detect_geography_strength_absent_when_no_geography() -> None:
    assert detect_geography_strength(
        geography=None,
        geography_categories_in_audience=0,
        total_geography_categories=0,
    ) == "absent"


# ---------------------------------------------------------------------------
# Coverage-thinness caveat uses dynamic counts (NOT hardcoded 8)
# ---------------------------------------------------------------------------


def test_coverage_thinness_caveat_uses_actual_total_count() -> None:
    caveats = build_micro_simulation_caveats(
        product_name="ProductX",
        total_categories=23,         # Triton-style 23 categories
        represented_categories=4,
        sample_size=7,
        core_count=5,
        adjacent_count=2,
    )
    blob = " | ".join(caveats)
    assert "4 of 23" in blob
    # The previous bug used "8" hardcoded. Make sure NEITHER the
    # plan total NOR a literal "of 8" leftover appears.
    assert "of 8 stakeholder" not in blob
    assert "8 Amboras" not in blob


# ---------------------------------------------------------------------------
# 8. Evaluator: detects evidence-grounded objections
# ---------------------------------------------------------------------------


def _result_dict_with_grounded_persona() -> dict:
    """Synthetic minimal MicroSimulationResult dict with one persona
    whose objection round trace has triggered_by_evidence_excerpt."""
    return {
        "persona_count": 1,
        "persona_states_initial": [{
            "persona_id": "p1", "display_name": "Test Persona",
            "matched_category_key": "competitor_user_red_bull",
            "evidence_excerpts": {
                "interests": "I drink Red Bull every day for studying",
            },
        }],
        "persona_states_final": [{
            "persona_id": "p1", "display_name": "Test Persona",
            "current_stance": "skeptical",
        }],
        "trace": {
            "rounds": [
                {
                    "persona_id": "p1", "round_kind": "objection",
                    "reasoning": (
                        "I drink Red Bull every day for studying — "
                        "I would need to know exact caffeine content "
                        "before switching brands"
                    ),
                    "objections": [
                        "Need exact caffeine content (mg per can) "
                        "before switching from Red Bull",
                    ],
                    "evidence_citations": ["Red Bull every day"],
                    "triggered_by_evidence_excerpt": "Red Bull every day",
                    "output_audit_passed": True,
                },
            ],
            "debate_turns": [],
        },
        "caveats": [
            "MICRO-TEST: this is a mechanical micro-test.",
            "sample-size caveat: n=1 personas; not population.",
            "coverage-thinness caveat: 1 of 5 categories for "
            "Test Product.",
            "not-a-forecast caveat: not a demand forecast.",
        ],
        "summary_text": "MICRO-TEST output for Test Product",
    }


def test_evaluator_detects_evidence_grounded_persona_as_pass() -> None:
    rd = _result_dict_with_grounded_persona()
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull"],
        total_plan_categories=5,
    )
    assert (
        report.dimensions["evidence_grounding_score"].status
        == QualityDimensionStatus.PASS
    )


def test_evaluator_detects_competitor_comparison_as_pass() -> None:
    rd = _result_dict_with_grounded_persona()
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull", "Monster"],
        total_plan_categories=5,
    )
    assert (
        report.dimensions["competitor_comparison_score"].status
        == QualityDimensionStatus.PASS
    )


# ---------------------------------------------------------------------------
# 10. Evaluator: detects missing caveats
# ---------------------------------------------------------------------------


def test_evaluator_flags_missing_caveats() -> None:
    rd = _result_dict_with_grounded_persona()
    rd["caveats"] = ["just one short caveat string"]
    rd["enriched_caveats"] = rd["caveats"]
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull"],
        total_plan_categories=5,
    )
    assert (
        report.dimensions["caveat_integrity_score"].status
        != QualityDimensionStatus.PASS
    )


def test_evaluator_flags_amboras_leakage() -> None:
    rd = _result_dict_with_grounded_persona()
    rd["caveats"] = [
        "MICRO-TEST: ok",
        "sample-size caveat: n=1",
        "coverage-thinness caveat: 1 of 8 Amboras categories",
        "not-a-forecast caveat: ok",
    ]
    rd["enriched_caveats"] = rd["caveats"]
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull"],
        total_plan_categories=5,
    )
    issues = report.dimensions["caveat_integrity_score"].issues
    assert any("Amboras" in i for i in issues)


# ---------------------------------------------------------------------------
# 11. Evaluator: rejects fake forecast / buy percentage / verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "Test Product will succeed in California",
    "30% of merchants will adopt this",
    "we should build it",
    "verdict: ship the product",
    "tiny_ready = true",
    "this persona is representative of the target market",
])
def test_evaluator_anti_fake_claim_dimension_blocks_phrase(
    phrase: str,
) -> None:
    rd = _result_dict_with_grounded_persona()
    rd["trace"]["rounds"][0]["reasoning"] = (
        rd["trace"]["rounds"][0]["reasoning"] + " " + phrase
    )
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull"],
        total_plan_categories=5,
    )
    assert (
        report.dimensions["anti_fake_claim_score"].status
        != QualityDimensionStatus.PASS
    )


# ---------------------------------------------------------------------------
# 12. Evaluator: returns expansion_readiness from closed enum
# ---------------------------------------------------------------------------


def test_evaluator_returns_closed_enum_expansion_readiness() -> None:
    rd = _result_dict_with_grounded_persona()
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull"],
        total_plan_categories=5,
    )
    assert isinstance(report.expansion_readiness, ExpansionReadiness)
    assert report.expansion_readiness.value in (
        "not_ready", "ready_for_prompt_fix",
        "ready_for_larger_micro_sim", "ready_for_source_expansion",
    )


# ---------------------------------------------------------------------------
# 13. Existing Triton 8.4B live JSON scores as useful-but-not-full
# ---------------------------------------------------------------------------


def test_evaluator_against_existing_triton_8_4b_live_json() -> None:
    audit_path = (
        Path(__file__).resolve().parent.parent
        / "_audit" / "triton_micro_simulation_live_8_4b.json"
    )
    if not audit_path.is_file():
        pytest.skip(
            "Phase 8.4B live audit JSON not present; skipping."
        )
    rd = json.loads(audit_path.read_text(encoding="utf-8"))
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Triton Drinks",
        competitors=[
            "Red Bull", "Monster", "Celsius", "Prime", "Gatorade",
            "pre-workout", "preworkout", "cold brew", "coffee",
            "electrolyte",
        ],
        total_plan_categories=23,
    )
    # Useful insight signal:
    assert (
        report.dimensions["evidence_grounding_score"].status
        == QualityDimensionStatus.PASS
    )
    assert (
        report.dimensions["competitor_comparison_score"].status
        == QualityDimensionStatus.PASS
    )
    assert (
        report.dimensions["anti_fake_claim_score"].status
        == QualityDimensionStatus.PASS
    )
    assert (
        report.dimensions["stance_validity_score"].status
        == QualityDimensionStatus.PASS
    )
    # NOT full-society-ready: coverage is partial (4 of 23) AND
    # caveat_integrity fails on the pre-Phase-8.4B.1 audit (Amboras
    # leak + missing not-a-forecast marker).
    assert (
        report.dimensions["coverage_score"].status
        != QualityDimensionStatus.PASS
    )
    # The expansion_readiness recommendation should be one of the
    # closed enum values (specific value depends on which dimensions
    # fail; the existing 8.4B audit fails caveat_integrity which is
    # critical, so it should be NOT_READY).
    assert isinstance(report.expansion_readiness, ExpansionReadiness)


# ---------------------------------------------------------------------------
# Bonus: report serialization round-trip
# ---------------------------------------------------------------------------


def test_report_to_dict_round_trips_cleanly() -> None:
    rd = _result_dict_with_grounded_persona()
    report = evaluate_micro_simulation_quality(
        result_dict=rd,
        product_name="Test Product",
        competitors=["Red Bull"],
        total_plan_categories=5,
    )
    d = report_to_dict(report)
    # JSON-serializable
    s = json.dumps(d)
    assert "Test Product" in s
    parsed = json.loads(s)
    assert "expansion_readiness" in parsed
    assert "dimensions" in parsed
