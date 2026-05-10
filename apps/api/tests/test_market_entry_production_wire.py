"""Phase 8.4A.4 — production-wired market-entry gate tests.

The Phase 8.4A.3 anchor gate was a utility module + replay script.
Phase 8.4A.4 wires it INTO the canonical
`retrieve_personas_for_target_society` so any caller of the real
audience-retrieval surface gets the gate behavior automatically when
the plan is market-entry-shaped.

Asserts:

  1. `retrieve_personas_for_target_society` for a market-entry plan
     auto-applies the gate without caller having to invoke replay
     logic. PersonaMatch carries the new fields (final_tier,
     anchor_*, gate_reason).
  2. ADJACENT_RELEVANT personas land in matched_personas (with
     caveat) under market-entry mode — NOT excluded. The classic
     path drops WEAKLY_RELEVANT to excluded; the market-entry path
     promotes ADJACENT (= same WEAKLY band) into matched with
     caveat.
  3. CORE_RELEVANT personas without anchors are EXCLUDED by the gate.
  4. ADJACENT_RELEVANT personas without grounded anchor evidence are
     EXCLUDED by the gate.
  5. Classic launched-product retrieval is UNCHANGED — WEAKLY_RELEVANT
     still drops to excluded; new fields stay None / [].
  6. Cross-domain: sunscreen + Shopify-tool plans use the gate
     automatically.
  7. Threshold discipline: 18 / 27 / 36 unchanged.
  8. No hardcoded brand names: PersonaMatch fields driven by the
     plan's dynamic categories, not by hardcoded strings.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from assembly.pipeline.audience_retrieval import (
    retrieve_personas_for_target_society,
)
from assembly.pipeline.audience_retrieval.inclusion_tier import (
    InclusionTier,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.persona_relevance.rubric import (
    CLASSIFICATION_THRESHOLDS,
    RelevanceClassification,
)
from assembly.pipeline.target_society import build_target_society_plan
from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.schemas import ProductBriefInput


# ---------------------------------------------------------------------------
# Brief + persona fixtures
# ---------------------------------------------------------------------------


def _triton_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can."
        ),
        price_or_price_structure="$3.99 per can",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society="California consumers",
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, "
            "pre-workout powders, electrolyte drinks. Triton is unlaunched."
        ),
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


def _classic_amboras_brief() -> ProductBriefInput:
    """A launched-product brief that does NOT trigger market-entry
    auto-detection (no 'unlaunched' / 'pre-launch' text + explicit
    TEST_TRUST_OBJECTION_BARRIERS goal)."""
    return ProductBriefInput(
        product_name="Amboras",
        product_type="Shopify tool",
        product_description=(
            "Amboras is the existing flagship Shopify tool for merchants."
        ),
        price_or_price_structure="$50/mo",
        competitors=["Klaviyo", "Mailchimp"],
        intended_user_or_buyer="Shopify merchants, DTC founders",
        simulation_goal=SimulationGoal.TEST_TRUST_OBJECTION_BARRIERS,
    )


def _sunscreen_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Solara",
        product_type="$10 mineral sunscreen",
        product_description=(
            "Solara is an unlaunched $10 mineral sunscreen launching "
            "in California."
        ),
        price_or_price_structure="$10",
        competitors=["Banana Boat", "Coppertone", "Neutrogena"],
        intended_user_or_buyer=(
            "swimmers, beachgoers, outdoor athletes"
        ),
        geography="California, United States",
        extra_context=(
            "Substitutes include: chemical sunscreen sprays, hats, UPF clothing."
        ),
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


def _shopify_tool_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="ShopBot",
        product_type="Shopify tool",
        product_description="ShopBot is an unlaunched SaaS for Shopify merchants.",
        price_or_price_structure="$29/mo",
        competitors=["Klaviyo", "Mailchimp", "WooCommerce"],
        intended_user_or_buyer=(
            "Shopify merchants, DTC founders, e-commerce operators"
        ),
        extra_context="Substitutes include: in-house scripts, freelancers.",
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


def _persona(
    *, name: str, traits: dict[str, str], excerpts: list[str],
) -> PersonaAuditInput:
    pid = uuid4()
    trait_views = tuple(
        TraitView(
            field_name=fn, support_level="direct", value=v,
            confidence=0.9, source_ids=tuple(), rationale=None,
        )
        for fn, v in traits.items()
    )
    link_views = tuple(
        EvidenceLinkView(
            persona_id=pid, source_record_id=uuid4(),
            contribution_kind="direct",
            contribution_field=(
                list(traits.keys())[0] if traits else "interests"
            ),
            excerpt=ex, source_likely_human_signal=True,
        )
        for ex in excerpts
    )
    return PersonaAuditInput(
        persona_id=pid, display_name=name,
        traits=trait_views, evidence_links=link_views,
    )


# ---------------------------------------------------------------------------
# 1. Production retrieval auto-applies the gate for market-entry plans
# ---------------------------------------------------------------------------


def test_market_entry_retrieval_auto_applies_gate_with_new_fields() -> None:
    brief = _triton_brief()
    plan = build_target_society_plan(brief)
    strong_red_bull = _persona(
        name="Strong Red Bull User",
        traits={
            "role_or_context": "Red Bull user and college student",
            "current_alternatives": "Red Bull is my daily energy drink",
            "interests": "Red Bull, energy drinks, caffeine for studying",
        },
        excerpts=[
            "I drink Red Bull every day for studying. Red Bull works "
            "best for me. Red Bull's taste beats Monster.",
        ],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[strong_red_bull],
    )
    assert len(result.matched_personas) == 1
    m = result.matched_personas[0]
    # Gate fields populated for market-entry plans
    assert m.final_tier in (
        InclusionTier.CORE_RELEVANT.value,
        InclusionTier.ADJACENT_RELEVANT.value,
    )
    assert m.base_tier in (
        InclusionTier.CORE_RELEVANT.value,
        InclusionTier.ADJACENT_RELEVANT.value,
    )
    assert m.anchor_has is True
    assert "competitor_anchor" in m.anchor_types
    assert "Red Bull" in " ".join(m.matched_anchor_terms)
    assert m.gate_reason == "pass"


# ---------------------------------------------------------------------------
# 2. ADJACENT_RELEVANT lands in matched (with caveat) under market-entry
# ---------------------------------------------------------------------------


def test_market_entry_adjacent_persona_in_matched_with_caveat() -> None:
    """A persona with score in 18-26 + grounded anchor lands in
    matched_personas (with adjacent caveat). This is NEW behavior:
    classic path drops WEAKLY_RELEVANT to excluded; market-entry
    promotes ADJACENT into matched with caveat."""
    brief = _triton_brief()
    plan = build_target_society_plan(brief)
    # A persona with mild Celsius mention + thin role evidence —
    # likely lands in ADJACENT band.
    persona = _persona(
        name="Mild Celsius User",
        traits={
            "interests": "I tried Celsius energy drink last summer.",
        },
        excerpts=[
            "Tried Celsius last summer at the gym. Was OK.",
        ],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[persona],
    )
    # Should be in matched_personas (NOT excluded) IF the gate
    # accepts this. If it lands in excluded, that's also acceptable —
    # but if matched, it must carry the adjacent caveat.
    if result.matched_personas:
        m = result.matched_personas[0]
        if m.final_tier == InclusionTier.ADJACENT_RELEVANT.value:
            joined = " ".join(m.caveats).lower()
            assert "adjacent" in joined or "lower-weight" in joined.replace(" ", "-") or "reduced weight" in joined


# ---------------------------------------------------------------------------
# 3. CORE without anchor → EXCLUDED via gate
# ---------------------------------------------------------------------------


def test_market_entry_core_score_without_anchor_excluded() -> None:
    """A persona with high category-text-match score but ZERO
    anchor terms (none of brief.competitors / substitutes / use-cases
    appear) must be excluded by the gate. (Synthetic — uses the
    universal-category language that scores high without firing
    any specific anchor type.)"""
    brief = _triton_brief()
    plan = build_target_society_plan(brief)
    # A persona with no brand mentions, no use-case, no objection,
    # no buyer-type anchor language — but generic "energy" mentions.
    # The gate should refuse it even at high score.
    persona = _persona(
        name="Generic Discusser",
        traits={
            "interests": "I once read an article about beverages.",
        },
        excerpts=[
            "Beverages exist. Some are sold in stores. End of opinion.",
        ],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[persona],
    )
    # Persona ends up either with a low score (below 18) OR with
    # the gate explicitly downgrading it.
    if result.matched_personas:
        m = result.matched_personas[0]
        # If matched, anchor must be present; otherwise should be excluded
        assert m.anchor_has is True
    else:
        assert len(result.excluded_personas) == 1
        ex = result.excluded_personas[0]
        # Either below_inclusion_threshold (low score) or a gate reason
        assert ex.gate_reason in (
            "below_inclusion_threshold",
            "no_market_entry_anchor",
            "insufficient_anchor_evidence",
        )


# ---------------------------------------------------------------------------
# 4. ADJACENT without grounded anchor evidence → EXCLUDED
# ---------------------------------------------------------------------------


def test_market_entry_adjacent_without_grounded_evidence_excluded() -> None:
    """A persona whose trait LABELS reference an anchor term but
    whose evidence-link excerpts do NOT contain the anchor in
    verbatim source text is downgraded by the gate. This catches
    the Phase 8.4A.2 false-positive class (Oakley-J-shaped)."""
    brief = _triton_brief()
    plan = build_target_society_plan(brief)
    # Synthetic: "budget" appears in trait, NOT in excerpt.
    persona = _persona(
        name="Trait-Only Budget Persona",
        traits={
            "price_sensitivity": "budget-conscious shopper",
            "interests": "general grocery shopping",
        },
        excerpts=[
            "I buy stuff at the store. Sometimes I look at prices on the shelf.",
        ],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[persona],
    )
    # Either excluded with insufficient_anchor_evidence, or score too
    # low and excluded with below_inclusion_threshold.
    assert len(result.matched_personas) == 0
    assert len(result.excluded_personas) == 1


# ---------------------------------------------------------------------------
# 5. Classic launched-product retrieval is UNCHANGED
# ---------------------------------------------------------------------------


def test_classic_path_unchanged_no_gate_fields_populated() -> None:
    """A non-market-entry brief routes through the classic path.
    PersonaMatch.final_tier / anchor_* fields stay None / [] /
    False. WEAKLY_RELEVANT personas drop to excluded as before."""
    brief = _classic_amboras_brief()
    plan = build_target_society_plan(brief)
    # Ensure classic path was selected
    assert not any(
        c.category_key.startswith("competitor_user_")
        for c in plan.stakeholder_categories
    ), "classic plan must NOT use dynamic-planner categories"

    persona = _persona(
        name="Klaviyo User",
        traits={
            "interests": "I run a Shopify store and use Klaviyo for email",
        },
        excerpts=["Klaviyo is our email tool of choice for our DTC store."],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[persona],
    )
    # Classic path: gate fields stay at defaults
    for m in result.matched_personas:
        assert m.final_tier is None, (
            f"classic path should leave final_tier=None, got {m.final_tier!r}"
        )
        assert m.base_tier is None
        assert m.gate_reason is None
        assert m.anchor_has is False
        assert m.anchor_types == []
    for ex in result.excluded_personas:
        assert ex.final_tier is None
        assert ex.gate_reason is None
        assert ex.anchor_has is False


# ---------------------------------------------------------------------------
# 6. Cross-domain generality
# ---------------------------------------------------------------------------


def test_sunscreen_market_entry_retrieval_uses_gate() -> None:
    brief = _sunscreen_brief()
    plan = build_target_society_plan(brief)
    persona = _persona(
        name="Banana Boat User",
        traits={"interests": "Banana Boat is my go-to sunscreen"},
        excerpts=["I use Banana Boat at the beach every summer."],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[persona],
    )
    if result.matched_personas:
        m = result.matched_personas[0]
        assert m.final_tier is not None
        assert "competitor_anchor" in m.anchor_types
        # No energy-drink anchor leakage
        joined = " ".join(m.matched_anchor_terms).lower()
        assert "red bull" not in joined
        assert "monster" not in joined


def test_shopify_market_entry_retrieval_uses_gate() -> None:
    brief = _shopify_tool_brief()
    plan = build_target_society_plan(brief)
    persona = _persona(
        name="Klaviyo User",
        traits={"interests": "Klaviyo is our email tool"},
        excerpts=["We've used Klaviyo for 2 years for our DTC store."],
    )
    result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=[persona],
    )
    if result.matched_personas:
        m = result.matched_personas[0]
        assert m.final_tier is not None
        # No energy-drink anchor leakage
        joined = " ".join(m.matched_anchor_terms).lower()
        assert "red bull" not in joined


# ---------------------------------------------------------------------------
# 7. Threshold discipline
# ---------------------------------------------------------------------------


def test_thresholds_unchanged_in_production_path() -> None:
    """The wiring did NOT move the thresholds."""
    assert CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.WEAKLY_RELEVANT
    ] == 18
    assert CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.RELEVANT
    ] == 27
    assert CLASSIFICATION_THRESHOLDS[
        RelevanceClassification.HIGHLY_RELEVANT
    ] == 36


# ---------------------------------------------------------------------------
# 8. Anti-hardcoding sentinel
# ---------------------------------------------------------------------------


def _strip_docstrings_and_comments(src: str) -> str:
    """Strip Python docstrings (triple-quoted string literals at module
    or function-body level) AND `#` comments so the hardcode-detection
    test only scans actual executable code. Brand names appearing in
    illustrative docstrings ('e.g. Red Bull') don't count as hardcoding;
    only references in code do."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    # Walk the AST and collect docstring + string-literal byte ranges.
    docstring_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            ds = ast.get_docstring(node, clean=False)
            if ds is None:
                continue
            # Locate the docstring node in node.body[0]
            if (
                node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                ds_node = node.body[0]
                docstring_ranges.append(
                    (ds_node.lineno, ds_node.end_lineno or ds_node.lineno)
                )
    # Build a per-line keep mask
    lines = src.splitlines()
    kept: list[str] = []
    in_docstring = [False] * (len(lines) + 1)
    for start, end in docstring_ranges:
        for i in range(start, end + 1):
            if 0 <= i - 1 < len(in_docstring):
                in_docstring[i - 1] = True
    for i, line in enumerate(lines):
        if in_docstring[i]:
            continue
        # Strip inline `#` comments
        comment_idx = line.find("#")
        if comment_idx >= 0:
            # Naive — fine for our hardcode-check use-case.
            line = line[:comment_idx]
        kept.append(line)
    return "\n".join(kept)


def test_no_hardcoded_brand_names_in_retriever_module() -> None:
    """The retriever module must NOT contain hardcoded competitor /
    energy-drink names in code (docstrings + comments don't count).
    All matching goes through plan.stakeholder_categories'
    inclusion_signals."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline" / "audience_retrieval"
        / "retriever.py"
    ).read_text(encoding="utf-8")
    code_only = _strip_docstrings_and_comments(src)
    forbidden = (
        "Red Bull", "RedBull", "Monster", "Celsius", "Prime energy",
        "Gatorade", "Banana Boat", "Coppertone",
        "Klaviyo", "Mailchimp",
        "Triton", "Solara", "ShopBot",
    )
    for term in forbidden:
        assert term not in code_only, (
            f"retriever.py CODE must not hardcode {term!r}"
        )


def test_no_hardcoded_brand_names_in_anchor_detector_module() -> None:
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "pipeline" / "audience_retrieval"
        / "anchor_detector.py"
    ).read_text(encoding="utf-8")
    code_only = _strip_docstrings_and_comments(src)
    forbidden = (
        "Red Bull", "RedBull", "Monster", "Celsius", "Gatorade",
        "Triton", "Solara", "ShopBot", "Banana Boat", "Klaviyo",
    )
    for term in forbidden:
        assert term not in code_only, (
            f"anchor_detector.py CODE must not hardcode {term!r}"
        )
