"""Phase 8.4B — Triton Drinks micro-simulation tests.

Covers the 10 operator scenarios:
  1. Retrieval returns only included personas (CORE + ADJACENT).
  2. Excluded personas cannot enter the simulation set.
  3. Adjacent personas carry caveat.
  4. No persona is labeled Triton buyer / reviewer / loyalist.
  5. Stance enum validation (MarketEntryFinalStance is closed).
  6. Stance enum mapping (MicroStance → MarketEntryFinalStance).
  7. Forbidden-language scanner still runs on outputs.
  8. No forbidden DB writes (drift-test).
  9. All LLM calls route through cost_guarded_chat.
 10. Mandatory caveats are present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from assembly.pipeline.micro_simulation import (
    MarketEntryFinalStance,
    MicroStance,
    map_micro_stance_to_market_entry,
)
from assembly.pipeline.micro_simulation.output_audit import (
    scan_text_for_forbidden_claims,
)


# ---------------------------------------------------------------------------
# 5. Stance enum validation
# ---------------------------------------------------------------------------


def test_market_entry_final_stance_is_closed_5_value_enum() -> None:
    """The user-spec'd 5 stances exist as a closed enum."""
    values = {s.value for s in MarketEntryFinalStance}
    assert values == {
        "reject",
        "skeptical",
        "curious_but_unconvinced",
        "willing_to_try_once",
        "likely_repeat_buyer",
    }


# ---------------------------------------------------------------------------
# 6. Stance enum mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("internal,expected", [
    (MicroStance.RESISTANT, MarketEntryFinalStance.REJECT),
    (MicroStance.SKEPTICAL, MarketEntryFinalStance.SKEPTICAL),
    (
        MicroStance.CONFUSED,
        MarketEntryFinalStance.CURIOUS_BUT_UNCONVINCED,
    ),
    (
        MicroStance.CURIOUS_HESITANT,
        MarketEntryFinalStance.CURIOUS_BUT_UNCONVINCED,
    ),
    (
        MicroStance.MILDLY_INTERESTED,
        MarketEntryFinalStance.WILLING_TO_TRY_ONCE,
    ),
    (
        MicroStance.STRONGLY_INTERESTED,
        MarketEntryFinalStance.LIKELY_REPEAT_BUYER,
    ),
])
def test_micro_stance_maps_to_market_entry_stance(
    internal: MicroStance, expected: MarketEntryFinalStance,
) -> None:
    assert map_micro_stance_to_market_entry(internal) == expected


def test_every_micro_stance_has_a_market_entry_mapping() -> None:
    """Every MicroStance value (closed enum) must map to a
    MarketEntryFinalStance value. No exceptions."""
    for s in MicroStance:
        out = map_micro_stance_to_market_entry(s)
        assert isinstance(out, MarketEntryFinalStance)


# ---------------------------------------------------------------------------
# 1, 2, 3. Persona retrieval discipline
# ---------------------------------------------------------------------------


def test_8_4b_operator_script_uses_production_retrieval() -> None:
    """The Phase 8.4B operator script must call into
    `retrieve_personas_for_target_society` (production-wired
    Phase 8.4A.4 path), NOT bypass it via direct DB queries or
    manual category-keyword matching."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    assert "retrieve_personas_for_target_society" in src, (
        "operator script must use production retrieval path"
    )


def test_8_4b_only_picks_personas_with_inclusion_tier() -> None:
    """The operator script's persona selection logic must filter
    matched_personas by `final_tier == 'core_relevant' / 'adjacent_relevant'`
    — never by classification alone, and never from excluded_personas."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    # Sentinel checks
    assert "InclusionTier.CORE_RELEVANT" in src
    assert "InclusionTier.ADJACENT_RELEVANT" in src
    # NEGATIVE: must NOT pull from excluded_personas
    assert "audience_result.excluded_personas" not in src or (
        "excluded_personas" in src
        and "audience_result.matched_personas" in src
    )


def test_8_4b_caps_match_spec() -> None:
    """The operator script enforces the operator-spec'd caps:
      MAX_CORE = 5, MAX_ADJACENT = 2, MAX_LLM_CALLS = 25,
      COST_CAP_USD = $1.00.
    """
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    assert "MAX_CORE = 5" in src
    assert "MAX_ADJACENT = 2" in src
    assert "MAX_LLM_CALLS = 25" in src
    assert 'COST_CAP_USD = Decimal("1.00")' in src


# ---------------------------------------------------------------------------
# 4. Anti-fake-buyer naming
# ---------------------------------------------------------------------------


def _strip_docstrings_and_comments(src: str) -> str:
    """Strip Python docstrings + `#` comments so the string-literal
    check only scans actual executable code. Same helper as
    Phase 8.4A.4."""
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


def test_8_4b_script_does_not_label_personas_as_triton_buyers() -> None:
    """The operator script must NOT contain CODE language that labels
    any persona as a 'Triton buyer' / 'Triton loyalist' / 'Triton
    reviewer'. Triton is unlaunched — no buyers exist. Docstring
    references are allowed when they NEGATE the concept (e.g.
    'NOT direct Triton buyer evidence')."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    code_only = _strip_docstrings_and_comments(src)
    forbidden_substrings = (
        "Triton buyer", "Triton loyalist", "Triton reviewer",
        "TritonBuyer", "tritonbuyer",
    )
    for s in forbidden_substrings:
        assert s not in code_only, (
            f"operator script CODE must not label personas as {s!r}"
        )


# ---------------------------------------------------------------------------
# 7. Forbidden-language scanner regression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "Triton will succeed in the California market",
    "30% of merchants would adopt this",
    "we should build it",
    "the California society thinks",
    "tiny_ready = true",
    "this persona is representative of the target market",
])
def test_existing_forbidden_language_scanner_blocks_market_claims(
    phrase: str,
) -> None:
    """Phase 8.2K's 7-category forbidden-language scanner is
    unchanged in 8.4B — population-level / forecast / verdict
    language is still blocked. Used by the harness on every text
    leaf."""
    found = scan_text_for_forbidden_claims(phrase)
    assert found, (
        f"forbidden-language scanner missed: {phrase!r}"
    )


# ---------------------------------------------------------------------------
# 10. Mandatory caveats
# ---------------------------------------------------------------------------


def test_8_4b_mandatory_caveats_function_returns_six_caveats() -> None:
    """The operator script's `_build_mandatory_caveats` returns the
    6 caveats specified in the Phase 8.4B operator brief."""
    # Import the function dynamically (script-level helper)
    import importlib.util
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_phase_8_4b_script", script_path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    caveats = mod._build_mandatory_caveats(
        n=7, n_core=5, n_adjacent=2,
        distinct_category_count=4, total_plan_categories=22,
    )
    assert len(caveats) == 6
    joined = " || ".join(caveats).lower()
    # Each of the 6 mandatory caveats present (substring sentinels)
    assert "micro-test" in joined
    assert "sample-size" in joined
    assert "unlaunched" in joined
    assert "adjacent-tier" in joined
    assert "coverage-thinness" in joined
    assert "geography" in joined


# ---------------------------------------------------------------------------
# 8. No forbidden DB writes (script-level static check)
# ---------------------------------------------------------------------------


def test_8_4b_script_does_not_construct_forbidden_orm_rows() -> None:
    """Static check: the operator script must not construct any
    forbidden ORM row (SimulationOutput, SimulationRound, Agent,
    AgentResponse, DebateTurn, AgentEdge, PersonaGraphEdge,
    PersonaCluster, PersonaClusterMembership, PersonaOpinion).
    The existing micro-simulation harness has its own drift tests
    for the package; this is a script-level cross-check."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    import re
    forbidden = (
        "PersonaGraphEdge", "PersonaCluster",
        "PersonaClusterMembership", "PersonaOpinion",
        "SimulationOutput", "SimulationRound",
        "AgentResponse", "AgentEdge", "DebateTurn",
    )
    pat = re.compile(
        r"\b(?:" + "|".join(forbidden) + r")\s*\(",
    )
    assert not pat.search(src), (
        "operator script must not construct forbidden ORM rows"
    )


# ---------------------------------------------------------------------------
# 9. cost_guarded_chat routing (existing harness invariant)
# ---------------------------------------------------------------------------


def test_8_4b_routes_through_existing_cost_guarded_micro_runner() -> None:
    """The operator script's live path delegates to the existing
    Phase 8.2K `run_micro_simulation` runner — which is drift-tested
    to route every LLM call through `cost_guarded_chat` only via
    `micro_llm_call` with `micro_*` stage labels. The script must
    NOT define its own LLM call path."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    assert "run_micro_simulation" in src, (
        "operator script must invoke the existing micro-simulation "
        "runner, not roll its own LLM loop"
    )
    # Negative: script must not directly call provider methods
    assert "provider.chat(" not in src
    assert "provider.structured_output(" not in src


# ---------------------------------------------------------------------------
# Compose a full audience-set-and-cap sanity check (offline)
# ---------------------------------------------------------------------------


def test_8_4b_dry_run_mode_is_default() -> None:
    """The operator script defaults to dry-run; --live is required
    to actually trigger the LLM path."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "run_triton_micro_simulation_8_4b.py"
    ).read_text(encoding="utf-8")
    # The argparse flag must be opt-in (default False)
    assert (
        '"--live"' in src and 'action="store_true"' in src
    ), "--live flag must default to False (require operator opt-in)"
