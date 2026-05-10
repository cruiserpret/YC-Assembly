"""Phase 8.2A — PopulationConstructionAudit Pydantic builder/validator tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from assembly.pipeline.persona.audit import (
    PopulationConstructionAuditPayload,
    build_population_construction_audit_payload,
    render_audit_summary,
    validate_population_construction_audit_payload,
)


def _good_payload(**overrides):
    base = dict(
        requested_society={"target_market": "us_california"},
        retrieved_persona_count=20060,
        final_persona_count=18900,
        cluster_count=14,
        source_kind_counts={"trustpilot_review": 8421, "reddit_public_thread": 5117},
        direct_trait_count=10500,
        inferred_trait_count=4200,
        unknown_trait_count=900,
        missing_trait_count=120,
        trait_support_breakdown={
            "price_sensitivity": {"direct_pct": 0.61, "inferred_pct": 0.31, "unknown_pct": 0.08},
        },
        geography_coverage_label="moderate",
        geography_coverage_notes="39% direct California signal; 35% inferred regional support",
        source_freshness_label="moderate",
        representativeness_caveats=[
            "Premium-bottled-water buyers over-represented vs baseline.",
        ],
        missing_evidence_warnings=[
            "No retail-pricing reviews from convenience stores.",
        ],
        compliance_status={"trustpilot_review": "ok", "reddit_public_thread": "ok"},
        society_strength_label="moderate",
        society_strength_explanation="Strong on grocery + sustainability; thin on convenience.",
    )
    base.update(overrides)
    return base


def test_audit_payload_validates() -> None:
    payload = build_population_construction_audit_payload(**_good_payload())
    assert isinstance(payload, PopulationConstructionAuditPayload)
    assert payload.geography_coverage_label == "moderate"


def test_audit_invalid_geography_coverage_label_fails() -> None:
    with pytest.raises(ValidationError):
        build_population_construction_audit_payload(
            **_good_payload(geography_coverage_label="not_a_real_label")
        )


def test_audit_invalid_society_strength_label_fails() -> None:
    with pytest.raises(ValidationError):
        build_population_construction_audit_payload(
            **_good_payload(society_strength_label="amazing")
        )


def test_audit_negative_count_fails() -> None:
    with pytest.raises(ValidationError):
        build_population_construction_audit_payload(
            **_good_payload(final_persona_count=-1)
        )


def test_audit_missing_required_field_fails() -> None:
    bad = _good_payload()
    del bad["geography_coverage_label"]
    with pytest.raises(ValidationError):
        build_population_construction_audit_payload(**bad)


def test_audit_extra_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        build_population_construction_audit_payload(
            **_good_payload(secret_key="leaked")
        )


def test_audit_validate_dict_returns_violations_for_invalid_label() -> None:
    payload = _good_payload(geography_coverage_label="amazing")
    r = validate_population_construction_audit_payload(payload)
    assert not r.passed
    assert any(v.rule_id == "audit.label_invalid" for v in r.violations)


def test_audit_renders_human_readable_summary() -> None:
    payload = build_population_construction_audit_payload(**_good_payload())
    summary = render_audit_summary(payload)
    assert "anonymous source-grounded persona node" in summary
    assert "Society strength: moderate" in summary
    assert "Geography: moderate" in summary


def test_audit_caveats_must_be_list_of_strings() -> None:
    payload = _good_payload()
    payload["representativeness_caveats"] = "should be a list"  # type: ignore[assignment]
    r = validate_population_construction_audit_payload(payload)
    assert not r.passed
    assert any(v.rule_id == "audit.list_not_strings" for v in r.violations)


def test_audit_optional_source_freshness_label_can_be_null() -> None:
    payload = build_population_construction_audit_payload(
        **_good_payload(source_freshness_label=None)
    )
    assert payload.source_freshness_label is None
