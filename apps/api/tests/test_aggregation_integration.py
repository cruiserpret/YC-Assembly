"""Phase 7 — integration tests against real Postgres.

Asserts the structural Phase 7 contract end-to-end with mocked LLM:
  - run_aggregation_v7 against a seeded simulation produces one
    `simulation_outputs` row + N claims rows.
  - All 9 sections present.
  - GET /simulations/{id}/report returns 200 only when status='reported'.
  - Re-running aggregation raises SimulationOutputAlreadyExists.
  - clear_simulation_output enables re-aggregation.
  - Aggregation is read-only over agent_responses / simulation_rounds /
    debate_turns / evidence_items / evidence_edges.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.embeddings.mock import MockEmbeddingProvider
from assembly.llm.mock import MockProvider
from assembly.main import create_app
from assembly.models.agent import Agent as AgentORM
from assembly.models.claim import Claim
from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EvidenceEdge
from assembly.models.output import SimulationOutput
from assembly.models.round import AgentResponse, DebateTurn, SimulationRound
from assembly.models.simulation import Simulation, SimulationInput
from assembly.pipeline.aggregation.persistence import (
    SimulationOutputAlreadyExists,
    clear_simulation_output,
)
from assembly.pipeline.aggregation.service import (
    AggregationFailed,
    run_aggregation_v7,
)
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
# Test fixtures
# ---------------------------------------------------------------------------


def _basic_brief() -> SimulationBriefIn:
    return SimulationBriefIn(
        product_type="ai_commerce_platform",
        product_name="Amboras-AggTest",
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


async def _seed_full_simulation(
    sessionmaker, *, brief: SimulationBriefIn, pio_dict: dict[str, Any],
) -> tuple[UUID, list[UUID], list[UUID]]:
    """Build a Simulation with a parsed_pio, 1 evidence row, 1 agent,
    1 round, 1 agent_response, 1 missing-evidence row. Returns
    (sim_id, evidence_ids, agent_ids).
    """
    sim_id = uuid4()
    eid_direct = uuid4()
    eid_missing = uuid4()
    agent_id = uuid4()

    async with sessionmaker() as session:
        async with session.begin():
            sim = Simulation(
                id=sim_id, status="simulation_completed",
                progress={"stage": "simulation_completed"},
                parsed_pio=pio_dict,
                evidence_graph_built_at=datetime.now(UTC),  # graph built
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

            # Use node_class='trust_barrier' so the retriever's
            # `for_trust_barrier_evidence` and `for_market_acceptance_evidence`
            # pick this row up; otherwise the mock LLM's anchor wouldn't be
            # in `_all_supplied_evidence_ids` and would trigger a repair loop.
            session.add(EvidenceItem(
                id=eid_direct,
                simulation_id=sim_id,
                kind="direct",
                source_type="user_input",
                content="Founders worry about brand identity damage from autonomous AI.",
                metadata_={"input_field": "user_description"},
                node_class="trust_barrier",
                content_hash="x" * 32,
            ))
            session.add(EvidenceItem(
                id=eid_missing,
                simulation_id=sim_id,
                kind="missing",
                source_type="user_input",
                content="missing public review",
                metadata_={"expected_kind": "public_review"},
                node_class="review",
                content_hash="m" * 32,
            ))

            # 1 agent.
            session.add(AgentORM(
                id=agent_id,
                simulation_id=sim_id,
                segment_label="mid_volume_merchants",
                weight=1.0,
                buyer_state={"summary": "mid-volume merchant; cautious",
                             "cluster": "mid_volume"},
                traits={},
                evidence_anchors=[str(eid_direct)],
            ))

            # 1 simulation_round, 1 agent_response with a shift.
            round_id = uuid4()
            session.add(SimulationRound(
                id=round_id, simulation_id=sim_id,
                round_number=7, round_type="final_stance",
                summary={"stance_distribution": {"skeptical": 1}},
            ))
            session.add(AgentResponse(
                id=uuid4(), round_id=round_id, agent_id=agent_id,
                stance="skeptical",
                reasoning="The agent seemed cautious about brand-control risk.",
                objections=[{
                    "text": "lack of public reviews",
                    "category": "trust",
                    "severity": "strong",
                }],
                persuasion_drivers=[{
                    "text": "consolidation against plugin sprawl",
                    "category": "consolidation",
                    "strength": "moderate",
                    "evidence_anchors": [str(eid_direct)],
                }],
                shift_from_previous={
                    "from_stance": "curious_hesitant",
                    "to_stance": "skeptical",
                    "reason": "concerns about brand control",
                    "triggered_by": str(eid_direct),
                },
                state_after={},
                raw_output={},
            ))
    return sim_id, [eid_direct, eid_missing], [agent_id]


# ---------------------------------------------------------------------------
# E2E aggregation
# ---------------------------------------------------------------------------


def _section_a_response(eid: UUID) -> str:
    return json.dumps({
        "public_opinion_sentiment": {
            "summary": "The society seemed cautiously interested but resistant on brand-control grounds.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
        },
        "persuaded": {
            "summary": "Agents who softened seemed to do so when consolidation against plugin sprawl was named.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
            "factual_claims": [],
        },
        "not_persuaded": {
            "summary": "The strongest resistance appeared to come from agents who cited brand-control risk.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
            "factual_claims": [],
        },
        "market_acceptance_requirement": {
            "summary": "The society seemed to need verifiable public reviews before adopting.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
            "factual_claims": [],
        },
    })


def _section_b_response(eid: UUID) -> str:
    return json.dumps({
        "product_trajectory": {
            "summary": "Across the seven rounds the society appeared to soften slightly then re-harden.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
        },
        "competitor_analysis": {
            "summary": "Agents seemed to view named alternatives as narrower than the product itself.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
            "competitors": [],
        },
    })


def _section_c_response(eid: UUID) -> str:
    return json.dumps({
        "target_audience": {
            "summary": "Agents portraying mid-volume merchants tended to lean more receptive.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
        },
        "positioning": {
            "summary": "The product seemed to land as a consolidation play against the plugin stack.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
            "factual_claims": [],
        },
        "price_structure": {
            "summary": "The supplied starter price seemed reasonable to most agents; concerns centered on opaque higher tiers.",
            "evidence_anchors": [str(eid)],
            "simulation_references": [],
            "confidence": "moderate",
            "validator_notes": [],
            "factual_claims": [],
        },
    })


@pytest.mark.asyncio
async def test_aggregation_e2e_writes_one_row_with_all_sections(valid_pio_json: str) -> None:
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid = eids[0]

    p = MockProvider()
    # Register each stage 4 times so up to 3 repair attempts per call still find a rule.
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))

    row = await run_aggregation_v7(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        provider=p,
        embedding_provider=MockEmbeddingProvider(),
    )

    assert row is not None

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SimulationOutput).where(
                    SimulationOutput.simulation_id == sim_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    out = rows[0]

    # All 9 JSONB columns populated and non-empty.
    for col in (
        "public_opinion_sentiment", "persuasion_analysis",
        "market_acceptance_requirement", "product_trajectory",
        "competitor_analysis", "recommendations",
        "debate_shift_markers", "confidence", "evidence_ledger",
    ):
        assert getattr(out, col), f"section {col} empty"

    # Mechanical sections populated correctly.
    assert "split_confidence" in out.confidence
    assert "counts" in out.evidence_ledger
    assert "markers" in out.debate_shift_markers
    # Validator passed.
    assert out.validator_passed


@pytest.mark.asyncio
async def test_aggregation_rerun_raises_already_exists(valid_pio_json: str) -> None:
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid = eids[0]
    p = MockProvider()
    # Register each stage 4 times so up to 3 repair attempts per call still find a rule.
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))

    await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p, embedding_provider=MockEmbeddingProvider(),
    )

    p2 = MockProvider()
    p2.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
    p2.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
    p2.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))
    with pytest.raises(AggregationFailed) as excinfo:
        await run_aggregation_v7(
            simulation_id=sim_id, sessionmaker=sessionmaker,
            provider=p2, embedding_provider=MockEmbeddingProvider(),
        )
    assert "already" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_clear_simulation_output_enables_rerun(valid_pio_json: str) -> None:
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid = eids[0]
    p = MockProvider()
    # Register each stage 4 times so up to 3 repair attempts per call still find a rule.
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))
    await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p, embedding_provider=MockEmbeddingProvider(),
    )
    await clear_simulation_output(
        sessionmaker=sessionmaker, simulation_id=sim_id,
    )
    p2 = MockProvider()
    p2.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
    p2.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
    p2.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))
    row = await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p2, embedding_provider=MockEmbeddingProvider(),
    )
    assert row is not None


@pytest.mark.asyncio
async def test_accepted_claim_persists_source_url_from_evidence(valid_pio_json: str) -> None:
    """Quality gate: when a factual_claim binds to an evidence_items row that
    has a source_url, the persisted `claims` row MUST carry that URL — not
    NULL. Lossy provenance breaks Phase 8's report rendering."""
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(),
        pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid = eids[0]
    # Patch the seeded evidence row to have a real URL.
    real_url = "https://shopify-magic.example.test/"
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                EvidenceItem.__table__.update()
                .where(EvidenceItem.id == eid)
                .values(source_url=real_url)
            )

    # Mock LLM that emits a factual_claim bound to this evidence with a
    # verbatim excerpt from its content.
    excerpt = "Founders worry about brand identity"

    def section_a_with_claim() -> str:
        return json.dumps({
            "public_opinion_sentiment": {
                "summary": "The society seemed cautious.",
                "evidence_anchors": [str(eid)],
                "simulation_references": [],
                "confidence": "moderate",
                "validator_notes": [],
            },
            "persuaded": {
                "summary": "Agents who softened cited consolidation.",
                "evidence_anchors": [str(eid)],
                "simulation_references": [],
                "confidence": "moderate",
                "validator_notes": [],
                "factual_claims": [
                    {
                        "text": "Founders worry about brand identity (per the brief).",
                        "source_evidence_id": str(eid),
                        "source_excerpt": excerpt,
                        "claim_type": "observation",
                        "basis": "direct",
                        "confidence": 0.9,
                    }
                ],
            },
            "not_persuaded": {
                "summary": "Agents portraying premium operators tended to resist.",
                "evidence_anchors": [str(eid)],
                "simulation_references": [],
                "confidence": "moderate",
                "validator_notes": [],
                "factual_claims": [],
            },
            "market_acceptance_requirement": {
                "summary": "The society seemed to need verifiable proof.",
                "evidence_anchors": [str(eid)],
                "simulation_references": [],
                "confidence": "moderate",
                "validator_notes": [],
                "factual_claims": [],
            },
        })

    p = MockProvider()
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", section_a_with_claim())
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))

    await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p, embedding_provider=MockEmbeddingProvider(),
    )

    # Read back the claim row.
    async with sessionmaker() as session:
        claims = (
            await session.execute(
                select(Claim).where(Claim.simulation_id == sim_id)
            )
        ).scalars().all()
    assert len(claims) >= 1, "expected at least one claim row written"
    claim = claims[0]
    assert claim.source_url == real_url, (
        f"claims.source_url should match bound evidence's source_url; "
        f"expected {real_url!r}, got {claim.source_url!r}"
    )


@pytest.mark.asyncio
async def test_aggregation_does_not_mutate_raw_simulation_data(valid_pio_json: str) -> None:
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid = eids[0]

    async with sessionmaker() as session:
        ar_count_before = (
            await session.execute(
                select(func.count(AgentResponse.id))
                .where(AgentResponse.round_id.in_(
                    select(SimulationRound.id).where(
                        SimulationRound.simulation_id == sim_id
                    )
                ))
            )
        ).scalar_one()
        ev_count_before = (
            await session.execute(
                select(func.count(EvidenceItem.id))
                .where(EvidenceItem.simulation_id == sim_id)
            )
        ).scalar_one()
        edge_count_before = (
            await session.execute(
                select(func.count(EvidenceEdge.id))
                .where(EvidenceEdge.simulation_id == sim_id)
            )
        ).scalar_one()

    p = MockProvider()
    # Register each stage 4 times so up to 3 repair attempts per call still find a rule.
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))
    await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p, embedding_provider=MockEmbeddingProvider(),
    )

    async with sessionmaker() as session:
        ar_count_after = (
            await session.execute(
                select(func.count(AgentResponse.id))
                .where(AgentResponse.round_id.in_(
                    select(SimulationRound.id).where(
                        SimulationRound.simulation_id == sim_id
                    )
                ))
            )
        ).scalar_one()
        ev_count_after = (
            await session.execute(
                select(func.count(EvidenceItem.id))
                .where(EvidenceItem.simulation_id == sim_id)
            )
        ).scalar_one()
        edge_count_after = (
            await session.execute(
                select(func.count(EvidenceEdge.id))
                .where(EvidenceEdge.simulation_id == sim_id)
            )
        ).scalar_one()

    assert ar_count_after == ar_count_before
    assert ev_count_after == ev_count_before
    assert edge_count_after == edge_count_before


# ---------------------------------------------------------------------------
# Report endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_report_endpoint_404_for_unknown_simulation(app_client, valid_pio_json: str) -> None:
    r = await app_client.get(f"/simulations/{uuid4()}/report")
    assert r.status_code == 404
    assert r.json()["detail"]["kind"] == "simulation_not_found"


@pytest.mark.asyncio
async def test_report_endpoint_409_when_not_reported(app_client, valid_pio_json: str) -> None:
    sessionmaker = get_sessionmaker()
    sim_id, _, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    r = await app_client.get(f"/simulations/{sim_id}/report")
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["kind"] == "report_not_ready"
    assert body["detail"]["current_status"] == "simulation_completed"


@pytest.mark.asyncio
async def test_report_endpoint_includes_evidence_anchor_details(app_client, valid_pio_json: str) -> None:
    """Phase 8: every UUID referenced in the report (anchors, evidence-item
    simulation_references, missing entries, claim sources) MUST resolve
    in the response's `evidence_anchor_details` map."""
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid_direct = eids[0]
    eid_missing = eids[1]
    p = MockProvider()
    # Register each stage 4 times so up to 3 repair attempts per call still find a rule.
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid_direct))
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid_direct))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid_direct))
    await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p, embedding_provider=MockEmbeddingProvider(),
    )
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                Simulation.__table__.update()
                .where(Simulation.id == sim_id)
                .values(status="reported", progress={"stage": "reported"})
            )

    r = await app_client.get(f"/simulations/{sim_id}/report")
    assert r.status_code == 200
    body = r.json()
    assert "evidence_anchor_details" in body
    details = body["evidence_anchor_details"]
    # The mock anchors used eid_direct; missing-evidence ledger references
    # eid_missing. Both should appear.
    assert str(eid_direct) in details, (
        f"expected eid_direct {eid_direct!s} in evidence_anchor_details; "
        f"got keys: {list(details.keys())}"
    )
    assert str(eid_missing) in details, (
        f"expected eid_missing {eid_missing!s} (from evidence_ledger) in "
        f"evidence_anchor_details; got keys: {list(details.keys())}"
    )
    # Hydrated fields present.
    direct_meta = details[str(eid_direct)]
    for k in ("evidence_id", "kind", "node_class", "source_type"):
        assert k in direct_meta, f"missing {k} in details for eid_direct"
    assert direct_meta["kind"] == "direct"
    missing_meta = details[str(eid_missing)]
    assert missing_meta["kind"] == "missing"


@pytest.mark.asyncio
async def test_report_endpoint_returns_report_when_reported(app_client, valid_pio_json: str) -> None:
    sessionmaker = get_sessionmaker()
    sim_id, eids, _ = await _seed_full_simulation(
        sessionmaker, brief=_basic_brief(), pio_dict=__import__("json").loads(valid_pio_json),
    )
    eid = eids[0]
    p = MockProvider()
    # Register each stage 4 times so up to 3 repair attempts per call still find a rule.
    for _ in range(4):
        p.add_response_for_stage("aggregation_sentiment_persuasion", _section_a_response(eid))
        p.add_response_for_stage("aggregation_trajectory_competitor", _section_b_response(eid))
        p.add_response_for_stage("aggregation_recommendations", _section_c_response(eid))
    await run_aggregation_v7(
        simulation_id=sim_id, sessionmaker=sessionmaker,
        provider=p, embedding_provider=MockEmbeddingProvider(),
    )
    # Manually flip the sim status to 'reported' (the orchestrator does
    # this in production; here we test the endpoint contract directly).
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                Simulation.__table__.update()
                .where(Simulation.id == sim_id)
                .values(status="reported", progress={"stage": "reported"})
            )

    r = await app_client.get(f"/simulations/{sim_id}/report")
    assert r.status_code == 200
    body = r.json()
    for k in (
        "public_opinion_sentiment", "persuasion_analysis",
        "market_acceptance_requirement", "product_trajectory",
        "competitor_analysis", "recommendations",
        "debate_shift_markers", "confidence", "evidence_ledger",
    ):
        assert k in body
        assert body[k]
