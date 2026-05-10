"""Phase 8.4B.2 — objection-specificity prompt-fix tests.

Operator scenarios covered (13 total):

  1. Objection prompt requires exactly one primary objection.
  2. Objection prompt requires a concrete anchor.
  3. Objection prompt lists acceptable anchor types.
  4. Objection prompt bans standalone hedge-only objections.
  5. Objection prompt requires evidence tie-back when possible.
  6. Objection prompt forbids inventing missing product facts.
  7. Objection prompt preserves anti-forecast / anti-verdict /
     anti-buy-percentage rules.
  8. Stance enum behavior unchanged (closed set unchanged).
  9. Repair-loop behavior unchanged (run_llm_round signature, audit
     hooks, schema parsing branch all still in place).
 10. Quality-evaluator regex thresholds unchanged.
 11. Existing 8.4B-RERUN JSON still evaluates the same way (stability;
     prompt fix does not retroactively rescore saved output).
 12. Full unit tests pass (verified by harness regression).
 13. Full integration tests pass (verified by harness regression).

Drift checks added on top:

 14. rounds.py code (post-docstring strip) hardcodes no product
     brand names; the prompt is product-agnostic.
 15. Specificity contract is shared (referenced from all three LLM-
     backed rounds), not duplicated three times.

NO LIVE LLM. NO INGESTION. NO DB writes. Pure prompt-text + module-
import + JSON-evaluator inspection.
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from assembly.pipeline.micro_simulation import rounds as rounds_mod
from assembly.pipeline.micro_simulation.quality_evaluator import (
    QualityDimensionStatus,
    evaluate_micro_simulation_quality,
)
from assembly.pipeline.micro_simulation.schemas import (
    MicroPersonaState,
    MicroRelevanceLabel,
    MicroRoundKind,
    MicroStance,
)


# ---------------------------------------------------------------------------
# Test fixture — minimal MicroPersonaState
# ---------------------------------------------------------------------------


def _state() -> MicroPersonaState:
    return MicroPersonaState(
        persona_id="p1",
        display_name="Test Persona",
        relevance_label=MicroRelevanceLabel.RELEVANT,
        matched_category_key="competitor_user_x",
        relevance_score=30,
        supported_traits={
            "current_pain": "energy crashes from sugary drinks",
            "switching_trigger": "lower-sugar option",
        },
        evidence_excerpts={
            "current_pain": (
                "I get headaches from regular energy drinks "
                "because of the sugar load."
            ),
        },
        initial_stance=MicroStance.CURIOUS_HESITANT,
        current_stance=MicroStance.CURIOUS_HESITANT,
    )


def _build(round_kind: MicroRoundKind) -> str:
    return rounds_mod._build_user_prompt(  # type: ignore[attr-defined]
        state=_state(),
        round_kind=round_kind,
        brief_summary="ProductX in the energy-drink category at $3.99.",
    )


# ---------------------------------------------------------------------------
# 1. OBJECTION prompt requires exactly one primary objection
# ---------------------------------------------------------------------------


def test_objection_round_requires_exactly_one_primary_objection() -> None:
    p = _build(MicroRoundKind.OBJECTION)
    # Must explicitly cap at 1 (length-1 list).
    assert "list of length EXACTLY 1" in p
    # Must mention primary / strongest single objection.
    assert "SINGLE STRONGEST" in p or "ONE primary" in p or "one primary" in p
    # Must explicitly forbid padding.
    assert "Do NOT pad" in p or "do not pad" in p.lower()


# ---------------------------------------------------------------------------
# 2 + 3. OBJECTION prompt requires a concrete anchor + lists types
# ---------------------------------------------------------------------------


def test_objection_round_lists_concrete_anchor_types() -> None:
    p = _build(MicroRoundKind.OBJECTION)
    assert "CONCRETE ANCHOR" in p
    # Each anchor category must be named (the operator-spec'd 12-item
    # list). We sample the salient ones — at least these MUST appear:
    must_appear = (
        "price",
        "quantity",
        "caffeine",
        "sugar",
        "ingredient",
        "sweetener",
        "flavor",
        "distribution",
        "channel",
        "availability",
        "competitor",
        "substitute",
        "recall",
        "safety",
        "stacking",
        "switching trigger",
        "use-case mismatch",
        "proof",
        "review",
    )
    for anchor in must_appear:
        assert anchor in p.lower(), f"missing anchor type {anchor!r}"
    # Concrete numeric examples must be in the prompt so the LLM
    # patterns the evaluator's regex (`$\d+`, `\d+\s*(mg|g|ml|oz|cans?)`).
    assert "$3.99" in p or "$30/mo" in p
    assert "mg" in p
    assert "oz" in p


# ---------------------------------------------------------------------------
# 4. OBJECTION prompt bans standalone hedge-only objections
# ---------------------------------------------------------------------------


def test_objection_round_bans_standalone_hedges() -> None:
    p = _build(MicroRoundKind.OBJECTION)
    assert "NO STANDALONE HEDGE" in p
    # Specific hedge phrases the evaluator's regex penalizes:
    for hedge in ("not sure", "maybe", "I don't know", "might be risky"):
        assert hedge in p, f"hedge phrase {hedge!r} must appear in ban list"
    # The prompt must clarify: hedge IS allowed when paired with anchor.
    assert "ONLY when" in p or "only when" in p.lower()


# ---------------------------------------------------------------------------
# 5. OBJECTION prompt requires evidence tie-back when persona has it
# ---------------------------------------------------------------------------


def test_objection_round_requires_evidence_tie_back() -> None:
    p = _build(MicroRoundKind.OBJECTION)
    assert "EVIDENCE TIE-BACK" in p
    assert "evidence_citations" in p
    assert "never invented" in p.lower() or "never invent" in p.lower()


# ---------------------------------------------------------------------------
# 6. OBJECTION prompt forbids inventing missing product facts
# ---------------------------------------------------------------------------


def test_objection_round_forbids_fact_invention() -> None:
    p = _build(MicroRoundKind.OBJECTION)
    assert "NO FACT INVENTION" in p
    # Must direct the LLM to object to the ABSENCE of disclosure.
    p_low = p.lower()
    assert (
        "absence" in p_low or "not disclosed" in p_low
        or "panel is missing" in p_low or "not available" in p_low
    )
    # Specific ban on inventing numbers / ingredients / flavors:
    assert "Inventing" in p or "DO NOT make them up" in p


# ---------------------------------------------------------------------------
# 7. OBJECTION prompt preserves anti-forecast / anti-verdict rules
# ---------------------------------------------------------------------------


def test_objection_round_preserves_forbidden_language_rules() -> None:
    # Inline preservation in the OBJECTION round prompt:
    p = _build(MicroRoundKind.OBJECTION)
    p_low = p.lower()
    assert "no forecast" in p_low
    assert "no verdict" in p_low
    assert "no buy-percentage" in p_low or "no buy percentage" in p_low

    # System-prompt-level preservation (untouched by the 8.4B.2 fix):
    sys_prompt = rounds_mod._SYSTEM_PROMPT  # type: ignore[attr-defined]
    assert '"will succeed"' in sys_prompt
    assert '"will fail"' in sys_prompt
    assert '"verdict:"' in sys_prompt
    assert '"build it"' in sys_prompt or '"kill it"' in sys_prompt
    assert '"the society thinks"' in sys_prompt
    assert "representative of the market" in sys_prompt


# ---------------------------------------------------------------------------
# 8. Stance enum behavior unchanged
# ---------------------------------------------------------------------------


def test_micro_stance_enum_closed_set_unchanged() -> None:
    expected = {
        "strongly_interested",
        "mildly_interested",
        "curious_hesitant",
        "confused",
        "skeptical",
        "resistant",
    }
    assert {s.value for s in MicroStance} == expected
    # And the system prompt still embeds the enum list:
    sys_prompt = rounds_mod._SYSTEM_PROMPT  # type: ignore[attr-defined]
    for v in expected:
        assert f"'{v}'" in sys_prompt


# ---------------------------------------------------------------------------
# 9. Repair-loop behavior unchanged (run_llm_round signature + branches)
# ---------------------------------------------------------------------------


def test_run_llm_round_signature_and_repair_branches_unchanged() -> None:
    sig = inspect.signature(rounds_mod.run_llm_round)
    # Same kw-only parameter set as Phase 8.2K.
    assert set(sig.parameters.keys()) == {
        "state", "round_kind", "brief_summary", "sessionmaker",
        "simulation_id", "provider", "model",
    }
    # The audit-fail branches that drive the 1-retry repair loop
    # (stance shift requires triggered_by; stance not in closed enum;
    # forbidden-language detected) must all still be present in the
    # function source.
    src = inspect.getsource(rounds_mod.run_llm_round)
    assert "stance_after = state.current_stance" in src
    assert "stance shifted but triggered_by_evidence_excerpt" in src
    assert "not in closed enum" in src
    assert "forbidden language detected" in src


# ---------------------------------------------------------------------------
# 10. Quality-evaluator thresholds unchanged
# ---------------------------------------------------------------------------


def test_evaluator_specificity_regex_unchanged() -> None:
    from assembly.pipeline.micro_simulation import quality_evaluator as qe
    # Generic (hedge) patterns — exact list:
    generic_patterns = [p.pattern for p in qe._GENERIC_OBJECTION_PATTERNS]
    assert generic_patterns == [
        r"\bnot sure\b",
        r"\bI don'?t know\b",
        r"\bmaybe\b",
        r"\bmight (be|not be)\b",
        r"\bjust (a|an) regular\b",
    ]
    # Specific patterns — exact list:
    specific_patterns = [p.pattern for p in qe._SPECIFIC_OBJECTION_PATTERNS]
    assert specific_patterns[0] == r"\$\d+"
    assert "caffeine" in specific_patterns[1]
    assert "sugar" in specific_patterns[1]
    assert "flavor" in specific_patterns[1]
    assert specific_patterns[2] == r"\b\d+\s*(?:mg|g|ml|oz|cans?)\b"
    # Threshold steps unchanged:
    src = inspect.getsource(qe._eval_objection_specificity)
    assert "score >= 0.7" in src
    assert "score >= 0.4" in src


# ---------------------------------------------------------------------------
# 11. Existing 8.4B-RERUN JSON still evaluates the same way
# ---------------------------------------------------------------------------


def test_existing_8_4b_rerun_json_eval_stability() -> None:
    """The prompt change is text-only. Evaluating the SAVED rerun JSON
    must yield the same dimension scores it did before — no
    retroactive rescoring."""
    audit_path = (
        Path(__file__).resolve().parent.parent
        / "_audit" / "triton_micro_simulation_live_8_4b_rerun.json"
    )
    if not audit_path.is_file():
        pytest.skip("Phase 8.4B-RERUN JSON not present; skipping.")
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
    # The pre-fix rerun JSON was scored by the Phase 8.4B.1 evaluator
    # at: caveat_integrity=PASS, anti_fake=PASS, stance_validity=PASS,
    # objection_specificity~0.32 (FAIL). These must remain identical
    # because the JSON file is frozen and the evaluator is unchanged.
    assert (
        report.dimensions["caveat_integrity_score"].status
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
    spec = report.dimensions["objection_specificity_score"]
    assert spec.status == QualityDimensionStatus.FAIL
    assert 0.30 <= spec.score <= 0.35


# ---------------------------------------------------------------------------
# 14. rounds.py code (post-docstring strip) hardcodes no brand names
# ---------------------------------------------------------------------------


def _strip_docstrings_and_comments(src: str) -> str:
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


def test_no_hardcoded_brand_names_in_rounds_module_code() -> None:
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline" / "micro_simulation"
        / "rounds.py"
    )
    code_only = _strip_docstrings_and_comments(src_path.read_text("utf-8"))
    forbidden = (
        "Triton", "Amboras", "Red Bull", "Monster", "Celsius",
        "Prime", "Gatorade", "ShopBot", "Solara",
    )
    for term in forbidden:
        assert term not in code_only, (
            f"rounds.py CODE (post-docstring/comment strip) must not "
            f"hardcode brand name {term!r}; the prompt must remain "
            "product-agnostic."
        )


# ---------------------------------------------------------------------------
# 15. Specificity contract is shared, not duplicated three times
# ---------------------------------------------------------------------------


def test_specificity_contract_is_a_single_shared_constant() -> None:
    contract = rounds_mod._OBJECTION_SPECIFICITY_CONTRACT  # type: ignore[attr-defined]
    assert isinstance(contract, str)
    assert "SPECIFICITY CONTRACT" in contract
    # Every LLM-backed round prompt must reference the SAME contract
    # text — i.e. the contract appears once verbatim in each.
    for kind in (
        MicroRoundKind.FIRST_EXPOSURE,
        MicroRoundKind.OBJECTION,
        MicroRoundKind.FINAL_STANCE,
    ):
        rendered = _build(kind)
        # All three rounds must include the contract block. We check
        # the first contract bullet ("CONCRETE ANCHOR (mandatory)")
        # because that line is unique to the contract and won't match
        # round-specific text.
        assert "CONCRETE ANCHOR (mandatory)" in rendered, (
            f"{kind.value} prompt must embed the specificity contract"
        )
    # Source-level: the literal constant is referenced exactly three
    # times in `_build_user_prompt` (one per LLM-backed round).
    src = inspect.getsource(rounds_mod._build_user_prompt)
    occurrences = src.count("_OBJECTION_SPECIFICITY_CONTRACT")
    assert occurrences == 3, (
        f"expected 3 references to _OBJECTION_SPECIFICITY_CONTRACT "
        f"(one per LLM-backed round), got {occurrences}"
    )


# ---------------------------------------------------------------------------
# Bonus: FIRST_EXPOSURE objection-emission is guarded
# ---------------------------------------------------------------------------


def test_first_exposure_round_caps_objections_at_one() -> None:
    p = _build(MicroRoundKind.FIRST_EXPOSURE)
    # The FIRST_EXPOSURE prompt must allow either an empty list or at
    # most one specific objection — not unconstrained list emission.
    assert "AT MOST ONE objection" in p or "at most one objection" in p
    assert "deferring to the OBJECTION round" in p


def test_final_stance_round_requires_one_specific_objection() -> None:
    p = _build(MicroRoundKind.FINAL_STANCE)
    assert "list of length EXACTLY 1" in p
    assert "remaining strongest" in p
    assert "SPECIFICITY CONTRACT" in p
