"""Phase 6.75 — integration tests against real Postgres.

Asserts:
  - Migration applied (evidence_edges, claims, evidence_items columns).
  - build_evidence_graph runs end-to-end, populates rows, sets the
    idempotent flag, and is a no-op on second invocation.
  - Cutoff-date safety — captured_at IS NULL is filtered for retrieved
    web sources but allowed for user_input / missing / snapshots.
  - Claim validator structural rules.
  - Phase 7 helper service returns ranked + missing in expected shape.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from assembly.db import get_sessionmaker
from assembly.embeddings.mock import MockEmbeddingProvider
from assembly.llm.mock import MockProvider
from assembly.models.claim import Claim
from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EvidenceEdge
from assembly.models.simulation import Simulation, SimulationInput
from assembly.pipeline.aggregation.claim_validator import validate_claim
from assembly.pipeline.evidence_graph import (
    EvidenceGraphService,
    build_evidence_graph,
)
from assembly.pipeline.evidence_graph.builder import clear_graph_for_rebuild
from assembly.schemas.brief import (
    CompetitorRef,
    PriceStructure,
    SimulationBriefIn,
    TargetSociety,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_simulation(
    sessionmaker, *, brief: SimulationBriefIn, cutoff: date | None = None
) -> UUID:
    sim_id = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            sim = Simulation(
                id=sim_id, status="pending", progress={"stage": "pending"},
                evidence_cutoff_date=cutoff,
            )
            sim.input = SimulationInput(
                product_type=brief.product_type,
                product_name=brief.product_name,
                description=brief.description,
                price_structure=brief.price_structure.model_dump(),
                target_society=brief.target_society.model_dump(),
                competitors=[c.model_dump() for c in brief.competitors],
                product_url=None,
                additional_context=brief.additional_context,
                raw_brief=brief.model_dump(mode="json"),
            )
            session.add(sim)
    return sim_id


async def _seed_evidence(
    sessionmaker, *, simulation_id: UUID, rows: list[dict],
) -> list[UUID]:
    """rows: list of dicts merged into EvidenceItem fields."""
    ids: list[UUID] = []
    async with sessionmaker() as session:
        async with session.begin():
            for r in rows:
                item = EvidenceItem(
                    id=r.get("id", uuid4()),
                    simulation_id=simulation_id,
                    kind=r.get("kind", "direct"),
                    source_type=r.get("source_type", "user_input"),
                    source_url=r.get("source_url"),
                    content=r.get("content", ""),
                    captured_at=r.get("captured_at"),
                    metadata_=r.get("metadata", {}),
                    content_hash=r.get("content_hash", "x" * 32),
                )
                session.add(item)
                ids.append(item.id)
    return ids


def _basic_brief() -> SimulationBriefIn:
    return SimulationBriefIn(
        product_type="ai_commerce_platform",
        product_name="Amboras-Test",
        description=(
            "Amboras is an AI commerce platform that builds and operates "
            "Shopify stores autonomously. Founders worry about brand identity."
        ),
        price_structure=PriceStructure(model="subscription_monthly", amount="$49"),
        target_society=TargetSociety(
            description="Shopify merchants overwhelmed by plugins"
        ),
        competitors=[CompetitorRef(name="Shopify Magic")],
        additional_context="brand control concerns",
    )


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_added_evidence_graph_columns_and_tables() -> None:
    """Evidence_items has the new graph columns; evidence_edges + claims tables exist."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        # Sample one row insertion to prove the columns accept the right types.
        sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
        await _seed_evidence(
            sessionmaker, simulation_id=sim_id,
            rows=[{"content": "x" * 50, "source_type": "competitor_page"}],
        )
        item = (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.simulation_id == sim_id)
            )
        ).scalar_one()
        assert hasattr(item, "node_class")
        assert hasattr(item, "content_hash")
        assert hasattr(item, "dedup_group_id")
        assert hasattr(item, "embedded_at")


# ---------------------------------------------------------------------------
# build_evidence_graph end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_evidence_graph_classifies_and_marks_built_at() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    await _seed_evidence(
        sessionmaker,
        simulation_id=sim_id,
        rows=[
            {
                "source_type": "competitor_page",
                "source_url": "https://shopify-magic.test/",
                "content": "Shopify Magic generates product descriptions and ads.",
                "captured_at": datetime.now(UTC),
            },
            {
                "source_type": "pricing_page",
                "source_url": "https://shopify-magic.test/",
                "content": "Plus plan: Custom. Trusted by 10000 merchants.",
                "captured_at": datetime.now(UTC),
            },
            {
                "source_type": "user_input",
                "metadata": {"input_field": "competitors"},
                "content": "Shopify Magic, Conversion AI Tool",
            },
            {
                "kind": "missing",
                "source_type": "user_input",
                "metadata": {"expected_kind": "public_review"},
                "content": "missing public reviews",
            },
        ],
    )

    result = await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None,  # no LLM classifier or edges
        model=None,
        embedding_provider=MockEmbeddingProvider(),
        use_llm_classifier=False,
        use_llm_edges=False,
    )

    assert not result.skipped
    assert result.classified_count >= 3  # competitor + pricing + user_input

    # Check classifications + idempotent flag.
    async with sessionmaker() as session:
        items = (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.simulation_id == sim_id)
            )
        ).scalars().all()
        sim = await session.get(Simulation, sim_id)

    classes = {it.source_type: it.node_class for it in items}
    assert classes["competitor_page"] == "competitor"
    assert classes["pricing_page"] == "pricing"
    assert sim is not None and sim.evidence_graph_built_at is not None

    # Embeddings persisted for every non-missing eligible item.
    embedded = [it for it in items if it.embedded_at is not None]
    assert len(embedded) >= 2

    # Deterministic edge: pricing + competitor at same URL → priced_against.
    async with sessionmaker() as session:
        edges = (
            await session.execute(
                select(EvidenceEdge)
                .where(EvidenceEdge.simulation_id == sim_id)
                .where(EvidenceEdge.edge_type == "priced_against")
            )
        ).scalars().all()
    assert len(edges) >= 1


@pytest.mark.asyncio
async def test_build_evidence_graph_idempotent_resume() -> None:
    """Second invocation must be a no-op (skipped=True)."""
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[{"source_type": "competitor_page", "content": "x" * 30}],
    )
    r1 = await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None, model=None,
        embedding_provider=None,
        use_llm_classifier=False, use_llm_edges=False,
    )
    assert not r1.skipped

    r2 = await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None, model=None,
        embedding_provider=None,
        use_llm_classifier=False, use_llm_edges=False,
    )
    assert r2.skipped


@pytest.mark.asyncio
async def test_clear_graph_for_rebuild_resets_flag_and_edges() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[
            {"source_type": "competitor_page", "source_url": "https://x.test/",
             "content": "a" * 30},
            {"source_type": "competitor_page", "source_url": "https://x.test/",
             "content": "b" * 30},
        ],
    )
    await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None, model=None,
        embedding_provider=None,
        use_llm_classifier=False, use_llm_edges=False,
    )
    await clear_graph_for_rebuild(sessionmaker=sessionmaker, simulation_id=sim_id)

    async with sessionmaker() as session:
        sim = await session.get(Simulation, sim_id)
        edges_left = (
            await session.execute(
                select(EvidenceEdge).where(EvidenceEdge.simulation_id == sim_id)
            )
        ).scalars().all()
    assert sim is not None and sim.evidence_graph_built_at is None
    assert edges_left == []


# ---------------------------------------------------------------------------
# Cutoff-date (Correction 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cutoff_blocks_post_cutoff_embedding() -> None:
    sessionmaker = get_sessionmaker()
    cutoff = date(2024, 1, 1)
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief(), cutoff=cutoff)
    await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[
            {  # eligible: pre-cutoff
                "source_type": "competitor_page",
                "captured_at": datetime(2023, 12, 1, tzinfo=UTC),
                "content": "pre-cutoff competitor",
            },
            {  # NOT eligible: post-cutoff
                "source_type": "competitor_page",
                "captured_at": datetime(2024, 6, 1, tzinfo=UTC),
                "content": "post-cutoff competitor",
            },
        ],
    )
    await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None, model=None,
        embedding_provider=MockEmbeddingProvider(),
        use_llm_classifier=False, use_llm_edges=False,
    )
    async with sessionmaker() as session:
        items = (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.simulation_id == sim_id)
            )
        ).scalars().all()
    pre = [i for i in items if "pre-cutoff" in (i.content or "")]
    post = [i for i in items if "post-cutoff" in (i.content or "")]
    assert pre and pre[0].embedded_at is not None
    assert post and post[0].embedded_at is None


@pytest.mark.asyncio
async def test_cutoff_null_captured_at_blocked_for_retrieved_web_evidence() -> None:
    """Retrieved web evidence with captured_at IS NULL must be excluded
    when cutoff_date is set (Correction 3). User input + missing rows are
    still allowed."""
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(
        sessionmaker, brief=_basic_brief(), cutoff=date(2024, 1, 1)
    )
    await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[
            {  # web-fetched competitor with NULL captured_at — must be blocked
                "source_type": "competitor_page",
                "captured_at": None,
                "content": "web competitor null capture",
            },
            {  # user input — allowed
                "source_type": "user_input",
                "captured_at": None,
                "content": "user-supplied desc",
                "metadata": {"input_field": "user_description"},
            },
        ],
    )
    await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None, model=None,
        embedding_provider=MockEmbeddingProvider(),
        use_llm_classifier=False, use_llm_edges=False,
    )
    async with sessionmaker() as session:
        items = (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.simulation_id == sim_id)
            )
        ).scalars().all()
    by_type = {i.source_type: i for i in items}
    # User input embedded; web competitor with null captured_at NOT embedded.
    assert by_type["user_input"].embedded_at is not None
    assert by_type["competitor_page"].embedded_at is None


# ---------------------------------------------------------------------------
# Edge constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_loop_edge_rejected_by_check_constraint() -> None:
    from sqlalchemy.exc import IntegrityError
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    [eid] = await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[{"source_type": "competitor_page", "content": "x" * 20}],
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                EvidenceEdge(
                    simulation_id=sim_id,
                    source_evidence_id=eid,
                    target_evidence_id=eid,
                    edge_type="similar_to",
                    strength=Decimal("1.0"),
                    confidence=Decimal("1.0"),
                    basis="direct",
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()


@pytest.mark.asyncio
async def test_duplicate_edge_rejected() -> None:
    from sqlalchemy.exc import IntegrityError
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    a, b = await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[
            {"source_type": "competitor_page", "content": "a" * 20,
             "source_url": "https://a.test/"},
            {"source_type": "pricing_page", "content": "b" * 20,
             "source_url": "https://a.test/"},
        ],
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(EvidenceEdge(
                simulation_id=sim_id, source_evidence_id=a, target_evidence_id=b,
                edge_type="priced_against", strength=Decimal("1.0"),
                confidence=Decimal("1.0"), basis="direct",
            ))
    async with sessionmaker() as session:
        async with session.begin():
            session.add(EvidenceEdge(
                simulation_id=sim_id, source_evidence_id=a, target_evidence_id=b,
                edge_type="priced_against", strength=Decimal("1.0"),
                confidence=Decimal("1.0"), basis="direct",
            ))
            with pytest.raises(IntegrityError):
                await session.flush()


# ---------------------------------------------------------------------------
# Claim validator (Section 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_validator_accepts_well_bound_claim() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    [eid] = await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[{
            "kind": "direct",
            "source_type": "user_input",
            "content": "Founders worry about brand identity damage from autonomous AI.",
        }],
    )
    result = await validate_claim(
        sessionmaker=sessionmaker,
        simulation_id=sim_id,
        text="Founders worry about brand identity",
        source_evidence_id=eid,
        source_excerpt="Founders worry about brand identity",
        claim_type="observation",
        basis="direct",
    )
    assert result.passed, result.violations


@pytest.mark.asyncio
async def test_claim_validator_rejects_excerpt_not_in_source() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    [eid] = await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[{
            "kind": "direct",
            "source_type": "user_input",
            "content": "actual content",
        }],
    )
    result = await validate_claim(
        sessionmaker=sessionmaker,
        simulation_id=sim_id,
        text="claim",
        source_evidence_id=eid,
        source_excerpt="THIS IS NOT IN THE SOURCE",
        claim_type="observation", basis="direct",
    )
    assert not result.passed
    assert any(v.rule_id == "claim.excerpt_not_in_source" for v in result.violations)


@pytest.mark.asyncio
async def test_claim_validator_rejects_basis_mismatch() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    [eid] = await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[{
            "kind": "analogical",
            "source_type": "analogical_market",
            "content": "analogical observation about a comparable market",
        }],
    )
    result = await validate_claim(
        sessionmaker=sessionmaker, simulation_id=sim_id,
        text="claim", source_evidence_id=eid,
        source_excerpt="analogical observation",
        claim_type="observation",
        basis="direct",  # basis says direct but source kind is analogical
    )
    assert not result.passed
    assert any(v.rule_id == "claim.basis_mismatch" for v in result.violations)


@pytest.mark.asyncio
async def test_claim_validator_rejects_orphan_contradiction() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    [eid] = await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[{
            "kind": "direct", "source_type": "user_input",
            "content": "some excerpt here",
        }],
    )
    result = await validate_claim(
        sessionmaker=sessionmaker, simulation_id=sim_id,
        text="contradiction claim", source_evidence_id=eid,
        source_excerpt="some excerpt here",
        claim_type="contradiction", basis="direct",
    )
    assert not result.passed
    assert any(
        v.rule_id == "claim.contradiction_orphan" for v in result.violations
    )


@pytest.mark.asyncio
async def test_claim_validator_missing_source_rejected() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    result = await validate_claim(
        sessionmaker=sessionmaker, simulation_id=sim_id,
        text="x", source_evidence_id=uuid4(),
        source_excerpt="anything", claim_type="observation", basis="direct",
    )
    assert not result.passed
    assert any(v.rule_id == "claim.source_missing" for v in result.violations)


# ---------------------------------------------------------------------------
# Phase 7 helper service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_graph_service_returns_ranked_and_missing_separately() -> None:
    sessionmaker = get_sessionmaker()
    sim_id = await _create_simulation(sessionmaker, brief=_basic_brief())
    await _seed_evidence(
        sessionmaker, simulation_id=sim_id,
        rows=[
            {"source_type": "competitor_page",
             "source_url": "https://shopify-magic.test/",
             "content": "Shopify Magic auto-generates copy",
             "captured_at": datetime.now(UTC)},
            {"kind": "missing", "source_type": "user_input",
             "metadata": {"expected_kind": "public_review"},
             "content": "missing reviews"},
        ],
    )
    await build_evidence_graph(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=None, model=None,
        embedding_provider=MockEmbeddingProvider(),
        use_llm_classifier=False, use_llm_edges=False,
    )
    service = EvidenceGraphService(
        sessionmaker=sessionmaker, embedding_provider=MockEmbeddingProvider(),
    )
    bundle = await service.get_competitor_evidence(sim_id, k=10)
    # missing rows are surfaced separately, never in ranked list.
    for r in bundle.ranked:
        assert r.item.kind != "missing"
    miss_summary = await service.get_missing_evidence_summary(sim_id)
    assert miss_summary.total >= 1
