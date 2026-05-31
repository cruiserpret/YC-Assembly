"""Phase 15C — action-signal taxonomy + weighting tests.

Pure, deterministic: no LLM, no network, no DB. Verifies classification,
tiering, source/category-aware weighting, aggregation, missing-data behavior,
safety (no forbidden imports / no outcome leakage / no forecast emitted), and
backward-compatibility of the optional ledger extension.
"""
from __future__ import annotations

import ast
from pathlib import Path

from assembly.market_calibration import (
    ActionSignal,
    action_signal_confidence,
    aggregate_action_signals,
    classify_action_signal,
    default_signal_strength,
    evidence_tier_summary,
    has_tier1_action_evidence,
)
from assembly.validation_ledger import load_cases
from assembly.validation_ledger.schema import CaseMetadata, ValidationCase

_PKG_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "market_calibration"
)

_MARKET_BUCKETS = {
    "buyer_action_positive", "receptive", "uncertain_proof_needed",
    "skeptical_resistant",
}


# --------------------------------------------------------------------------
# Classification + tiering
# --------------------------------------------------------------------------


def test_classify_known_signal_tiers():
    assert classify_action_signal(ActionSignal(signal_type="purchase")) == 1
    assert classify_action_signal(ActionSignal(signal_type="github_fork")) == 1
    assert classify_action_signal(ActionSignal(signal_type="github_star")) == 2
    assert classify_action_signal(ActionSignal(signal_type="product_hunt_upvote")) == 2
    assert classify_action_signal(ActionSignal(signal_type="comment_sentiment")) == 3
    assert classify_action_signal(ActionSignal(signal_type="deep_agent_forecast")) == 4


def test_model_autofills_tier_for_known_types():
    assert ActionSignal(signal_type="kickstarter_pledge").tier == 1
    assert ActionSignal(signal_type="waitlist_signup").tier == 2


def test_unknown_signal_type():
    # Unknown + no explicit tier -> unclassified
    assert classify_action_signal(ActionSignal(signal_type="mystery_signal")) is None
    # Unknown + explicit tier -> respected (custom signal)
    s = ActionSignal(signal_type="custom_partner_intro", tier=2)
    assert classify_action_signal(s) == 2


# --------------------------------------------------------------------------
# Weighting (source + category aware)
# --------------------------------------------------------------------------


def test_default_strength_tier_ordering():
    s1 = default_signal_strength("purchase")
    s2 = default_signal_strength("waitlist_signup")
    s3 = default_signal_strength("comment_sentiment")
    s4 = default_signal_strength("deep_agent_forecast")
    assert s1 > s2 > s3
    assert s3 == s4  # opinion ≈ synthetic by default
    assert default_signal_strength("totally_unknown") == 0.2  # conservative


def test_github_signal_is_category_aware():
    # GitHub matters for dev/OSS, weak for consumer, neutral when unknown.
    assert default_signal_strength("github_star", "github", "developer_tools") == 0.6
    assert default_signal_strength("github_star", "github", "open_source_software") == 0.6
    assert default_signal_strength("github_star", "github", "consumer_apps") == 0.3
    assert default_signal_strength("github_star", "github", None) == 0.45
    # github_fork is Tier 1 (base 1.0)
    assert default_signal_strength("github_fork", "github", "developer_tools") == 1.0
    assert default_signal_strength("github_fork", "github", "consumer_apps") == 0.5


def test_product_hunt_upvote_discounted():
    assert default_signal_strength("product_hunt_upvote", "product_hunt") < 0.6


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def _mixed_signals() -> list[ActionSignal]:
    return [
        ActionSignal(signal_type="purchase", source_type="b2b", count=50,
                     denominator=50, direction="positive"),
        ActionSignal(signal_type="github_star", source_type="github", count=300,
                     direction="positive"),
        ActionSignal(signal_type="comment_sentiment", source_type="hacker_news",
                     direction="negative"),
    ]


def test_aggregate_basic_shape():
    agg = aggregate_action_signals(_mixed_signals(), "developer_tools")
    assert agg["n_signals"] == 3
    assert agg["highest_tier_present"] == 1
    assert agg["has_tier1"] is True
    assert agg["dominant_direction"] == "positive"  # tier-1 purchase outweighs a comment
    assert set(agg["by_tier"].keys()) == {1, 2, 3}


def test_aggregate_empty_is_graceful():
    agg = aggregate_action_signals([])
    assert agg["n_signals"] == 0
    assert agg["highest_tier_present"] is None
    assert agg["dominant_direction"] == "unknown"
    assert agg["has_tier1"] is False
    assert agg["confidence"] == "low"
    assert agg["total_strength"] == 0.0


def test_aggregate_emits_no_market_forecast():
    """Critical: the evidence layer must NOT emit a market-proportion forecast.
    No buyer/receptive/uncertain/skeptical keys may appear."""
    agg = aggregate_action_signals(_mixed_signals(), "developer_tools")
    assert _MARKET_BUCKETS.isdisjoint(set(agg.keys()))


def test_dominant_direction_negative_and_mixed():
    neg = aggregate_action_signals([
        ActionSignal(signal_type="churn", direction="negative", count=10),
    ])
    assert neg["dominant_direction"] == "negative"
    mixed = aggregate_action_signals([
        ActionSignal(signal_type="purchase", direction="positive"),
        ActionSignal(signal_type="kickstarter_pledge", direction="negative"),
    ])
    assert mixed["dominant_direction"] == "mixed"  # equal tier-1 strength both ways


def test_evidence_tier_summary_counts():
    summary = evidence_tier_summary(_mixed_signals())
    assert summary == {1: 1, 2: 1, 3: 1}


def test_has_tier1_action_evidence():
    assert has_tier1_action_evidence(_mixed_signals()) is True
    assert has_tier1_action_evidence([
        ActionSignal(signal_type="comment_sentiment"),
    ]) is False


# --------------------------------------------------------------------------
# Confidence + missing-data behavior
# --------------------------------------------------------------------------


def test_confidence_levels():
    high = [ActionSignal(signal_type="purchase", count=100, denominator=100)]
    assert action_signal_confidence(high) == "high"
    med_t1 = [ActionSignal(signal_type="purchase")]  # tier-1 but unquantified
    assert action_signal_confidence(med_t1) == "medium"
    med_t2 = [ActionSignal(signal_type="github_star", count=300)]
    assert action_signal_confidence(med_t2) == "medium"
    low = [ActionSignal(signal_type="comment_sentiment")]
    assert action_signal_confidence(low) == "low"
    assert action_signal_confidence([]) == "low"


def test_missing_count_and_denominator_handled():
    # Signals with no count/denominator still classify + aggregate (no crash).
    sigs = [ActionSignal(signal_type="install"), ActionSignal(signal_type="follow")]
    agg = aggregate_action_signals(sigs)
    assert agg["n_signals"] == 2
    assert agg["has_tier1"] is True  # install is tier-1
    assert agg["confidence"] == "medium"  # tier-1 present but unquantified


# --------------------------------------------------------------------------
# Ledger backward-compatibility (optional extension)
# --------------------------------------------------------------------------


def test_seed_cases_still_load_unchanged():
    cases = load_cases()
    assert len(cases) == 6
    for c in cases:
        assert c.action_signals == []  # default empty; old seed unaffected


def test_ledger_case_can_carry_action_signals():
    vc = ValidationCase(
        case_id="with_signals",
        metadata=CaseMetadata(
            product_name="X", source_type="github",
            product_category="developer_tools", launch_stage="launched",
            date_run="2026-05-30", validation_status="pending",
        ),
        action_signals=[ActionSignal(signal_type="github_fork", count=42)],
    )
    assert len(vc.action_signals) == 1
    assert vc.action_signals[0].tier == 1


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def _pkg_sources() -> list[Path]:
    return sorted(_PKG_DIR.glob("*.py"))


def test_no_llm_network_db_or_outcome_leakage():
    forbidden_tokens = (
        "anthropic", "openai", "httpx", "requests", "aiohttp", "sqlalchemy",
        "redis", "behavioral_mind_layer", "assembly_behavioral",
        "observed_pct", "observed_proportions",
    )
    for p in _pkg_sources():
        src = p.read_text(encoding="utf-8").lower()
        for tok in forbidden_tokens:
            assert tok.lower() not in src, f"{p.name} must not reference {tok}"


def test_package_imports_only_stdlib_pydantic_and_self():
    allowed = {"__future__", "typing", "collections", "pydantic", "assembly"}
    for p in _pkg_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            top = None
            if isinstance(node, ast.Import):
                top = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
            if top is not None:
                assert top in allowed, f"{p.name} imports unexpected module: {top}"


def test_market_calibration_does_not_import_the_ledger():
    """One-directional dependency: the ledger may use market_calibration, but
    market_calibration must NOT import the ledger (no access to observed data).
    Checks IMPORTS via AST (a comment mentioning the ledger is fine)."""
    for p in _pkg_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.Import):
                mod = node.names[0].name
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            if mod:
                assert "validation_ledger" not in mod, (
                    f"{p.name} must not import the ledger"
                )
