"""Highest-priority test in the project: the output validator must reject every
forbidden-language fixture and accept every subjective-language fixture.

If this test ever fails, the product is broken — Assembly will be allowed to
emit numeric forecasts, absolute claims, or forced verdicts.
"""
from __future__ import annotations

import pytest

from assembly.pipeline.aggregation.validator import (
    ValidationResult,
    ViolationCategory,
    validate_output,
    validate_text,
)


# ---------------------------------------------------------------------------
# 1. Acceptance: subjective language must pass cleanly
# ---------------------------------------------------------------------------

ACCEPTABLE_SUBJECTIVE = [
    "The society seemed curious but cautious.",
    "The product created interest, but not full trust yet.",
    "The strongest resistance appeared to come from fear of losing control.",
    "The market did not seem hostile, but it did not seem comfortable enough to fully accept the offer.",
    "Several skeptical agents became more open after the product clarified that merchants retain final control.",
    "The most receptive segment appeared to be Shopify merchants doing mid-range volumes.",
    "Based on available evidence, the segment most resistant was premium brand operators.",
    "A monthly subscription may feel easier to accept than aggressive revenue-share until trust is established.",
    "The product trajectory appears to be narrow early-adopter pull with a trust barrier.",
    "Merchants will need stronger proof before they trust the autonomous pricing.",  # "will need" is soft
    "The market may take time to warm up to the autonomous-operator framing.",
    "The product seems likely to require repositioning before broad acceptance.",
    "Several agents shifted from skeptical to curious after the control safeguards were clarified.",
]


@pytest.mark.parametrize("text", ACCEPTABLE_SUBJECTIVE)
def test_subjective_language_passes(text: str) -> None:
    violations = validate_text(text)
    assert not violations, (
        f"Subjective text wrongly flagged: {text!r}\n"
        f"Violations: {[(v.rule_id, v.matched_phrase) for v in violations]}"
    )


# ---------------------------------------------------------------------------
# 2. Numeric forecasts must be rejected
# ---------------------------------------------------------------------------

NUMERIC_FORECAST_CASES = [
    ("This will convert at 4.7%.", "num.percent_forecast"),
    ("Expected CTR is 3.2%.", "num.metric_acronym"),
    ("CAC will land around $45.", "num.metric_acronym"),
    ("Conversion rate of 12% is likely.", "num.percent_forecast"),
    ("The product will generate $50K in the first month.", "num.dollar_forecast"),
    ("Expected ROI is 3x.", "num.metric_acronym"),
    ("LTV looks strong.", "num.metric_acronym"),
    ("The market converts at industry-standard rates.", "num.convert_at"),
    ("AOV around $80.", "num.metric_acronym"),
]


@pytest.mark.parametrize("text,expected_rule", NUMERIC_FORECAST_CASES)
def test_numeric_forecasts_rejected(text: str, expected_rule: str) -> None:
    violations = validate_text(text)
    assert violations, f"Numeric forecast slipped through: {text!r}"
    rule_ids = {v.rule_id for v in violations}
    assert expected_rule in rule_ids, (
        f"Expected rule {expected_rule!r} to fire on {text!r}, "
        f"got rules {rule_ids}"
    )
    # The expected rule must be categorized as numeric_forecast. Other rules
    # may also fire on the same text (e.g. "will convert at 4.7%" hits
    # num.percent_forecast, num.convert_at, AND abs.will_verb — that's
    # correct behavior for compound bad text).
    matching = [v for v in violations if v.rule_id == expected_rule]
    assert all(v.category == ViolationCategory.NUMERIC_FORECAST for v in matching)


# ---------------------------------------------------------------------------
# 3. Absolute claims must be rejected
# ---------------------------------------------------------------------------

ABSOLUTE_CLAIM_CASES = [
    ("The market will reject this product.", "abs.will_verb"),
    ("Customers will accept the new pricing.", "abs.will_verb"),
    ("This will succeed in the DTC space.", "abs.will_verb"),
    ("The product will fail without changes.", "abs.will_verb"),
    ("Customers definitely want this.", "abs.definitely"),
    ("Adoption is guaranteed.", "abs.guaranteed"),
    ("This certainly is the right move.", "abs.certainly"),
    ("Merchants will buy if positioning is right.", "abs.will_verb"),
]


@pytest.mark.parametrize("text,expected_rule", ABSOLUTE_CLAIM_CASES)
def test_absolute_claims_rejected(text: str, expected_rule: str) -> None:
    violations = validate_text(text)
    rule_ids = {v.rule_id for v in violations}
    assert expected_rule in rule_ids, (
        f"Absolute claim {text!r} did not fire {expected_rule!r}; "
        f"got {rule_ids}"
    )


# ---------------------------------------------------------------------------
# 4. Forced verdicts must be rejected
# ---------------------------------------------------------------------------

FORCED_VERDICT_CASES = [
    ("We recommend building this product.", "verdict.imperative"),
    ("You should kill this idea.", "verdict.imperative"),
    ("The team must pivot now.", "verdict.imperative"),
    ("You need to revise the positioning.", "verdict.imperative"),
    ("Verdict: BUILD", "verdict.label"),
    ("Final answer: KILL", "verdict.label"),
    ("Decision: pivot", "verdict.label"),
    ("Build this product immediately.", "verdict.bare_command"),
    ("Kill the product. Move on.", "verdict.bare_command"),
    ("Pivot the company toward enterprise.", "verdict.bare_command"),
]


@pytest.mark.parametrize("text,expected_rule", FORCED_VERDICT_CASES)
def test_forced_verdicts_rejected(text: str, expected_rule: str) -> None:
    violations = validate_text(text)
    rule_ids = {v.rule_id for v in violations}
    assert expected_rule in rule_ids, (
        f"Verdict {text!r} did not fire {expected_rule!r}; got {rule_ids}"
    )


# ---------------------------------------------------------------------------
# 5. Structured output: walk nested dict/list, report field_path
# ---------------------------------------------------------------------------


def test_structured_output_walks_nested_fields() -> None:
    sections = {
        "public_opinion_sentiment": {
            "overall_interpretation": "The society seemed cautiously interested.",
            "subjective_summary": "Trust was the main hesitation.",
        },
        "recommendations": {
            "target_audience": "Mid-volume Shopify merchants.",
            "market_positioning": "Autonomous operator, not store builder.",
            "price_structure": "Monthly subscription appears safer than revenue share.",
        },
    }
    result = validate_output(sections)
    assert result.passed, [
        (v.field_path, v.matched_phrase) for v in result.violations
    ]


def test_structured_output_reports_field_paths() -> None:
    sections = {
        "public_opinion_sentiment": {
            "overall_interpretation": "The market will reject this.",
        },
        "recommendations": {
            "price_structure": "Charge $49/mo to win the segment.",
        },
        "competitor_analysis": {
            "stronger_than_competitors": [
                "We definitely beat Shopify on automation.",
            ],
        },
    }
    result = validate_output(sections)
    assert not result.passed
    paths = {v.field_path for v in result.violations}
    assert any("public_opinion_sentiment" in p for p in paths)
    assert any("recommendations.price_structure" in p for p in paths)
    assert any("competitor_analysis.stronger_than_competitors[0]" in p for p in paths)


def test_skip_paths_exempts_fields() -> None:
    """Evidence ledger may legitimately quote a competitor's real price page."""
    sections = {
        "public_opinion_sentiment": {
            "overall_interpretation": "The society seemed curious but cautious.",
        },
        "evidence_ledger": {
            "direct_evidence": ["Competitor X charges $29/mo per their pricing page."],
        },
    }
    result = validate_output(sections, skip_paths=("evidence_ledger",))
    assert result.passed, [
        (v.field_path, v.matched_phrase) for v in result.violations
    ]


def test_validation_result_serializes_to_dict() -> None:
    sections = {"a": {"b": "Build this product."}}
    result = validate_output(sections)
    blob = result.to_dict()
    assert blob["passed"] is False
    assert isinstance(blob["violations"], list)
    assert blob["violations"][0]["rule_id"] == "verdict.bare_command"
    assert blob["violations"][0]["field_path"] == "a.b"


# ---------------------------------------------------------------------------
# 6. Defensive cases: must NOT false-positive on legitimate uses
# ---------------------------------------------------------------------------

# These are examples drawn from the plan's own example output language.
# Any one of them firing means the validator is too aggressive.
DEFENSIVE_CASES = [
    "The strongest resistance appeared to come from fear of losing control over brand identity.",
    "Merchants liked that the store could learn from analytics over time.",
    "The product creates interest, but the emotional tone is not full trust yet.",
    "Recommended target audience: overwhelmed Shopify merchants.",
    "The market may need stronger proof before broad acceptance.",
    "The product seems unlikely to be accepted broadly at first.",
    "A monthly subscription may feel easier to accept than aggressive revenue-share.",
]


@pytest.mark.parametrize("text", DEFENSIVE_CASES)
def test_validator_does_not_false_positive(text: str) -> None:
    violations = validate_text(text)
    assert not violations, (
        f"FALSE POSITIVE: {text!r}\n"
        f"Wrongly fired: {[(v.rule_id, v.matched_phrase) for v in violations]}"
    )


# ---------------------------------------------------------------------------
# 7. Empty / edge cases
# ---------------------------------------------------------------------------


def test_empty_text_is_clean() -> None:
    assert validate_text("") == []


def test_empty_sections_is_clean() -> None:
    result = validate_output({})
    assert result.passed
    assert result.violations == ()


def test_validation_result_is_immutable() -> None:
    result = ValidationResult(passed=True, violations=())
    with pytest.raises(Exception):  # frozen dataclass
        result.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 8. Objective-sentiment rules — must reject claims-about-the-market language
# ---------------------------------------------------------------------------

OBJECTIVE_SENTIMENT_CASES = [
    ("The market is positive on this product.", "obj.market_state"),
    ("Market sentiment is favorable.", "obj.market_state"),
    ("The market will be hostile to autonomous pricing.", "obj.market_state"),
    ("Customers want this.", "obj.customers_verb"),
    ("Customers reject autonomous pricing.", "obj.customers_verb"),
    ("Customers wanted control over branding.", "obj.customers_verb"),
    ("Customer needs simple onboarding.", "obj.customers_verb"),
    ("The audience accepts the offer.", "obj.audience_verb"),
    ("Audience rejects the framing.", "obj.audience_verb"),
    ("The product is accepted by the segment.", "obj.product_state"),
    ("The product has been rejected.", "obj.product_state"),
]


@pytest.mark.parametrize("text,expected_rule", OBJECTIVE_SENTIMENT_CASES)
def test_objective_sentiment_rejected(text: str, expected_rule: str) -> None:
    violations = validate_text(text)
    rule_ids = {v.rule_id for v in violations}
    assert expected_rule in rule_ids, (
        f"Objective sentiment {text!r} did not fire {expected_rule!r}; "
        f"got {rule_ids}"
    )
    for v in violations:
        if v.rule_id == expected_rule:
            assert v.category == ViolationCategory.OBJECTIVE_SENTIMENT


# Subjective rewrites of the same intent must pass.
SUBJECTIVE_REWRITES = [
    "The market mood seemed positive but cautious.",
    "The society appeared cautiously interested.",
    "Customers seemed to want stronger trust signals.",
    "Many agents indicated they wanted clearer brand control.",
    "The audience appeared to reject autonomous pricing on first exposure.",
    "The product seemed to be accepted by mid-volume merchants.",
    "Several agents portraying premium operators tended to reject the framing.",
]


@pytest.mark.parametrize("text", SUBJECTIVE_REWRITES)
def test_subjective_rewrites_pass(text: str) -> None:
    violations = validate_text(text)
    assert not violations, (
        f"Subjective rewrite wrongly flagged: {text!r}\n"
        f"Violations: {[(v.rule_id, v.matched_phrase) for v in violations]}"
    )


# ---------------------------------------------------------------------------
# 9. Structural evidence-ledger check
# ---------------------------------------------------------------------------


def test_require_ledger_blocks_when_missing() -> None:
    sections = {
        "public_opinion_sentiment": {
            "overall_interpretation": "The society seemed cautiously interested.",
        },
        # no evidence_ledger
    }
    result = validate_output(sections, require_ledger=True)
    assert not result.passed
    rule_ids = {v.rule_id for v in result.violations}
    assert "struct.ledger_missing" in rule_ids


def test_require_ledger_blocks_when_direct_empty() -> None:
    sections = {
        "evidence_ledger": {
            "direct_evidence": [],
            "analogical_evidence": ["AI website builders"],
            "missing_evidence": ["partner analytics"],
        },
    }
    result = validate_output(sections, require_ledger=True)
    assert not result.passed
    rule_ids = {v.rule_id for v in result.violations}
    assert "struct.no_direct_evidence" in rule_ids


def test_require_ledger_blocks_when_keys_missing() -> None:
    sections = {
        "evidence_ledger": {
            "direct_evidence": ["user-provided brief"],
            # missing analogical_evidence and missing_evidence
        },
    }
    result = validate_output(sections, require_ledger=True)
    assert not result.passed
    rule_ids = {v.rule_id for v in result.violations}
    assert "struct.ledger_missing_key" in rule_ids


def test_require_ledger_blocks_when_not_a_dict() -> None:
    sections = {"evidence_ledger": "not a dict"}
    result = validate_output(sections, require_ledger=True)
    assert not result.passed
    rule_ids = {v.rule_id for v in result.violations}
    assert "struct.ledger_invalid_type" in rule_ids


def test_complete_output_with_valid_ledger_passes() -> None:
    sections = {
        "public_opinion_sentiment": {
            "overall_interpretation": "The society seemed curious but cautious.",
            "subjective_summary": "Trust appeared to be the main hesitation.",
        },
        "recommendations": {
            "target_audience": "Mid-volume Shopify merchants.",
            "market_positioning": "Autonomous operator, not store builder.",
            "price_structure": "Monthly subscription appears safer than revenue share.",
        },
        "evidence_ledger": {
            "direct_evidence": [
                "User-provided product description",
                "Competitor pricing pages fetched at evidence cutoff",
            ],
            "analogical_evidence": ["AI website builders", "Shopify automation tools"],
            "missing_evidence": ["partner historical conversion data"],
        },
    }
    result = validate_output(sections, require_ledger=True)
    assert result.passed, [
        (v.field_path, v.matched_phrase, v.rule_id) for v in result.violations
    ]


def test_default_validate_does_not_require_ledger() -> None:
    """Per-section validation during regeneration shouldn't insist on a ledger."""
    sections = {
        "public_opinion_sentiment": {
            "overall_interpretation": "The society seemed cautious.",
        },
    }
    result = validate_output(sections)  # require_ledger defaults to False
    assert result.passed
