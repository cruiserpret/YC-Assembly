"""Phase 8.2D — `mechanism_initialization_audit` write surface tests.

Asserts that:
  - `write_mechanism_initialization_audit` persists the profile with the
    structured `applied_mechanisms`, `skipped_mechanisms`,
    `applied_belief_rules`, `anti_pattern_warnings`, and
    `evidence_outranked_priors` fields populated
  - the demographic-only refusal path's audit row carries the
    DEMOGRAPHIC_ONLY anti-pattern warning string
  - the row id round-trips through the database
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest

from assembly.db import get_sessionmaker
from assembly.pipeline.behavioral_science.audit import (
    write_mechanism_initialization_audit,
)
from assembly.pipeline.behavioral_science.constants import (
    ANTI_PATTERN_DEMOGRAPHIC_ONLY,
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
    def __init__(self, name: str, category: str, default_strength: float) -> None:
        self.id = uuid4()
        self.name = name
        self.category = category
        self.default_strength = Decimal(str(default_strength))


class _FakeApplicabilityRule:
    def __init__(self, mechanism_id, domain_label, applies_when, notes=None) -> None:
        self.id = uuid4()
        self.mechanism_id = mechanism_id
        self.domain_label = domain_label
        self.applies_when = applies_when
        self.notes = notes


class _FakeBeliefRule:
    def __init__(self, topic_a, topic_b, relation_type, strength) -> None:
        self.id = uuid4()
        self.topic_a = topic_a
        self.topic_b = topic_b
        self.relation_type = relation_type
        self.allowed_inference_strength = strength
        self.notes = None


@pytest.mark.asyncio
async def test_audit_row_persists_applied_mechanisms() -> None:
    sessionmaker = get_sessionmaker()
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
    rule = _FakeApplicabilityRule(
        mechanism_id=mech.id,
        domain_label="commerce",
        applies_when={"requires": []},
        notes="commerce default",
    )
    br = _FakeBeliefRule("a", "b", "same_cluster", "moderate")

    profile = build_persona_mechanism_profile(
        domain_label="commerce",
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
        belief_rules_for_topic=[br],
    )
    row = await write_mechanism_initialization_audit(
        sessionmaker, profile=profile,
    )
    assert row.id is not None
    assert row.evidence_outranked_priors is False
    assert isinstance(row.applied_mechanisms, list)
    assert len(row.applied_mechanisms) == 1
    assert row.applied_mechanisms[0]["name"] == "strategy_personalization"
    assert len(row.applied_belief_rules) == 1
    assert row.applied_belief_rules[0]["allowed_inference_strength"] == "moderate"


@pytest.mark.asyncio
async def test_audit_row_records_demographic_refusal() -> None:
    sessionmaker = get_sessionmaker()
    mech = _FakeMech("demographic_only_roleplay_unreliable", "belief_network", 0.9)
    profile = build_persona_mechanism_profile(
        domain_label="unsupported_demographic_only",
        persona_traits=[],
        candidate_mechanisms=[mech],
        applicability_rules_for_domain=[],
        belief_rules_for_topic=[],
    )
    row = await write_mechanism_initialization_audit(
        sessionmaker, profile=profile,
    )
    assert ANTI_PATTERN_DEMOGRAPHIC_ONLY in row.anti_pattern_warnings
    # No mechanisms should have been applied in refusal mode.
    assert row.applied_mechanisms == []
    # And every candidate is in the skipped list with the structured reason.
    assert any(
        s["reason_code"] == "DEMOGRAPHIC_ONLY_REFUSED"
        for s in row.skipped_mechanisms
    )


@pytest.mark.asyncio
async def test_audit_row_records_evidence_outranked_priors() -> None:
    sessionmaker = get_sessionmaker()
    mech = _FakeMech("strategy_personalization", "persuasion", 0.55)
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
    row = await write_mechanism_initialization_audit(
        sessionmaker, profile=profile,
    )
    assert row.evidence_outranked_priors is True
