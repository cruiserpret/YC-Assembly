"""Phase 8.2A — pure-Python persona-validator tests. No DB."""
from __future__ import annotations

from uuid import uuid4

import pytest

from assembly.pipeline.persona.validator import (
    validate_persona_record_safe_for_user,
    validate_persona_trait_payload,
    validate_population_audit_payload,
)


# ---------------------------------------------------------------------------
# direct
# ---------------------------------------------------------------------------


def test_direct_valid_payload_passes() -> None:
    sid = uuid4()
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "direct",
            "value": "moderate; cited price as a primary concern",
            "source_ids": [str(sid)],
            "confidence": 0.85,
        }
    )
    assert r.passed, r.violations


def test_direct_without_source_ids_fails() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "direct",
            "value": "moderate",
            "source_ids": [],
            "confidence": 0.9,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.support_requires_source_ids" for v in r.violations
    )


def test_direct_with_zero_confidence_fails() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "direct",
            "value": "moderate",
            "source_ids": [str(uuid4())],
            "confidence": 0.0,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.direct_requires_confidence_gt_0"
        for v in r.violations
    )


# ---------------------------------------------------------------------------
# inferred
# ---------------------------------------------------------------------------


def test_inferred_above_threshold_passes() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "current_alternatives",
            "support_level": "inferred",
            "value": "competing reusable bottle category",
            "source_ids": [str(uuid4())],
            "confidence": 0.6,
        }
    )
    assert r.passed, r.violations


def test_inferred_below_confidence_threshold_fails() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "current_alternatives",
            "support_level": "inferred",
            "value": "weakly suggested alternative",
            "source_ids": [str(uuid4())],
            "confidence": 0.30,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.inferred_confidence_too_low" for v in r.violations
    )


# ---------------------------------------------------------------------------
# unknown / missing
# ---------------------------------------------------------------------------


def test_unknown_with_value_fails() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "unknown",
            "value": "must not have a value",
            "source_ids": [],
            "confidence": 0.0,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.unknown_forbids_value" for v in r.violations
    )


def test_unknown_with_source_ids_fails() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "unknown",
            "value": None,
            "source_ids": [str(uuid4())],
            "confidence": 0.0,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.unknown_forbids_source_ids" for v in r.violations
    )


def test_unknown_payload_without_value_passes() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "unknown",
            "value": None,
            "source_ids": [],
            "confidence": 0.0,
        }
    )
    assert r.passed, r.violations


def test_missing_with_value_fails() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "missing",
            "value": "some value",
            "source_ids": [],
            "confidence": 0.0,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.missing_forbids_value" for v in r.violations
    )


# ---------------------------------------------------------------------------
# closed enums
# ---------------------------------------------------------------------------


def test_arbitrary_field_name_rejected() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "favorite_color",
            "support_level": "unknown",
            "value": None,
            "source_ids": [],
            "confidence": 0.0,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.field_name_unknown" for v in r.violations
    )


def test_unknown_support_level_rejected() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "definitely",
            "value": None,
            "source_ids": [],
            "confidence": 0.0,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.support_level_unknown" for v in r.violations
    )


# ---------------------------------------------------------------------------
# geography precision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "123 Main St, Berkeley CA",
        "94110",
        "Apt 4B Brooklyn",
        "1234 Sunset Boulevard suite 500",
    ],
)
def test_geography_too_precise_rejected(value: str) -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "geography_broad",
            "support_level": "direct",
            "value": value,
            "source_ids": [str(uuid4())],
            "confidence": 0.9,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.geography_too_precise" for v in r.violations
    )


def test_geography_broad_region_allowed() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "geography_broad",
            "support_level": "direct",
            "value": "us_california",
            "source_ids": [str(uuid4())],
            "confidence": 0.9,
        }
    )
    assert r.passed, r.violations


# ---------------------------------------------------------------------------
# influence_signals must be source-backed (not inferred)
# ---------------------------------------------------------------------------


def test_influence_signals_inferred_rejected() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "influence_signals",
            "support_level": "inferred",
            "value": "seemed influential",
            "source_ids": [str(uuid4())],
            "confidence": 0.7,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.trait.field_must_be_source_backed" for v in r.violations
    )


def test_influence_signals_direct_allowed() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "influence_signals",
            "support_level": "direct",
            "value": "post received 240 upvotes; 42 replies",
            "source_ids": [str(uuid4())],
            "confidence": 0.95,
        }
    )
    assert r.passed, r.violations


# ---------------------------------------------------------------------------
# Sensitive attribute screening on value/rationale
# ---------------------------------------------------------------------------


def test_value_with_email_rejected() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "interests",
            "support_level": "direct",
            "value": "contact at jane@example.com",
            "source_ids": [str(uuid4())],
            "confidence": 0.8,
        }
    )
    assert not r.passed
    assert any(
        v.rule_id.startswith("persona.trait.sensitive.contact_email")
        for v in r.violations
    )


def test_value_with_health_attribute_rejected() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "interests",
            "support_level": "direct",
            "value": "adopted product after diabetes diagnosis",
            "source_ids": [str(uuid4())],
            "confidence": 0.9,
        }
    )
    assert not r.passed
    assert any(v.rule_id.startswith("persona.trait.sensitive.health") for v in r.violations)


# ---------------------------------------------------------------------------
# Extra keys / shape
# ---------------------------------------------------------------------------


def test_extra_keys_rejected() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "unknown",
            "value": None,
            "source_ids": [],
            "confidence": 0.0,
            "secret_metadata": "not allowed",
        }
    )
    assert not r.passed
    assert any(v.rule_id == "persona.trait.extra_keys" for v in r.violations)


def test_violations_carry_rule_id_and_suggestion() -> None:
    r = validate_persona_trait_payload(
        {
            "field_name": "price_sensitivity",
            "support_level": "direct",
            "value": None,  # missing value
            "source_ids": [],
            "confidence": 0.9,
        }
    )
    assert not r.passed
    for v in r.violations:
        assert v.rule_id
        assert v.suggestion


# ---------------------------------------------------------------------------
# user-facing safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden_key", [
    "real_name", "email", "phone", "raw_handle", "username",
    "address", "zip_code", "photo_url", "user_handle_hash",
])
def test_user_facing_payload_rejects_real_identity_keys(forbidden_key: str) -> None:
    r = validate_persona_record_safe_for_user(
        {"display_name": "Avery T.", forbidden_key: "leaked"}
    )
    assert not r.passed
    assert any(
        v.rule_id == "persona.user_facing.forbidden_key" for v in r.violations
    )


def test_user_facing_payload_clean_passes() -> None:
    r = validate_persona_record_safe_for_user(
        {
            "display_name": "Avery T.",
            "segment_label": "test_segment",
            "interests": "commerce platforms; plugin sprawl",
        }
    )
    assert r.passed, r.violations


# ---------------------------------------------------------------------------
# population audit payload
# ---------------------------------------------------------------------------


def test_audit_payload_valid() -> None:
    r = validate_population_audit_payload(
        {
            "requested_society": {"target_market": "us_test"},
            "retrieved_persona_count": 100,
            "final_persona_count": 80,
            "cluster_count": 4,
            "geography_coverage_label": "moderate",
            "society_strength_label": "moderate",
        }
    )
    assert r.passed, r.violations


def test_audit_payload_missing_required_keys_fails() -> None:
    r = validate_population_audit_payload({"requested_society": {}})
    assert not r.passed
    rule_ids = {v.rule_id for v in r.violations}
    assert "audit.required_key_missing" in rule_ids


def test_audit_payload_invalid_geography_label_fails() -> None:
    r = validate_population_audit_payload(
        {
            "requested_society": {"target_market": "us_test"},
            "retrieved_persona_count": 1,
            "final_persona_count": 1,
            "cluster_count": 1,
            "geography_coverage_label": "amazing",  # not in closed set
            "society_strength_label": "moderate",
        }
    )
    assert not r.passed
    assert any(v.rule_id == "audit.label_invalid" for v in r.violations)


def test_audit_payload_negative_count_fails() -> None:
    r = validate_population_audit_payload(
        {
            "requested_society": {"target_market": "us_test"},
            "retrieved_persona_count": -1,
            "final_persona_count": 0,
            "cluster_count": 0,
            "geography_coverage_label": "thin",
            "society_strength_label": "thin",
        }
    )
    assert not r.passed
    assert any(v.rule_id == "audit.count_negative_or_non_int" for v in r.violations)
