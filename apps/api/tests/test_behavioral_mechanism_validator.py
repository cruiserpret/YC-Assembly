"""Phase 8.2D — pure-Python validator tests (no DB required)."""
from __future__ import annotations

import pytest

from assembly.pipeline.behavioral_science.validator import (
    validate_applicability_rule_payload,
    validate_belief_rule_payload,
    validate_evidence_link_payload,
    validate_mechanism_payload,
    validate_persuasion_strategy_payload,
    validate_priors_do_not_outrank_evidence,
    validate_research_source_payload,
)


# ---------------------------------------------------------------------------
# research_sources
# ---------------------------------------------------------------------------


def test_research_source_valid_passes() -> None:
    r = validate_research_source_payload({
        "title": "Some paper",
        "source_type": "uploaded_paper",
        "year": 2024,
    })
    assert r.passed


def test_research_source_unknown_source_type_fails() -> None:
    r = validate_research_source_payload({
        "title": "X",
        "source_type": "magazine_article",
    })
    assert not r.passed
    assert any(
        v.rule_id == "research_source.source_type_unknown" for v in r.violations
    )


def test_research_source_year_out_of_range_fails() -> None:
    r = validate_research_source_payload({
        "title": "X",
        "source_type": "preprint",
        "year": 1800,
    })
    assert not r.passed


def test_research_source_blank_title_fails() -> None:
    r = validate_research_source_payload({
        "title": "  ",
        "source_type": "internal_note",
    })
    assert not r.passed


# ---------------------------------------------------------------------------
# behavioral_mechanisms
# ---------------------------------------------------------------------------


def _good_mechanism() -> dict:
    return {
        "name": "test_mechanism",
        "category": "persuasion",
        "description": "demo",
        "when_to_apply": "demo",
        "when_not_to_apply": "demo",
        "default_strength": 0.5,
        "status": "active",
    }


def test_mechanism_valid_passes() -> None:
    r = validate_mechanism_payload(_good_mechanism())
    assert r.passed


def test_mechanism_unknown_category_fails() -> None:
    p = _good_mechanism()
    p["category"] = "vibes"
    r = validate_mechanism_payload(p)
    assert not r.passed


def test_mechanism_strength_out_of_range_fails() -> None:
    p = _good_mechanism()
    p["default_strength"] = 1.5
    r = validate_mechanism_payload(p)
    assert not r.passed


def test_mechanism_required_text_blank_fails() -> None:
    p = _good_mechanism()
    p["description"] = ""
    r = validate_mechanism_payload(p)
    assert not r.passed


def test_mechanism_unknown_status_fails() -> None:
    p = _good_mechanism()
    p["status"] = "retired"
    r = validate_mechanism_payload(p)
    assert not r.passed


# ---------------------------------------------------------------------------
# evidence_link
# ---------------------------------------------------------------------------


def test_evidence_link_unknown_support_type_fails() -> None:
    r = validate_evidence_link_payload({
        "mechanism_id": "abc",
        "research_source_id": "abc",
        "support_type": "vibes",
        "excerpt_or_summary": "x",
    })
    assert not r.passed


def test_evidence_link_missing_excerpt_fails() -> None:
    r = validate_evidence_link_payload({
        "mechanism_id": "abc",
        "research_source_id": "abc",
        "support_type": "direct_claim",
        "excerpt_or_summary": "",
    })
    assert not r.passed


# ---------------------------------------------------------------------------
# persuasion_strategy
# ---------------------------------------------------------------------------


def test_persuasion_strategy_unknown_name_fails() -> None:
    r = validate_persuasion_strategy_payload({
        "strategy_name": "the_old_razzle_dazzle",
        "description": "x",
        "research_source_id": "abc",
    })
    assert not r.passed


def test_persuasion_strategy_known_name_passes() -> None:
    r = validate_persuasion_strategy_payload({
        "strategy_name": "logical_appeal",
        "description": "x",
        "research_source_id": "abc",
    })
    assert r.passed


# ---------------------------------------------------------------------------
# belief_rule  (the centerpiece — strength != 'strong')
# ---------------------------------------------------------------------------


def test_belief_rule_strong_strength_is_rejected() -> None:
    r = validate_belief_rule_payload({
        "topic_a": "a", "topic_b": "b",
        "relation_type": "same_cluster",
        "allowed_inference_strength": "strong",
        "research_source_id": "abc",
    })
    assert not r.passed
    assert any(
        v.rule_id == "belief_rule.strength_strong_forbidden"
        for v in r.violations
    )


def test_belief_rule_self_pair_is_rejected() -> None:
    r = validate_belief_rule_payload({
        "topic_a": "a", "topic_b": "a",
        "relation_type": "same_cluster",
        "allowed_inference_strength": "moderate",
        "research_source_id": "abc",
    })
    assert not r.passed


def test_belief_rule_unknown_relation_type_is_rejected() -> None:
    r = validate_belief_rule_payload({
        "topic_a": "a", "topic_b": "b",
        "relation_type": "fated",
        "allowed_inference_strength": "moderate",
        "research_source_id": "abc",
    })
    assert not r.passed


def test_belief_rule_moderate_passes() -> None:
    r = validate_belief_rule_payload({
        "topic_a": "a", "topic_b": "b",
        "relation_type": "same_cluster",
        "allowed_inference_strength": "moderate",
        "research_source_id": "abc",
    })
    assert r.passed


# ---------------------------------------------------------------------------
# applicability_rule
# ---------------------------------------------------------------------------


def test_applicability_rule_unknown_domain_fails() -> None:
    r = validate_applicability_rule_payload({
        "mechanism_id": "abc",
        "domain_label": "marketplace",
        "applies_when": {},
    })
    assert not r.passed


def test_applicability_rule_applies_when_must_be_dict() -> None:
    r = validate_applicability_rule_payload({
        "mechanism_id": "abc",
        "domain_label": "commerce",
        "applies_when": "not-a-dict",
    })
    assert not r.passed


def test_applicability_rule_known_domain_passes() -> None:
    r = validate_applicability_rule_payload({
        "mechanism_id": "abc",
        "domain_label": "commerce",
        "applies_when": {"requires": ["communication_style"]},
    })
    assert r.passed


# ---------------------------------------------------------------------------
# Cross-cutting: priors do not outrank evidence
# ---------------------------------------------------------------------------


def test_priors_outrank_evidence_is_rejected() -> None:
    r = validate_priors_do_not_outrank_evidence(
        source_supported_fields={"price_sensitivity", "communication_style"},
        mechanism_overrides={"price_sensitivity": "high"},
    )
    assert not r.passed
    assert any(
        v.rule_id == "initializer.prior_outranks_evidence"
        for v in r.violations
    )


def test_priors_on_unsupported_field_pass() -> None:
    r = validate_priors_do_not_outrank_evidence(
        source_supported_fields={"price_sensitivity"},
        mechanism_overrides={"trust_triggers": "authority"},
    )
    assert r.passed
