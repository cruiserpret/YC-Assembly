"""Phase 8.2K — runner dry-run tests.

Dry-run path:
  * no LLM calls
  * loads persona states from DB
  * runs deterministic baseline round per persona
  * emits a fully-shaped MicroSimulationResult with caveats + summary
  * never writes SimulationOutput / population-graph rows

Refusal paths:
  * zero relevant personas in audience pool
  * only weakly_relevant personas without operator opt-in
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink,
    PersonaRecord,
    PersonaTrait,
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
    MicroSimulationRefused,
    run_micro_simulation,
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


# ---------------------------------------------------------------------------
# DB fixture — one minimal Shopify-merchant persona
# ---------------------------------------------------------------------------


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
                content_hash="d" + uuid4().hex[:60],
                language="en", metadata_={}, ingested_by="phase_8_2k",
                compliance_tag="manual_seed",
                pii_redaction_status="redacted",
                sensitive_scan_status="clean",
            ))
            session.add(PersonaRecord(
                id=persona_id, display_name="Merchant for dry test",
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


# ---------------------------------------------------------------------------
# Helpers — build minimal audience_result + brief
# ---------------------------------------------------------------------------


def _make_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Test Product",
        product_description=(
            "A mechanical micro-test brief used to verify the harness."
        ),
    )


def _make_match(
    persona_id: UUID,
    *,
    classification: RelevanceClassification,
    score: int = 27,
) -> PersonaMatch:
    return PersonaMatch(
        persona_id=str(persona_id),
        display_name="Merchant for dry test",
        matched_category_key="shopify_or_platform_merchant",
        matched_category_display_name="Platform merchant",
        relevance_score=score,
        classification=classification,
        evidence_link_count=5,
        why_included="dry-run test fixture",
    )


def _make_audience_result(
    matches: list[PersonaMatch],
) -> RunScopedAudienceRetrievalResult:
    return RunScopedAudienceRetrievalResult(
        brief_summary="dry-run micro test",
        target_society_plan_summary=(
            "phase_8_2k dry test (no plan needed)"
        ),
        matched_personas=matches,
        excluded_personas=[],
        category_coverage=[],
        source_diversity_summary=SourceDiversitySummary(
            distinct_source_domains=1,
            domains=["test.invalid"],
            minimum_required=2,
            single_source_risk=True,
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
# Dry-run happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_produces_structured_trace_no_llm(
    merchant_persona_in_db,
) -> None:
    """Dry-run path: no LLM calls, but a fully-shaped MicroSimulationResult
    is returned with both mandatory caveats and the MICRO-TEST label."""
    persona_id = merchant_persona_in_db
    sm = get_sessionmaker()
    audience = _make_audience_result([
        _make_match(
            persona_id,
            classification=RelevanceClassification.RELEVANT,
        )
    ])
    result = await run_micro_simulation(
        sessionmaker=sm,
        brief=_make_brief(),
        audience_result=audience,
        brief_label="dry_test",
        provider=None,
        dry_run=True,
        include_weakly_relevant=False,
        enable_debate=False,
    )
    assert result.is_micro_test is True
    assert result.dry_run is True
    assert result.llm_call_count == 0
    assert result.cost_actual_usd == 0.0
    assert result.persona_count == 1
    # Mandatory caveats:
    assert len(result.caveats) >= 2
    joined = " | ".join(result.caveats).lower()
    assert "sample-size" in joined
    assert "coverage-thinness" in joined
    # MICRO-TEST label in summary text:
    assert "MICRO-TEST" in result.summary_text
    assert "n=1" in result.summary_text
    assert (
        result.output_audit.sample_size_caveat_present
        and result.output_audit.coverage_thinness_caveat_present
        and result.output_audit.micro_test_label_present
    )
    # Trace contains the deterministic baseline round only.
    assert len(result.trace.rounds) == 1
    assert result.trace.rounds[0].llm_call_was_used is False
    assert result.trace.debate_turns == []


# ---------------------------------------------------------------------------
# Refusal — zero relevant personas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_refuses_zero_relevant_personas() -> None:
    """If the audience pool has zero RELEVANT/HIGHLY_RELEVANT personas,
    the runner refuses with MicroSimulationRefused."""
    sm = get_sessionmaker()
    audience = _make_audience_result([])
    with pytest.raises(MicroSimulationRefused, match="zero relevant"):
        await run_micro_simulation(
            sessionmaker=sm,
            brief=_make_brief(),
            audience_result=audience,
            brief_label="zero_relevant_test",
            provider=None,
            dry_run=True,
        )


# ---------------------------------------------------------------------------
# Refusal — weakly_relevant only without opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_refuses_weakly_only_without_optin(
    merchant_persona_in_db,
) -> None:
    """An audience pool that only contains WEAKLY_RELEVANT personas
    must be refused unless the operator passes
    `include_weakly_relevant=True`."""
    persona_id = merchant_persona_in_db
    sm = get_sessionmaker()
    audience = _make_audience_result([
        _make_match(
            persona_id,
            classification=RelevanceClassification.WEAKLY_RELEVANT,
            score=22,
        )
    ])
    # Without opt-in: no relevant personas → refusal.
    with pytest.raises(MicroSimulationRefused, match="zero relevant"):
        await run_micro_simulation(
            sessionmaker=sm,
            brief=_make_brief(),
            audience_result=audience,
            brief_label="weakly_only_test",
            provider=None,
            dry_run=True,
            include_weakly_relevant=False,
        )
