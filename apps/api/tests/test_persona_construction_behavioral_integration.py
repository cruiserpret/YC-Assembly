"""Phase 8.2F — behavioral mechanism integration tests.

The persona construction worker accepts an optional
`mechanism_audit_writer` callback. The 8.2D mechanism library is the
SOLE place that may write `mechanism_initialization_audit` rows; the
worker never does so directly. These tests verify the boundary:

  - mechanism hints apply only when the required source-backed trait is
    present (validated by Phase 8.2D's applicability rules)
  - source evidence outranks mechanism priors (validator enforces)
  - belief-network rules return hints only — no direct opinions
  - demographic-only domain refuses initialization
  - mechanism audit row is written via 8.2D's audit module, not by
    the persona-construction worker
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest

from assembly.db import get_sessionmaker
from assembly.models.behavioral_mechanism import MechanismInitializationAudit
from assembly.pipeline.behavioral_science.audit import (
    write_mechanism_initialization_audit,
)
from assembly.pipeline.behavioral_science.constants import (
    ANTI_PATTERN_DEMOGRAPHIC_ONLY,
    ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE,
)
from assembly.pipeline.behavioral_science.initializer import (
    PersonaTraitInput,
    build_persona_mechanism_profile,
)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test() -> AsyncIterator[None]:
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:  # pragma: no cover
            pass
    db._engine = None
    db._sessionmaker = None


class _FakeMech:
    def __init__(self, name, category, default_strength):
        self.id = uuid4()
        self.name = name
        self.category = category
        self.default_strength = Decimal(str(default_strength))


class _FakeAppRule:
    def __init__(self, mechanism_id, domain_label, applies_when, notes=None):
        self.id = uuid4()
        self.mechanism_id = mechanism_id
        self.domain_label = domain_label
        self.applies_when = applies_when
        self.notes = notes


class _FakeBeliefRule:
    def __init__(self, topic_a, topic_b, relation_type, strength):
        self.id = uuid4()
        self.topic_a = topic_a
        self.topic_b = topic_b
        self.relation_type = relation_type
        self.allowed_inference_strength = strength
        self.notes = None


# ---------------------------------------------------------------------------
# 1) Mechanism hints apply only when the required trait exists
# ---------------------------------------------------------------------------


def test_mechanism_skipped_when_required_trait_missing() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    rule = _FakeAppRule(
        mechanism_id=mech.id,
        domain_label="commerce",
        applies_when={"requires": ["communication_style"]},
    )
    # Persona has source-backed `role_or_context` but NOT
    # communication_style — mechanism is required-fields-missing.
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[
            PersonaTraitInput(
                field_name="role_or_context",
                support_level="direct",
                value="Shopify merchant",
                confidence=0.9,
            ),
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[],
    )
    assert profile.applied_mechanisms == ()
    assert any(
        s.reason_code == "REQUIRED_FIELDS_MISSING"
        for s in profile.skipped_mechanisms
    )


def test_mechanism_applied_when_required_trait_present() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    rule = _FakeAppRule(
        mechanism_id=mech.id,
        domain_label="commerce",
        applies_when={"requires": ["communication_style"]},
    )
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[
            PersonaTraitInput(
                field_name="communication_style",
                support_level="direct",
                value="analytical",
                confidence=0.9,
            ),
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[],
    )
    assert len(profile.applied_mechanisms) == 1


# ---------------------------------------------------------------------------
# 2) Source evidence outranks mechanism priors
# ---------------------------------------------------------------------------


def test_mechanism_prior_cannot_override_source_supported_field() -> None:
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[
            PersonaTraitInput(
                field_name="price_sensitivity",
                support_level="direct",
                value="high",
                confidence=0.9,
            ),
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[],
        belief_rules_for_topic=[],
        mechanism_overrides={"price_sensitivity": "low"},
    )
    assert profile.evidence_outranked_priors is True
    assert (
        ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE
        in profile.anti_pattern_warnings
    )


# ---------------------------------------------------------------------------
# 3) Belief-network rule returns hint only — never direct opinion
# ---------------------------------------------------------------------------


def test_belief_rule_only_surfaces_hint_no_direct_opinion() -> None:
    mech = _FakeMech("bounded_same_cluster_spillover", "belief_network", 0.4)
    rule = _FakeAppRule(
        mechanism_id=mech.id,
        domain_label="well_supported_topic",
        applies_when={"max_strength": "moderate"},
    )
    br = _FakeBeliefRule("a", "b", "same_cluster", "moderate")
    profile = build_persona_mechanism_profile(
        domain_label="well_supported_topic",
        persona_traits=[
            PersonaTraitInput(
                field_name="role_or_context",
                support_level="direct",
                value="DTC founder",
                confidence=0.9,
            ),
        ],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[rule],
        belief_rules_for_topic=[br],
    )
    # The belief rule is surfaced as a hint (in `applied_belief_rules`)
    # but does NOT manifest as a persona-trait override.
    assert len(profile.applied_belief_rules) == 1
    assert profile.applied_belief_rules[0].allowed_inference_strength == "moderate"
    # No mechanism prior was claimed to override any persona trait.
    assert profile.evidence_outranked_priors is False


# ---------------------------------------------------------------------------
# 4) Demographic-only domain refuses initialization
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


# ---------------------------------------------------------------------------
# 5) Mechanism audit row is written via 8.2D audit module
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_row_records_applied_and_skipped_mechanisms() -> None:
    sessionmaker = get_sessionmaker()
    mech_applied = _FakeMech("strategy_personalization", "persuasion", 0.55)
    mech_skipped = _FakeMech("evidence_linking_drives_change", "evidence_processing", 0.6)
    rule_applied = _FakeAppRule(
        mechanism_id=mech_applied.id,
        domain_label="commerce",
        applies_when={"requires": []},
        notes="commerce default",
    )
    profile = build_persona_mechanism_profile(
        domain_label="commerce",
        persona_traits=[
            PersonaTraitInput(
                field_name="role_or_context",
                support_level="direct",
                value="DTC founder",
                confidence=0.9,
            ),
        ],
        candidate_mechanisms=[mech_applied, mech_skipped],
        applicability_rules_for_domain=[rule_applied],
        belief_rules_for_topic=[],
    )
    row = await write_mechanism_initialization_audit(
        sessionmaker, profile=profile,
    )
    assert isinstance(row, MechanismInitializationAudit)
    applied_names = {m["name"] for m in row.applied_mechanisms}
    skipped_names = {m["name"] for m in row.skipped_mechanisms}
    assert "strategy_personalization" in applied_names
    assert "evidence_linking_drives_change" in skipped_names
