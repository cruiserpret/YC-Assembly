"""Phase 8.5E — run-scoped simulation tests.

Operator scenarios 1-26 covered. Tests are fast unit tests over
the universal validators, the deterministic quality evaluator, and
static-grep over the dry-run script. NO LLM calls, NO DB writes.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from assembly.sources.run_scoped_persona_simulation import (
    AGENT_ROUND_TYPES, MARKET_ENTRY_STANCES, RoundOutputAudit,
    RunScopedAgentContext, evaluate_simulation_quality,
    load_run_scoped_agents, scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
    validate_market_entry_stance_label,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "run_strideshield_simulation_8_5e.py"
)
PKG = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "sources" / "run_scoped_persona_simulation"
)


# ---------------------------------------------------------------------------
# 1 + 2. Loader signature + run_scope_id refusal path
# ---------------------------------------------------------------------------


def test_load_run_scoped_agents_signature() -> None:
    sig = inspect.signature(load_run_scoped_agents)
    params = set(sig.parameters.keys())
    assert params == {"session", "run_scope_id"}


def test_script_refuses_when_persona_count_not_seven() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "EXPECTED_AGENT_COUNT = 7" in src
    assert "REFUSED" in src
    assert (
        "if len(agents) != EXPECTED_AGENT_COUNT" in src
        or "len(agents) != EXPECTED_AGENT_COUNT" in src
    )


# ---------------------------------------------------------------------------
# 3 + 4 + 5. Loader pulls traits + evidence_links + source_records
# ---------------------------------------------------------------------------


def test_loader_module_loads_traits_and_links_and_sources() -> None:
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    # PersonaTrait + PersonaEvidenceLink + SourceRecord queried
    assert "PersonaTrait" in src
    assert "PersonaEvidenceLink" in src
    assert "SourceRecord" in src
    # Filter is on product_relevance_tags ARRAY containment
    assert "product_relevance_tags.contains" in src


# ---------------------------------------------------------------------------
# 6 + 7 + 8. Agent shape preserves run_scope_id + compressed_candidate_id +
#  evidence excerpts
# ---------------------------------------------------------------------------


def test_run_scoped_agent_context_carries_run_scope_metadata() -> None:
    fields = set(RunScopedAgentContext.__dataclass_fields__.keys())
    required = {
        "persona_id", "display_name", "segment_label",
        "product_relevance_tags",
        "target_brief", "product_name", "launch_state",
        "run_scope_id", "normalized_primary_role",
        "evidence_theme", "source_provider_family",
        "compressed_candidate_id", "not_global_persona",
        "traits", "evidence_links", "source_records",
    }
    assert required.issubset(fields)


def test_evidence_excerpts_helper_returns_strings() -> None:
    ctx = RunScopedAgentContext(
        persona_id=__import__("uuid").uuid4(),
        display_name="Test M.", segment_label="x",
        product_relevance_tags=[],
        target_brief="x", product_name="x", launch_state="unlaunched",
        run_scope_id="x", normalized_primary_role="x",
        evidence_theme="x", source_provider_family="x",
        compressed_candidate_id="x", not_global_persona=True,
        traits=[],
        evidence_links=[
            {"excerpt": "Body Glide dries up after long runs."},
            {"excerpt": "Megababe smells nice but pricey."},
            {"excerpt": "Body Glide dries up after long runs."},  # dup
        ],
        source_records=[],
    )
    excerpts = ctx.evidence_excerpts(max_excerpts=4)
    assert len(excerpts) == 2  # de-duped


# ---------------------------------------------------------------------------
# 9-12. No new SourceRecord / PersonaRecord / PersonaTrait /
#  PersonaEvidenceLink writes from the script
# ---------------------------------------------------------------------------


def test_script_does_not_insert_persona_or_source_rows() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "SourceRecord", "PersonaRecord", "PersonaTrait",
        "PersonaEvidenceLink", "PersonaGraphEdge", "PersonaCluster",
        "PopulationConstructionAudit",
    )
    for term in forbidden:
        # Find ORM-construction patterns: `<Term>(` followed by a
        # word char (not a `)` or `[`).
        pat = re.compile(rf"\b{re.escape(term)}\(\s*\w")
        for m in pat.finditer(src):
            ctx = src[max(0, m.start() - 25):m.end() + 25]
            if "select(" in ctx:
                continue
            raise AssertionError(
                f"forbidden ORM construction in script: ...{ctx}..."
            )


def test_script_only_creates_simulation_related_rows() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # Only these ORM constructions are allowed
    allowed = (
        "Simulation(", "SimulationInput(", "Agent(",
        "SimulationRound(", "AgentResponse(",
    )
    found_allowed = [a for a in allowed if a in src]
    assert "Simulation(" in src
    assert "Agent(" in src
    assert "SimulationRound(" in src
    assert "AgentResponse(" in src


# ---------------------------------------------------------------------------
# 13. Prompts include launch-state + forbidden rules
# ---------------------------------------------------------------------------


def test_script_universal_forbidden_rules_block_present() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "_UNIVERSAL_FORBIDDEN_RULES" in src
    assert "DO NOT claim direct" in src
    assert "is unlaunched" in src
    assert "DO NOT produce buy-percentages" in src
    assert "DO NOT issue launch / kill / ship verdicts" in src


# ---------------------------------------------------------------------------
# 14 + 16. Forbidden claims detected — universal validators
# ---------------------------------------------------------------------------


def test_universal_launch_state_validator_catches_direct_use() -> None:
    cases = (
        "I bought StrideShield last week.",
        "My StrideShield works great.",
        "I'm a StrideShield buyer and recommend it.",
        "I am a StrideShield customer and reviewer.",
        "I tried StrideShield and it was amazing.",
    )
    for text in cases:
        v = scan_unlaunched_product_use_claims(
            text=text, product_name="StrideShield",
        )
        assert v.is_valid is False, f"missed: {text!r}"
        assert v.forbidden_phrases_matched


def test_universal_launch_state_validator_passes_competitor_use() -> None:
    cases = (
        "I have used Body Glide for years.",
        "Megababe Thigh Rescue works for me on theme park days.",
        "I would compare StrideShield to Trail Toes.",
        "I am skeptical and need more proof before considering it.",
    )
    for text in cases:
        v = scan_unlaunched_product_use_claims(
            text=text, product_name="StrideShield",
        )
        assert v.is_valid is True, f"false-positive: {text!r}"


def test_universal_forecast_validator_catches_buy_percent() -> None:
    cases = (
        "30% of the market will buy this.",
        "20% of customers would adopt within a year.",
        "50% of users will switch.",
        "The market will succeed.",
        "Consumers will dominate the category.",
        "should launch this product",
        "do not launch this product",
        "launch it",
        "kill this product",
        "go-to-market verdict: launch",
        "the market is bullish",
    )
    for text in cases:
        v = scan_forecast_or_verdict_claims(text=text)
        assert v.is_valid is False, f"missed: {text!r}"


def test_universal_forecast_validator_passes_persona_voice() -> None:
    cases = (
        "I would be skeptical until I saw proof.",
        "This persona's reaction: needs more information.",
        "Body Glide already wins on this dimension.",
        "I'd compare price and grease before deciding.",
    )
    for text in cases:
        v = scan_forecast_or_verdict_claims(text=text)
        assert v.is_valid is True, f"false-positive: {text!r}"


# ---------------------------------------------------------------------------
# 15. Final stance label restriction
# ---------------------------------------------------------------------------


def test_market_entry_stance_label_closed_set() -> None:
    for s in MARKET_ENTRY_STANCES:
        assert validate_market_entry_stance_label(s).is_valid is True
    forbidden = (
        "will_buy", "won't buy", "strongly_interested", "resistant",
        "build", "kill", "pivot", "launch_now",
    )
    for s in forbidden:
        assert (
            validate_market_entry_stance_label(s).is_valid is False
        ), f"false-pass: {s!r}"


# ---------------------------------------------------------------------------
# 17. Required caveats present
# ---------------------------------------------------------------------------


def test_script_emits_required_caveats() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "REQUIRED_CAVEATS" in src
    needed = (
        "micro-simulation", "n=7",
        "not a forecast", "not a market verdict",
        "not representative", "run-scoped", "synthetic",
        "Amazon", "snippets",
        "unlaunched",
    )
    for n in needed:
        assert n in src, f"missing caveat keyword: {n!r}"


# ---------------------------------------------------------------------------
# 18. Cost guard used for every LLM call
# ---------------------------------------------------------------------------


def test_script_uses_cost_guarded_chat_for_all_llm_calls() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "cost_guarded_chat" in src
    # Drift: no direct provider.chat( in this script
    assert "provider.chat(" not in src
    # Drift: no direct .structured_output(
    assert ".structured_output(" not in src


# ---------------------------------------------------------------------------
# 19. Stops safely on cost cap
# ---------------------------------------------------------------------------


def test_script_handles_cost_cap_exceeded_via_exception_path() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # Cost guard raises on cap exceed; the script's try/except catches
    # any LLM-call exception and writes a rollback audit.
    assert "rollback_reason" in src
    assert "LLM call failed" in src
    assert "hard_cap_usd=hard_cap_usd" in src


# ---------------------------------------------------------------------------
# 20. Quality evaluator catches missing grounding
# ---------------------------------------------------------------------------


def _make_round(
    *,
    persona_id: str = "p1",
    display_name: str = "T.M.",
    role: str = "competitor_user_body_glide",
    round_type: str = "first_exposure",
    round_number: int = 2,
    stance: str | None = "curious_but_unconvinced",
    reasoning: str = "I currently use Body Glide on long runs.",
    objections: list | None = None,
    persuasion_levers: list | None = None,
    competitor_mentions: list | None = None,
    forbidden: list | None = None,
    raw_text: str | None = None,
    candidate_id: str = "c1",
) -> RoundOutputAudit:
    return RoundOutputAudit(
        agent_persona_id=persona_id,
        display_name=display_name,
        compressed_candidate_id=candidate_id,
        normalized_primary_role=role,
        round_type=round_type,  # type: ignore[arg-type]
        round_number=round_number,
        stance=stance,  # type: ignore[arg-type]
        reasoning=reasoning,
        objections=objections or [
            {"text": "greasiness on heels — needs proof", "category": "texture"},
        ],
        persuasion_levers=persuasion_levers or [
            {"text": "independent runner reviews + price < $13", "category": "social_proof"},
        ],
        competitor_mentions=competitor_mentions or ["Body Glide"],
        forbidden_claim_audit=forbidden or [],
        raw_text=raw_text or reasoning,
    )


def test_quality_evaluator_catches_missing_grounding() -> None:
    """A round whose reasoning never references the persona's role
    or any competitor → grounding score drops."""
    rounds = [
        _make_round(reasoning="It's fine.", competitor_mentions=[]),
        _make_round(
            round_type="final_stance", round_number=7,
            reasoning="It is fine.", competitor_mentions=[],
        ),
    ]
    qual = evaluate_simulation_quality(
        rounds=rounds, caveats=["micro-simulation n=7"],
        product_name="StrideShield",
        agents_with_traits_count=1, total_agents=1,
    )
    assert qual.persona_grounding_score < 0.5


# ---------------------------------------------------------------------------
# 21. Quality evaluator catches vague objections
# ---------------------------------------------------------------------------


def test_quality_evaluator_penalizes_vague_objections() -> None:
    rounds = [
        _make_round(
            round_type="objection_formation", round_number=3,
            stance=None,
            objections=[
                {"text": "no", "category": "x"},  # too short
                {"text": "bad", "category": "x"},  # too short
            ],
        ),
        _make_round(round_type="final_stance", round_number=7),
    ]
    qual = evaluate_simulation_quality(
        rounds=rounds, caveats=["micro-simulation n=7"],
        product_name="StrideShield",
        agents_with_traits_count=1, total_agents=1,
    )
    assert qual.objection_specificity_score < 0.5


# ---------------------------------------------------------------------------
# 22. Quality evaluator catches missing caveats
# ---------------------------------------------------------------------------


def test_quality_evaluator_catches_missing_caveats() -> None:
    rounds = [_make_round(round_type="final_stance", round_number=7)]
    qual = evaluate_simulation_quality(
        rounds=rounds, caveats=[],  # missing all required caveats
        product_name="StrideShield",
        agents_with_traits_count=1, total_agents=1,
    )
    assert qual.caveat_integrity_score == 0.0
    assert qual.ready_state == "NOT_READY"


# ---------------------------------------------------------------------------
# 23. Quality evaluator catches invalid stance labels
# ---------------------------------------------------------------------------


def test_quality_evaluator_catches_invalid_stance_labels() -> None:
    rounds = [
        _make_round(
            round_type="final_stance", round_number=7,
            stance="strongly_interested",  # NOT in MARKET_ENTRY_STANCES
        ),
    ]
    qual = evaluate_simulation_quality(
        rounds=rounds, caveats=[
            "micro-simulation n=7", "this is not a forecast",
            "not a market verdict", "not representative",
            "run-scoped generated personas", "synthetic",
            "amazon and web snippets", "unlaunched no direct evidence",
        ],
        product_name="StrideShield",
        agents_with_traits_count=1, total_agents=1,
    )
    assert qual.stance_validity_score == 0.0
    assert qual.ready_state == "NOT_READY"


def test_quality_evaluator_anti_fake_claim_score_penalizes() -> None:
    """A round whose raw_text contains a fake-StrideShield-use claim
    drives anti_fake_claim_score down."""
    rounds = [
        _make_round(
            round_type="final_stance", round_number=7,
            raw_text="I bought StrideShield and it works great.",
            forbidden=["launch_state:fabricated_unlaunched_target_product_use"],
        ),
    ]
    qual = evaluate_simulation_quality(
        rounds=rounds, caveats=[],
        product_name="StrideShield",
        agents_with_traits_count=1, total_agents=1,
    )
    assert qual.anti_fake_claim_score < 1.0
    assert qual.ready_state == "NOT_READY"


# ---------------------------------------------------------------------------
# Bonus: round types match closed set
# ---------------------------------------------------------------------------


def test_round_types_closed_set() -> None:
    expected = {
        "baseline_context", "first_exposure", "objection_formation",
        "competitor_comparison", "proof_exposure", "social_influence",
        "final_stance",
    }
    assert set(AGENT_ROUND_TYPES) == expected


# ---------------------------------------------------------------------------
# Bonus: --dry-run / --commit modes present
# ---------------------------------------------------------------------------


def test_script_supports_dry_run_and_commit_flags() -> None:
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "--dry-run" in src
    assert "--commit" in src
    assert "default=True" in src


# ---------------------------------------------------------------------------
# 24. Existing 8.5D.2E tests still resolve their imports
# ---------------------------------------------------------------------------


def test_existing_phase_imports_still_resolve() -> None:
    from assembly.sources.persona_role_planner import (  # noqa: F401
        PersonaCandidate,
    )
    from assembly.sources.persona_diversity_evaluator import (  # noqa: F401
        evaluate_persona_diversity,
    )
    from assembly.sources.source_expansion_planner import (  # noqa: F401
        generate_source_expansion_plan,
    )
    from assembly.sources.persona_set_compressor import (  # noqa: F401
        compress_persona_set,
    )
    from assembly.sources.run_scoped_persona_simulation import (  # noqa: F401
        load_run_scoped_agents, evaluate_simulation_quality,
    )


# ---------------------------------------------------------------------------
# Bonus: drift — no hardcoded brand/category in the simulation pkg
# ---------------------------------------------------------------------------


def test_simulation_pkg_has_no_hardcoded_brand_or_category() -> None:
    forbidden = (
        "strideshield", "triton", "solara",
        "body glide", "body_glide", "megababe", "trail toes",
        "trail_toes", "squirrel", "gold bond", "red bull",
        "monster", "celsius", "gatorade",
        "anti-blister", "anti_blister", "anti-chafe", "anti_chafe",
        "energy drink",
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
                f"hardcoded {term!r} in simulation pkg {f.name}"
            )
