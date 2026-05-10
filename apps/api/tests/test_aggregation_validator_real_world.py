"""Phase 7 — REAL_WORLD_INSTRUCTIONS validator coverage.

Direct, fast, no DB. Asserts that the new rule_ids fire on the canonical
forbidden phrases the Phase 7 spec called out, AND that natural subjective
language passes."""
from __future__ import annotations

import pytest

from assembly.pipeline.aggregation.validator import (
    ViolationCategory,
    validate_text,
)


@pytest.mark.parametrize(
    "phrase, rule_id",
    [
        ("Run Meta ads to test demand.", "rwi.run_ads"),
        ("Run Google ads to validate.", "rwi.run_ads"),
        ("run TikTok ads", "rwi.run_ads"),
        ("Spend $5K on Facebook ads.", "rwi.ad_spend"),
        ("spend $1,000 on traffic", "rwi.ad_spend"),
        ("Launch a landing page to test the offer.", "rwi.landing_page"),
        ("set up a landing page", "rwi.landing_page"),
        ("run a smoke-test campaign", "rwi.validation_campaign"),
        ("launch a validation campaign", "rwi.validation_campaign"),
        ("Kill the test if conversion is below 1%.", "rwi.kill_the_test"),
        ("kill the campaign", "rwi.kill_the_test"),
    ],
)
def test_real_world_instruction_phrases_blocked(phrase: str, rule_id: str) -> None:
    violations = validate_text(phrase, field_path="test")
    rule_ids = {v.rule_id for v in violations}
    assert rule_id in rule_ids, (
        f"expected {rule_id!r} to fire on {phrase!r}; got {rule_ids}"
    )
    # All should land in REAL_WORLD_INSTRUCTIONS category.
    assert any(
        v.category == ViolationCategory.REAL_WORLD_INSTRUCTIONS
        for v in violations
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "Agents portraying premium operators tended to resist.",
        "The society seemed cautiously interested.",
        "Many agents indicated that brand control was their primary concern.",
        "The strongest resistance appeared to come from agents who already had a working freelancer stack.",
        "Several agents portraying mid-volume merchants tended to lean receptive after proof exposure.",
    ],
)
def test_subjective_language_passes(phrase: str) -> None:
    violations = validate_text(phrase, field_path="test")
    real_world = [
        v for v in violations
        if v.category == ViolationCategory.REAL_WORLD_INSTRUCTIONS
    ]
    assert real_world == []


def test_buyer_state_friendly_skip_set_does_not_widen_real_world_rules() -> None:
    """Even with the buyer-state-friendly skip set (used during simulation),
    real-world-instruction rules MUST still fire. Phase 7's report context
    does not loosen these rules."""
    skip_rules = frozenset({"num.dollar_forecast", "num.metric_acronym"})
    violations = validate_text(
        "Spend $500 on Meta ads",
        field_path="test",
        skip_rules=skip_rules,
    )
    rule_ids = {v.rule_id for v in violations}
    assert "rwi.ad_spend" in rule_ids or "rwi.run_ads" in rule_ids
