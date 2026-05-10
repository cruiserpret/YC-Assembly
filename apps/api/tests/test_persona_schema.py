"""Phase 8.2A — schema-level tests against real Postgres.

These tests prove the migration + ORM + DB CHECK constraints make it
impossible to:
  - store unsupported persona trait field names
  - store direct/inferred traits with no source_ids
  - store unknown/missing traits with a value
  - create self-loops in the persona graph
  - violate UNIQUE constraints
  - persist a persona with real-identity columns (there are none)

A persistent integration test, run with `pytest -m integration`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink,
    PersonaGraphEdge,
    PersonaRecord,
    PersonaTrait,
    PopulationConstructionAudit,
    SourceRecord,
)
from assembly.models.simulation import Simulation


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


def _new_source(**overrides) -> SourceRecord:
    base = dict(
        id=uuid4(),
        source_kind="manual_seed_test",
        source_url=None,
        captured_at=datetime.now(UTC),
        content="seeded test content for schema tests; no real identity here",
        content_hash="x" * 64,
        language="en",
        metadata_={},
        ingested_by="schema_test",
        compliance_tag="manual_seed",
        user_handle_hash=None,
    )
    base.update(overrides)
    return SourceRecord(**base)


def _new_persona(**overrides) -> PersonaRecord:
    base = dict(
        id=uuid4(),
        display_name="Avery T.",
        segment_label="test_segment",
        origin_market_broad="us_test",
        product_relevance_tags=["test"],
        influence_score=Decimal("0.50"),
        susceptibility=Decimal("0.40"),
        population_weight=Decimal("1.0"),
        source_strength_score=Decimal("0.60"),
        refreshed_at=datetime.now(UTC),
    )
    base.update(overrides)
    return PersonaRecord(**base)


# ---------------------------------------------------------------------------
# source_records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_records_unique_kind_and_content_hash() -> None:
    sessionmaker = get_sessionmaker()
    # Unique-per-run hash so re-running the test against the same DB
    # doesn't collide with a leftover row from a prior pass.
    unique_hash = "dup-" + uuid4().hex
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_source(content_hash=unique_hash))
    # Second insert with same (kind, content_hash) → IntegrityError
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_source(content_hash=unique_hash))
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_source_records_compliance_tag_must_be_in_closed_set() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_source(compliance_tag="not_a_real_tag"))
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# persona_records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persona_record_can_be_created_without_real_identity() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_new_persona())
    # And no real-identity columns on the table — only display_name.
    table_cols = {c.name for c in PersonaRecord.__table__.columns}
    forbidden = {
        "raw_handle", "handle", "username", "email", "phone",
        "real_name", "first_name", "last_name", "address", "photo",
        "photo_url", "avatar_url", "profile_url", "ssn", "dob",
    }
    assert table_cols.isdisjoint(forbidden), (
        f"persona_records must not have real-identity columns; "
        f"unexpected: {sorted(table_cols & forbidden)}"
    )


# ---------------------------------------------------------------------------
# persona_traits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persona_trait_direct_requires_source_ids() -> None:
    sessionmaker = get_sessionmaker()
    persona = _new_persona()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(persona)
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaTrait(
                    id=uuid4(),
                    persona_id=persona.id,
                    field_name="price_sensitivity",
                    value="cautious",
                    support_level="direct",
                    source_ids=[],  # ← violates CHECK
                    confidence=Decimal("0.9"),
                    last_updated_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_persona_trait_unknown_forbids_value() -> None:
    sessionmaker = get_sessionmaker()
    persona = _new_persona()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(persona)
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaTrait(
                    id=uuid4(),
                    persona_id=persona.id,
                    field_name="price_sensitivity",
                    value="some value",   # ← violates CHECK for support_level=unknown
                    support_level="unknown",
                    source_ids=[],
                    confidence=Decimal("0"),
                    last_updated_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_persona_trait_missing_forbids_value() -> None:
    sessionmaker = get_sessionmaker()
    persona = _new_persona()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(persona)
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaTrait(
                    id=uuid4(),
                    persona_id=persona.id,
                    field_name="price_sensitivity",
                    value="some value",  # ← violates CHECK for support_level=missing
                    support_level="missing",
                    source_ids=[],
                    confidence=Decimal("0"),
                    last_updated_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_persona_trait_rejects_arbitrary_field_name() -> None:
    sessionmaker = get_sessionmaker()
    persona = _new_persona()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(persona)
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaTrait(
                    id=uuid4(),
                    persona_id=persona.id,
                    field_name="favorite_color",  # ← not in allowed set
                    value="blue",
                    support_level="unknown",
                    source_ids=[],
                    confidence=Decimal("0"),
                    last_updated_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_persona_trait_uniqueness_per_persona_and_field() -> None:
    sessionmaker = get_sessionmaker()
    persona = _new_persona()
    source = _new_source(content_hash=uuid4().hex, source_kind="trait_test")
    async with sessionmaker() as session:
        async with session.begin():
            session.add(persona)
            session.add(source)
            session.add(
                PersonaTrait(
                    id=uuid4(),
                    persona_id=persona.id,
                    field_name="price_sensitivity",
                    value="moderate",
                    support_level="direct",
                    source_ids=[source.id],
                    confidence=Decimal("0.8"),
                    last_updated_at=datetime.now(UTC),
                )
            )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaTrait(
                    id=uuid4(),
                    persona_id=persona.id,
                    field_name="price_sensitivity",
                    value="duplicate",
                    support_level="direct",
                    source_ids=[source.id],
                    confidence=Decimal("0.7"),
                    last_updated_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# persona_graph_edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persona_graph_edges_rejects_self_loop() -> None:
    sessionmaker = get_sessionmaker()
    persona = _new_persona()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(persona)
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaGraphEdge(
                    id=uuid4(),
                    source_persona_id=persona.id,
                    target_persona_id=persona.id,  # self-loop
                    edge_type="similar_to",
                    strength=Decimal("0.9"),
                    basis="embedding_cosine",
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_persona_graph_edges_unique_per_source_target_type() -> None:
    sessionmaker = get_sessionmaker()
    p1 = _new_persona()
    p2 = _new_persona()
    async with sessionmaker() as session:
        async with session.begin():
            session.add_all([p1, p2])
            await session.flush()  # FK targets must be visible before child insert
            session.add(
                PersonaGraphEdge(
                    id=uuid4(),
                    source_persona_id=p1.id,
                    target_persona_id=p2.id,
                    edge_type="similar_to",
                    strength=Decimal("0.8"),
                    basis="embedding_cosine",
                )
            )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaGraphEdge(
                    id=uuid4(),
                    source_persona_id=p1.id,
                    target_persona_id=p2.id,
                    edge_type="similar_to",  # duplicate
                    strength=Decimal("0.7"),
                    basis="embedding_cosine",
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# population_construction_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_population_audit_row_can_be_created() -> None:
    sessionmaker = get_sessionmaker()
    # Audit row needs a real simulation row; the simplest is to create one.
    sim_id = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                Simulation(
                    id=sim_id,
                    status="simulation_completed",
                    progress={"stage": "simulation_completed"},
                )
            )
            await session.flush()  # parent simulation must exist before audit FK
            session.add(
                PopulationConstructionAudit(
                    id=uuid4(),
                    simulation_id=sim_id,
                    requested_society={"target_market": "us_test"},
                    retrieved_persona_count=100,
                    final_persona_count=80,
                    cluster_count=4,
                    source_kind_counts={"trustpilot_review": 80},
                    direct_trait_count=120,
                    inferred_trait_count=60,
                    unknown_trait_count=20,
                    missing_trait_count=10,
                    trait_support_breakdown={},
                    geography_coverage_label="moderate",
                    society_strength_label="moderate",
                    representativeness_caveats=["test caveat"],
                    missing_evidence_warnings=["test warning"],
                    compliance_status={"manual_seed": "ok"},
                )
            )
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(PopulationConstructionAudit).where(
                    PopulationConstructionAudit.simulation_id == sim_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].geography_coverage_label == "moderate"
