"""Phase 8.2D — schema-level tests against real Postgres.

Asserts that the migration + ORM + DB CHECK constraints make it
impossible to:
  - register a research_source with an unknown source_type
  - create a behavioral_mechanism with an unknown category / status / out-of-range strength
  - create a mechanism_evidence_link with an unknown support_type
  - create a belief_network_rule with allowed_inference_strength='strong'
  - create a belief_network_rule with topic_a == topic_b
  - violate UNIQUE (mechanism_id, domain_label) on applicability rules

A persistent integration test, run with `pytest -m integration`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from assembly.db import get_sessionmaker
from assembly.models.behavioral_mechanism import (
    BehavioralMechanism,
    BeliefNetworkRule,
    MechanismApplicabilityRule,
    MechanismEvidenceLink,
    MechanismInitializationAudit,
    PersuasionStrategyTaxonomy,
    ResearchSource,
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


def _new_source(**overrides) -> ResearchSource:
    base = dict(
        id=uuid4(),
        title=f"test paper {uuid4().hex[:6]}",
        authors="anon",
        year=2024,
        source_type="uploaded_paper",
        citation=None,
        notes=None,
    )
    base.update(overrides)
    return ResearchSource(**base)


def _new_mechanism(**overrides) -> BehavioralMechanism:
    base = dict(
        id=uuid4(),
        name=f"mech_{uuid4().hex[:8]}",
        category="persuasion",
        description="d",
        when_to_apply="d",
        when_not_to_apply="d",
        default_strength=Decimal("0.5"),
        status="active",
    )
    base.update(overrides)
    return BehavioralMechanism(**base)


# ---------------------------------------------------------------------------
# research_sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_source_unknown_source_type_rejected() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_source(source_type="not_a_real_type"))
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_research_source_year_out_of_range_rejected() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_source(year=1800))
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# behavioral_mechanisms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mechanism_unknown_category_rejected() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_mechanism(category="vibes"))
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_mechanism_unknown_status_rejected() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_mechanism(status="retired"))
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_mechanism_strength_out_of_range_rejected() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_mechanism(default_strength=Decimal("1.5")))
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# mechanism_evidence_links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_link_unknown_support_type_rejected() -> None:
    sessionmaker = get_sessionmaker()
    src = _new_source()
    mech = _new_mechanism()
    async with sessionmaker() as session:
        async with session.begin():
            session.add_all([src, mech])
            await session.flush()
            session.add(
                MechanismEvidenceLink(
                    id=uuid4(),
                    mechanism_id=mech.id,
                    research_source_id=src.id,
                    support_type="vibes",
                    excerpt_or_summary="x",
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# belief_network_rules — the centerpiece
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_belief_rule_strength_strong_is_rejected_at_db() -> None:
    """Source evidence ALWAYS outranks mechanism priors. The DB CHECK
    enforces this by structurally rejecting `allowed_inference_strength
    ='strong'`. This test fails the moment the invariant is loosened."""
    sessionmaker = get_sessionmaker()
    src = _new_source()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)
            await session.flush()
            session.add(
                BeliefNetworkRule(
                    id=uuid4(),
                    topic_a="a",
                    topic_b="b",
                    relation_type="same_cluster",
                    allowed_inference_strength="strong",
                    research_source_id=src.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_belief_rule_self_pair_is_rejected_at_db() -> None:
    sessionmaker = get_sessionmaker()
    src = _new_source()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)
            await session.flush()
            session.add(
                BeliefNetworkRule(
                    id=uuid4(),
                    topic_a="same",
                    topic_b="same",
                    relation_type="same_cluster",
                    allowed_inference_strength="moderate",
                    research_source_id=src.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_belief_rule_unknown_relation_type_is_rejected_at_db() -> None:
    sessionmaker = get_sessionmaker()
    src = _new_source()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)
            await session.flush()
            session.add(
                BeliefNetworkRule(
                    id=uuid4(),
                    topic_a="a",
                    topic_b="b",
                    relation_type="fated",
                    allowed_inference_strength="moderate",
                    research_source_id=src.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# applicability rules — uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_applicability_rule_unique_per_mechanism_domain() -> None:
    sessionmaker = get_sessionmaker()
    mech = _new_mechanism()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(mech)
            await session.flush()
            session.add(
                MechanismApplicabilityRule(
                    id=uuid4(),
                    mechanism_id=mech.id,
                    domain_label="commerce",
                    applies_when={"requires": []},
                )
            )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                MechanismApplicabilityRule(
                    id=uuid4(),
                    mechanism_id=mech.id,
                    domain_label="commerce",
                    applies_when={"requires": ["communication_style"]},
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# strategy taxonomy — uniqueness on strategy_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persuasion_strategy_unique_strategy_name() -> None:
    sessionmaker = get_sessionmaker()
    src = _new_source()
    name = f"strat_{uuid4().hex[:8]}"
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)
            await session.flush()
            session.add(
                PersuasionStrategyTaxonomy(
                    id=uuid4(),
                    strategy_name=name,
                    description="d",
                    research_source_id=src.id,
                )
            )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersuasionStrategyTaxonomy(
                    id=uuid4(),
                    strategy_name=name,
                    description="d2",
                    research_source_id=src.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# audit table — minimum columns can be persisted with empty arrays
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mechanism_initialization_audit_can_be_created_minimal() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                MechanismInitializationAudit(
                    id=uuid4(),
                    persona_id=None,
                    simulation_id=None,
                    applied_mechanisms=[],
                    skipped_mechanisms=[],
                    applied_belief_rules=[],
                    anti_pattern_warnings=[],
                    evidence_outranked_priors=False,
                    notes="minimal smoke test",
                )
            )


# ---------------------------------------------------------------------------
# defensive: persona-table FK from audit row works (CASCADE on delete)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mechanism_initialization_audit_records_iso_timestamp() -> None:
    """Smoke check that `created_at` is automatically populated."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            row = MechanismInitializationAudit(
                id=uuid4(),
                applied_mechanisms=[],
                skipped_mechanisms=[],
                applied_belief_rules=[],
                anti_pattern_warnings=[],
                evidence_outranked_priors=False,
            )
            session.add(row)
            await session.flush()
            inserted_id = row.id

    async with sessionmaker() as session:
        from sqlalchemy import select
        rows = (
            await session.execute(
                select(MechanismInitializationAudit).where(
                    MechanismInitializationAudit.id == inserted_id
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].created_at is not None
        assert isinstance(rows[0].created_at, datetime)
