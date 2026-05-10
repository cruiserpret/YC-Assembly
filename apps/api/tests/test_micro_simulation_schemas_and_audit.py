"""Phase 8.2K — schema validation + output-audit tests (pure)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from assembly.pipeline.micro_simulation import (
    MicroDebateTurn,
    MicroPersonaState,
    MicroRelevanceLabel,
    MicroRoundKind,
    MicroRoundResult,
    MicroSimulationOutputAudit,
    MicroSimulationResult,
    MicroStance,
    MicroTrace,
    audit_full_trace_and_summary,
    scan_text_for_forbidden_claims,
)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_micro_persona_state_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        MicroPersonaState(
            persona_id="x", display_name="x",
            relevance_label=MicroRelevanceLabel.RELEVANT,
            matched_category_key="k", relevance_score=27,
            supported_traits={"role_or_context": "shopify merchant"},
            extra_oops="no",  # type: ignore[call-arg]
        )


def test_micro_round_result_requires_reasoning() -> None:
    with pytest.raises(ValidationError):
        MicroRoundResult(
            persona_id="x", round_kind=MicroRoundKind.BASELINE,
            stance_before=MicroStance.CURIOUS_HESITANT,
            stance_after=MicroStance.CURIOUS_HESITANT,
            reasoning="",  # min_length=1
            llm_call_was_used=False,
            output_audit_passed=True,
        )


def test_micro_simulation_result_requires_at_least_2_caveats() -> None:
    """The schema guard: caveats list must have ≥2 entries (sample-size
    + coverage-thinness at minimum)."""
    with pytest.raises(ValidationError):
        MicroSimulationResult(
            brief_label="x",
            persona_count=1, relevant_count=1, weakly_relevant_count=0,
            mixed_relevance_pool=False,
            persona_states_initial=[], persona_states_final=[],
            trace=MicroTrace(),
            output_audit=MicroSimulationOutputAudit(
                sample_size_caveat_present=True,
                coverage_thinness_caveat_present=True,
                micro_test_label_present=True,
            ),
            dry_run=True,
            llm_call_count=0, cost_actual_usd=0.0, cost_cap_usd=1.0,
            caveats=["only one caveat"],  # < 2
            summary_text="x",
        )


def test_micro_stance_closed_enum() -> None:
    """Stance values are closed; arbitrary strings are rejected."""
    assert MicroStance("resistant") is MicroStance.RESISTANT
    with pytest.raises(ValueError):
        MicroStance("not_a_stance")


# ---------------------------------------------------------------------------
# Forbidden-language scanner — every category must trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("Amboras will succeed in this market.", "forecast/verdict language"),
    ("we should build it for the entire market", "build/kill/pivot recommendation"),
    ("about 30% of merchants would adopt this product",
     "adoption/conversion percentage claim"),
    ("the market reaction is positive overall", "market-reaction framing"),
    ("This proves tiny_ready = true.", "tiny_ready claim"),
    ("This persona is representative of the target market.",
     "representative-of-market claim"),
    ("The Amboras society thinks this is a good idea.",
     "society-as-singular framing"),
    ("Verdict: build this product immediately.", "forecast/verdict language"),
])
def test_scanner_blocks_each_forbidden_category(text: str, expected: str) -> None:
    found = scan_text_for_forbidden_claims(text)
    assert expected in found, (
        f"scanner missed {expected!r} on text {text!r}; got {found}"
    )


def test_scanner_accepts_clean_mechanical_text() -> None:
    text = (
        "MICRO-TEST persona Tatum G. shifted from curious_hesitant to "
        "skeptical in round 2. The trigger was the excerpt: "
        "'I'm fed up with paying $400/mo for plugins.'"
    )
    found = scan_text_for_forbidden_claims(text)
    assert found == [], f"scanner false-positive on clean text: {found}"


def test_scanner_blocks_will_dominate() -> None:
    found = scan_text_for_forbidden_claims(
        "This product will dominate the small business segment."
    )
    assert "forecast/verdict language" in found


# ---------------------------------------------------------------------------
# audit_full_trace_and_summary — caveat-marker discipline
# ---------------------------------------------------------------------------


def test_audit_flags_missing_sample_size_caveat() -> None:
    summary = "Plain text with no markers."
    audit = audit_full_trace_and_summary(
        trace=MicroTrace(), summary_text=summary, persona_count=2,
    )
    assert audit.sample_size_caveat_present is False
    assert audit.coverage_thinness_caveat_present is False
    assert audit.micro_test_label_present is False


def test_audit_recognizes_full_caveat_block() -> None:
    summary = (
        "MICRO-TEST result. n=2 sample size. Coverage is thin "
        "(1 of 8 stakeholder categories represented)."
    )
    audit = audit_full_trace_and_summary(
        trace=MicroTrace(), summary_text=summary, persona_count=2,
    )
    assert audit.sample_size_caveat_present is True
    assert audit.coverage_thinness_caveat_present is True
    assert audit.micro_test_label_present is True


def test_audit_collects_forbidden_claims_from_round_reasoning() -> None:
    bad_round = MicroRoundResult(
        persona_id="x", round_kind=MicroRoundKind.FINAL_STANCE,
        stance_before=MicroStance.SKEPTICAL,
        stance_after=MicroStance.SKEPTICAL,
        reasoning="The market reaction is positive overall.",
        llm_call_was_used=True,
        output_audit_passed=True,  # audit will flip via scanner
    )
    trace = MicroTrace(rounds=[bad_round])
    audit = audit_full_trace_and_summary(
        trace=trace, summary_text="MICRO-TEST n=1 thin coverage",
        persona_count=1,
    )
    assert "market-reaction framing" in audit.forbidden_claims_found
