"""Phase 8.2D — initializer unit tests (pure, no DB)."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from assembly.pipeline.behavioral_science.constants import (
    ANTI_PATTERN_DEMOGRAPHIC_ONLY,
    ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE,
)
from assembly.pipeline.behavioral_science.initializer import (
    PersonaTraitInput,
    build_persona_mechanism_profile,
)


# ---------------------------------------------------------------------------
# Lightweight in-memory stand-ins for the ORM rows the initializer needs.
# The function only reads `.id`, `.name`, `.category`, `.default_strength`,
# `.applies_when`, `.notes`, `.topic_a`, `.topic_b`, `.relation_type`,
# `.allowed_inference_strength`. Bespoke dataclass-style objects are
# enough for unit tests; we never round-trip through the DB here.
# ---------------------------------------------------------------------------


class _FakeMech:
    def __init__(self, name: str, category: str, default_strength: float) -> None:
        self.id = uuid4()
        self.name = name
        self.category = category
        self.default_strength = Decimal(str(default_strength))


class _FakeApplicabilityRule:
    def __init__(
        self,
        mechanism_id,
        domain_label: str,
        applies_when: dict,
        notes: str | None = None,
    ) -> None:
        self.id = uuid4()
        self.mechanism_id = mechanism_id
        self.domain_label = domain_label
        self.applies_when = applies_when
        self.notes = notes


class _FakeBeliefRule:
    def __init__(
        self,
        topic_a: str,
        topic_b: str,
        relation_type: str,
        allowed_inference_strength: str,
        notes: str | None = None,
    ) -> None:
        self.id = uuid4()
        self.topic_a = topic_a
        self.topic_b = topic_b
        self.relation_type = relation_type
        self.allowed_inference_strength = allowed_inference_strength
        self.notes = notes


# ---------------------------------------------------------------------------
# Demographic-only refusal
# ---------------------------------------------------------------------------


def test_demographic_only_domain_refuses_initialization() -> None:
    mech = _FakeMech("demographic_only_roleplay_unreliable", "belief_network", 0.9)
    profile = build_persona_mechanism_profile(
        domain_label="unsupported_demographic_only",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[],
        belief_rules_for_topic=[],
    )
    assert profile.applied_mechanisms == ()
    assert ANTI_PATTERN_DEMOGRAPHIC_ONLY in profile.anti_pattern_warnings
    assert any(
        s.reason_code == "DEMOGRAPHIC_ONLY_REFUSED"
        for s in profile.skipped_mechanisms
    )


def test_demographic_only_with_explicit_optin_does_not_refuse() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    rule = _FakeApplicabilityRule(
        mechanism_id=mech.id,
        domain_label="unsupported_demographic_only",
        applies_when={},
    )
    profile = build_persona_mechanism_profile(
        domain_label="unsupported_demographic_only",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[],
        allow_demographic_only=True,
    )
    assert ANTI_PATTERN_DEMOGRAPHIC_ONLY not in profile.anti_pattern_warnings
    assert len(profile.applied_mechanisms) == 1


# ---------------------------------------------------------------------------
# Mechanism prior cannot outrank source evidence
# ---------------------------------------------------------------------------


def test_mechanism_prior_cannot_override_source_supported_field() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.5)
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[
            PersonaTraitInput(
                field_name="price_sensitivity",
                support_level="direct",
                value="cautious",
                confidence=0.9,
            )
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[],
        belief_rules_for_topic=[],
        mechanism_overrides={"price_sensitivity": "high"},
    )
    assert profile.evidence_outranked_priors is True
    assert (
        ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE
        in profile.anti_pattern_warnings
    )


def test_mechanism_prior_on_unsupported_field_does_not_warn() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.5)
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[
            PersonaTraitInput(
                field_name="price_sensitivity",
                support_level="direct",
                value="cautious",
                confidence=0.9,
            )
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[],
        belief_rules_for_topic=[],
        mechanism_overrides={"trust_triggers": "authority"},
    )
    assert profile.evidence_outranked_priors is False


# ---------------------------------------------------------------------------
# Applicability rule application
# ---------------------------------------------------------------------------


def test_mechanism_without_applicability_rule_is_skipped() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[],
        belief_rules_for_topic=[],
    )
    assert profile.applied_mechanisms == ()
    assert any(
        s.reason_code == "NO_APPLICABILITY_RULE"
        for s in profile.skipped_mechanisms
    )


def test_required_field_missing_skips_mechanism() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    rule = _FakeApplicabilityRule(
        mechanism_id=mech.id,
        domain_label="commerce",
        applies_when={"requires": ["communication_style"]},
    )
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[],
    )
    assert profile.applied_mechanisms == ()
    assert any(
        s.reason_code == "REQUIRED_FIELDS_MISSING"
        for s in profile.skipped_mechanisms
    )


def test_applicable_mechanism_is_applied_with_clamped_strength() -> None:
    mech = _FakeMech("bounded_same_cluster_spillover", "belief_network", 0.9)
    rule = _FakeApplicabilityRule(
        mechanism_id=mech.id,
        domain_label="well_supported_topic",
        applies_when={"max_strength": "moderate"},
    )
    profile = build_persona_mechanism_profile(
        domain_label="well_supported_topic",
        persona_traits=[
            PersonaTraitInput(
                field_name="communication_style",
                support_level="direct",
                value="analytical",
                confidence=0.8,
            )
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[],
    )
    assert len(profile.applied_mechanisms) == 1
    # 'moderate' clamps to 0.6.
    assert profile.applied_mechanisms[0].strength <= 0.6 + 1e-9


def test_belief_rules_are_surfaced_when_provided() -> None:
    mech = _FakeMech("evidence_linking_drives_change", "evidence_processing", 0.6)
    rule = _FakeApplicabilityRule(
        mechanism_id=mech.id,
        domain_label="commerce",
        applies_when={"requires_evidence_anchor": True},
    )
    br = _FakeBeliefRule(
        topic_a="brand_control_priorities",
        topic_b="ai_tooling_acceptance",
        relation_type="adjacent_cluster",
        allowed_inference_strength="weak",
    )
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[br],
    )
    assert len(profile.applied_belief_rules) == 1
    assert profile.applied_belief_rules[0].allowed_inference_strength == "weak"


def test_refuses_initialization_rule_skips_mechanism() -> None:
    mech = _FakeMech("demographic_only_roleplay_unreliable", "belief_network", 0.9)
    rule = _FakeApplicabilityRule(
        mechanism_id=mech.id,
        domain_label="unsupported_demographic_only",
        applies_when={"refuses_initialization": True},
        notes="we refuse",
    )
    profile = build_persona_mechanism_profile(
        domain_label="unsupported_demographic_only",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[],
        allow_demographic_only=True,
    )
    assert profile.applied_mechanisms == ()
    assert any(
        s.reason_code == "REFUSED_BY_APPLICABILITY_RULE"
        for s in profile.skipped_mechanisms
    )
