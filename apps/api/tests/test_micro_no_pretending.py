"""Phase 8.2K — anti-pretending tests.

Three structural guarantees:

  1. The result object cannot pretend to be a population-level result.
     `MicroSimulationResult.is_micro_test` is `Literal[True]` and
     caveats include both sample-size + coverage-thinness markers.

  2. No `tiny_ready` claim can be smuggled into the summary text. The
     forbidden-language scanner blocks it; the runner emits an audit
     warning if it ever appears.

  3. End-to-end: after a full mock-LLM run, NO row appears in any of
     the population-graph or Phase 7 tables (`simulation_outputs`,
     `simulation_rounds`, `persona_graph_edges`, `persona_clusters`,
     `persona_opinions`, `persona_cluster_memberships`,
     `agents`, `agent_responses`, `debate_turns`, `agent_edges`).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.llm.mock import MockProvider
from assembly.models import (
    Agent,
    AgentEdge,
    AgentResponse,
    DebateTurn,
    PersonaCluster,
    PersonaClusterMembership,
    PersonaEvidenceLink,
    PersonaGraphEdge,
    PersonaOpinion,
    PersonaRecord,
    PersonaTrait,
    SimulationOutput,
    SimulationRound,
    SourceRecord,
)
from assembly.pipeline.audience_retrieval.schemas import (
    NextStepRecommendation,
    PersonaMatch,
    ReadinessByMode,
    RunScopedAudienceRetrievalResult,
    SourceDiversitySummary,
)
from assembly.pipeline.micro_simulation import (
    MicroStance,
    run_micro_simulation,
    scan_text_for_forbidden_claims,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification
from assembly.pipeline.target_society.schemas import ProductBriefInput


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test() -> AsyncIterator[None]:
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.fixture
async def merchant_persona_in_db() -> AsyncIterator[UUID]:
    sessionmaker = get_sessionmaker()
    persona_id = uuid4()
    src_id = uuid4()
    traits = {
        "role_or_context": "Shopify merchant doing $30k/month",
        "objection_patterns": "fed up with bloated apps",
        "current_alternatives": "Klaviyo and freelance designers",
        "price_sensitivity": "high; cumulative monthly fees expensive",
        "trust_triggers": "wants brand control",
    }
    async with sessionmaker() as session:
        async with session.begin():
            session.add(SourceRecord(
                id=src_id, source_kind="phase_8_2k_test",
                source_url=None, captured_at=datetime.now(UTC),
                content="micro test source content",
                content_hash="p" + uuid4().hex[:60],
                language="en", metadata_={}, ingested_by="phase_8_2k",
                compliance_tag="manual_seed",
                pii_redaction_status="redacted",
                sensitive_scan_status="clean",
            ))
            session.add(PersonaRecord(
                id=persona_id, display_name="Merchant for pretend test",
                segment_label="phase_8_2k_test",
                refreshed_at=datetime.now(UTC),
                product_relevance_tags=[],
                population_weight=Decimal("1.0"),
            ))
            await session.flush()
            for field, val in traits.items():
                session.add(PersonaTrait(
                    id=uuid4(), persona_id=persona_id, field_name=field,
                    value=val, support_level="direct",
                    source_ids=[src_id], confidence=Decimal("0.9"),
                    last_updated_at=datetime.now(UTC),
                ))
                session.add(PersonaEvidenceLink(
                    id=uuid4(), persona_id=persona_id,
                    source_record_id=src_id,
                    contribution_kind="direct",
                    contribution_field=field,
                    excerpt=val[:300], confidence=Decimal("0.9"),
                ))
    yield persona_id
    from sqlalchemy import delete
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(PersonaRecord).where(PersonaRecord.id == persona_id)
            )
            await session.execute(
                delete(SourceRecord).where(SourceRecord.id == src_id)
            )


def _make_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="No-Pretending Test Product",
        product_description=(
            "Anti-pretending test of the micro-simulation harness."
        ),
    )


def _make_audience(persona_id: UUID) -> RunScopedAudienceRetrievalResult:
    return RunScopedAudienceRetrievalResult(
        brief_summary="anti-pretending micro test",
        target_society_plan_summary="phase_8_2k anti-pretending",
        matched_personas=[PersonaMatch(
            persona_id=str(persona_id),
            display_name="Merchant for pretend test",
            matched_category_key="shopify_or_platform_merchant",
            matched_category_display_name="Platform merchant",
            relevance_score=27,
            classification=RelevanceClassification.RELEVANT,
            evidence_link_count=5,
            why_included="anti-pretending test fixture",
        )],
        excluded_personas=[],
        category_coverage=[],
        source_diversity_summary=SourceDiversitySummary(
            distinct_source_domains=1,
            domains=["test.invalid"],
            minimum_required=2, single_source_risk=True,
        ),
        readiness_by_mode=ReadinessByMode(
            tiny_ready=False, small_ready=False, serious_ready=False,
            blocked_reasons=["micro-test only"],
        ),
        topup_recommendations=[],
        warnings_and_caveats=[],
        next_step_recommendation=(
            NextStepRecommendation.RUN_TOPUP_INGESTION_FIRST
        ),
    )


# ---------------------------------------------------------------------------
# 1. Population-claim forbidden-language detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "the Amboras society thinks this is a great idea",
    "this persona is representative of the target market",
    "represents the target market for direct-to-consumer brands",
    "speaks for the market of Shopify merchants",
])
def test_scanner_blocks_population_level_claims(phrase: str) -> None:
    """The scanner refuses any phrasing that conflates one persona's
    voice with a population-level finding."""
    found = scan_text_for_forbidden_claims(phrase)
    assert found, (
        f"Expected scanner to flag population-claim phrase: {phrase!r}"
    )


# ---------------------------------------------------------------------------
# 2. tiny_ready / build-or-kill claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "tiny_ready = true",
    "tiny_ready: yes",
    "tiny ready is true",
    "we should build it",
    "let's kill this product",
    "we should pivot the product",
])
def test_scanner_blocks_readiness_or_decision_claims(phrase: str) -> None:
    """No micro-test text may declare readiness or product-decision
    verdicts. Both are population-/decision-level claims, beyond
    what an n=1..N harness can support."""
    found = scan_text_for_forbidden_claims(phrase)
    assert found, (
        f"Expected scanner to flag readiness/decision phrase: {phrase!r}"
    )


# ---------------------------------------------------------------------------
# 3. End-to-end: no Phase 7 / population-graph tables get written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_run_does_not_write_population_or_phase7_tables(
    merchant_persona_in_db,
) -> None:
    """After a full mock-LLM micro-run completes, ALL of these tables
    must have row counts equal to whatever they had BEFORE the run.
    This is the structural anti-pretending check."""
    persona_id = merchant_persona_in_db
    sm = get_sessionmaker()

    # Snapshot row counts in every forbidden table BEFORE the run.
    forbidden_models = [
        SimulationOutput, SimulationRound,
        PersonaGraphEdge, PersonaCluster, PersonaClusterMembership,
        PersonaOpinion,
        Agent, AgentResponse, DebateTurn, AgentEdge,
    ]
    async with sm() as session:
        before = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }

    # Run the micro-simulation with mock LLM.
    clean_payload = json.dumps({
        "stance_after": MicroStance.SKEPTICAL.value,
        "reasoning": (
            "MICRO-TEST: I'm a Shopify merchant; another tool is "
            "more bloat. Wants brand control."
        ),
        "objections": ["app sprawl"],
        "evidence_citations": ["fed up with bloated apps"],
        "triggered_by_evidence_excerpt": "fed up with bloated apps",
    })
    provider = MockProvider()
    provider.add_default(clean_payload)

    result = await run_micro_simulation(
        sessionmaker=sm,
        brief=_make_brief(),
        audience_result=_make_audience(persona_id),
        brief_label="anti_pretending",
        provider=provider,
        dry_run=False,
        enable_debate=False,  # skip debate to keep this test minimal
    )
    assert result.is_micro_test is True
    assert result.dry_run is False

    # Snapshot AFTER. Every count must be unchanged.
    async with sm() as session:
        after = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }

    deltas = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert deltas == {}, (
        f"Phase 8.2K runner wrote to forbidden tables: {deltas}"
    )
