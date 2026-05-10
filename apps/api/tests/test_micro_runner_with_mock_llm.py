"""Phase 8.2K — runner live-path tests with MockProvider.

These tests cover the full run_micro_simulation loop with `dry_run=False`
but ZERO real network: every LLM call is intercepted by MockProvider.

Asserts:
  * 3 LLM rounds (first_exposure, objection, final_stance) per persona
    + 2 debate turns (when N>=2 and enable_debate=True) all flow
    through `cost_guarded_chat` (visible via stage labels prefixed with
    `micro_`)
  * forbidden-language injection in any one round flips
    output_audit_passed=False on that round AND surfaces the failure
    in `MicroSimulationResult.output_audit.rounds_failing_audit`
  * The runner still returns a structured result; it does NOT crash on
    audit failure.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from assembly.db import get_sessionmaker
from assembly.llm.mock import MockProvider
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
    MicroStance,
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
# DB fixture — TWO Shopify-merchant personas (so debate path is exercised)
# ---------------------------------------------------------------------------


@pytest.fixture
async def two_merchant_personas() -> AsyncIterator[tuple[UUID, UUID]]:
    sessionmaker = get_sessionmaker()
    pid_a = uuid4()
    pid_b = uuid4()
    src_a = uuid4()
    src_b = uuid4()
    traits_a = {
        "role_or_context": "Shopify merchant doing $30k/month",
        "objection_patterns": "fed up with bloated apps",
        "current_alternatives": "Klaviyo Oberlo agency",
        "price_sensitivity": "high; cumulative monthly fees expensive",
        "trust_triggers": "wants brand control",
    }
    traits_b = {
        "role_or_context": "Premium Shopify operator running $200k/month",
        "objection_patterns": "would consider it if my brand voice stayed",
        "current_alternatives": "in-house team",
        "price_sensitivity": "moderate; ROI focused",
        "trust_triggers": "case studies from similar GMV merchants",
    }

    async def _seed_persona(session, pid, src, name, traits):
        session.add(SourceRecord(
            id=src, source_kind="phase_8_2k_test",
            source_url=None, captured_at=datetime.now(UTC),
            content="micro test source content",
            content_hash="m" + uuid4().hex[:60],
            language="en", metadata_={}, ingested_by="phase_8_2k",
            compliance_tag="manual_seed",
            pii_redaction_status="redacted",
            sensitive_scan_status="clean",
        ))
        session.add(PersonaRecord(
            id=pid, display_name=name,
            segment_label="phase_8_2k_test",
            refreshed_at=datetime.now(UTC),
            product_relevance_tags=[],
            population_weight=Decimal("1.0"),
        ))
        await session.flush()
        for field, val in traits.items():
            session.add(PersonaTrait(
                id=uuid4(), persona_id=pid, field_name=field,
                value=val, support_level="direct",
                source_ids=[src], confidence=Decimal("0.9"),
                last_updated_at=datetime.now(UTC),
            ))
            session.add(PersonaEvidenceLink(
                id=uuid4(), persona_id=pid, source_record_id=src,
                contribution_kind="direct", contribution_field=field,
                excerpt=val[:300], confidence=Decimal("0.9"),
            ))

    async with sessionmaker() as session:
        async with session.begin():
            await _seed_persona(session, pid_a, src_a, "Merchant A", traits_a)
            await _seed_persona(session, pid_b, src_b, "Merchant B", traits_b)

    yield pid_a, pid_b
    from sqlalchemy import delete
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(PersonaRecord).where(
                    PersonaRecord.id.in_([pid_a, pid_b])
                )
            )
            await session.execute(
                delete(SourceRecord).where(
                    SourceRecord.id.in_([src_a, src_b])
                )
            )


def _make_match(
    persona_id: UUID, name: str,
    *,
    classification: RelevanceClassification,
    score: int = 27,
) -> PersonaMatch:
    return PersonaMatch(
        persona_id=str(persona_id),
        display_name=name,
        matched_category_key="shopify_or_platform_merchant",
        matched_category_display_name="Platform merchant",
        relevance_score=score,
        classification=classification,
        evidence_link_count=5,
        why_included="micro-runner mock-llm test",
    )


def _make_audience(matches: list[PersonaMatch]) -> RunScopedAudienceRetrievalResult:
    return RunScopedAudienceRetrievalResult(
        brief_summary="micro mock-llm test",
        target_society_plan_summary="phase_8_2k mock_llm test",
        matched_personas=matches,
        excluded_personas=[],
        category_coverage=[],
        source_diversity_summary=SourceDiversitySummary(
            distinct_source_domains=2,
            domains=["test1.invalid", "test2.invalid"],
            minimum_required=2, single_source_risk=False,
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


def _make_brief() -> ProductBriefInput:
    return ProductBriefInput(
        product_name="Mechanical Test Product",
        product_description=(
            "Mock-LLM end-to-end test of the micro-simulation harness."
        ),
    )


# ---------------------------------------------------------------------------
# Happy path: 4-round + debate run, all rounds clean
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_run_with_mock_llm_two_personas_and_debate(
    two_merchant_personas,
) -> None:
    """End-to-end mock-LLM run on N=2 personas with debate enabled.
    Asserts call count, stage-label prefix, audit shape, caveats."""
    pid_a, pid_b = two_merchant_personas
    sm = get_sessionmaker()
    audience = _make_audience([
        _make_match(pid_a, "Merchant A",
                    classification=RelevanceClassification.RELEVANT),
        _make_match(pid_b, "Merchant B",
                    classification=RelevanceClassification.RELEVANT),
    ])

    # All round responses carry the persona's CURRENT stance verbatim;
    # this means no stance shift fires the trigger requirement, and the
    # forbidden-language scanner sees only mechanical persona-voice
    # text. We do NOT use sequence rules so we can serve N calls from a
    # single default rule per stage.
    clean_payload = json.dumps({
        "stance_after": MicroStance.SKEPTICAL.value,
        "reasoning": (
            "MICRO-TEST: I'm skeptical because adding another tool is "
            "more bloat. I would only consider it if my brand control "
            "stayed intact."
        ),
        "objections": ["adding more apps is more bloat"],
        "evidence_citations": ["fed up with bloated apps"],
        "triggered_by_evidence_excerpt": None,
    })
    clean_debate = json.dumps({
        "argument": (
            "MICRO-TEST: I would only consider this if it kept my brand "
            "voice; otherwise I'd stay on my current setup."
        ),
        "cited_evidence_excerpt": "wants brand control",
        "target_stance_after": MicroStance.SKEPTICAL.value,
    })

    provider = MockProvider()
    # Both personas land at SKEPTICAL via baseline ("fed up with bloated
    # apps" → RESISTANT for A, "would consider" → MILDLY_INTERESTED for
    # B), so we pick the easier path: declare each stance as the
    # persona's current stance via separate rules per stage.
    # Easier: register a single default that returns the persona's
    # stance_before via a predicate. Since the predicate has access to
    # ctx but NOT the prompt body easily, we instead force every round
    # to return SKEPTICAL — which means audit will fail for personas
    # whose initial stance is not SKEPTICAL. To keep the happy path
    # clean, we serve clean_payload via .add_default and bypass the
    # shift-trigger check by supplying a non-null trigger.
    clean_payload_with_trigger = json.dumps({
        "stance_after": MicroStance.SKEPTICAL.value,
        "reasoning": (
            "MICRO-TEST: I'm skeptical because adding another tool is "
            "more bloat. I would only consider it if my brand control "
            "stayed intact."
        ),
        "objections": ["adding more apps is more bloat"],
        "evidence_citations": ["fed up with bloated apps"],
        "triggered_by_evidence_excerpt": (
            "fed up with bloated apps"
        ),
    })
    provider.add_default(clean_payload_with_trigger)
    # Debate stage gets its own structured rule (different schema):
    provider.add_response_for_stage("micro_debate_turn", clean_debate)
    provider.add_response_for_stage("micro_debate_turn", clean_debate)

    result = await run_micro_simulation(
        sessionmaker=sm,
        brief=_make_brief(),
        audience_result=audience,
        brief_label="mock_llm_full",
        provider=provider,
        dry_run=False,
        include_weakly_relevant=False,
        enable_debate=True,
    )
    assert result.is_micro_test is True
    assert result.dry_run is False
    assert result.persona_count == 2
    # 3 LLM rounds × 2 personas + 2 debate turns = 8 LLM calls.
    assert result.llm_call_count == 8
    # Trace: 1 baseline per persona + 3 LLM rounds per persona = 8.
    assert len(result.trace.rounds) == 8
    assert len(result.trace.debate_turns) == 2
    # Every LLM call was made with a `micro_` stage label.
    for _msgs, ctx in provider.calls:
        assert ctx.stage.startswith("micro_"), ctx.stage
    # Audit shape: caveats present, no forbidden claims.
    assert result.output_audit.sample_size_caveat_present
    assert result.output_audit.coverage_thinness_caveat_present
    assert result.output_audit.micro_test_label_present
    assert result.output_audit.forbidden_claims_found == []


# ---------------------------------------------------------------------------
# Forbidden-language injection: round audit flips, runner does not crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forbidden_language_in_one_round_surfaces_audit_failure(
    two_merchant_personas,
) -> None:
    """A first_exposure round emits forbidden language; the runner
    completes but the round is flagged and the top-level audit lists
    it. The structured result still returns — it just carries the
    failure flag so the operator can act on it."""
    pid_a, pid_b = two_merchant_personas
    sm = get_sessionmaker()
    audience = _make_audience([
        _make_match(pid_a, "Merchant A",
                    classification=RelevanceClassification.RELEVANT),
        _make_match(pid_b, "Merchant B",
                    classification=RelevanceClassification.RELEVANT),
    ])
    clean = json.dumps({
        "stance_after": MicroStance.SKEPTICAL.value,
        "reasoning": (
            "MICRO-TEST: I'm staying skeptical for now; adding another "
            "tool would compound my existing app sprawl."
        ),
        "objections": ["app sprawl"],
        "evidence_citations": ["fed up with bloated apps"],
        "triggered_by_evidence_excerpt": "fed up with bloated apps",
    })
    poisoned = json.dumps({
        "stance_after": MicroStance.SKEPTICAL.value,
        "reasoning": (
            # Two forbidden categories: forecast/verdict + market reaction.
            "Amboras will dominate this market and the market reaction "
            "is positive."
        ),
        "objections": [],
        "evidence_citations": [],
        "triggered_by_evidence_excerpt": "fed up with bloated apps",
    })
    debate_clean = json.dumps({
        "argument": "MICRO-TEST: persona-voice argument.",
        "cited_evidence_excerpt": "wants brand control",
        "target_stance_after": MicroStance.SKEPTICAL.value,
    })

    provider = MockProvider()
    # First first_exposure call (persona A) gets POISONED; everything
    # else clean.
    provider.add_response_for_stage("micro_first_exposure", poisoned)
    provider.add_default(clean)
    provider.add_response_for_stage("micro_debate_turn", debate_clean)
    provider.add_response_for_stage("micro_debate_turn", debate_clean)

    result = await run_micro_simulation(
        sessionmaker=sm,
        brief=_make_brief(),
        audience_result=audience,
        brief_label="mock_llm_poison",
        provider=provider,
        dry_run=False,
        enable_debate=True,
    )
    # Result still produced.
    assert result.is_micro_test is True
    # Audit reports forbidden categories (sorted, deduped).
    assert result.output_audit.forbidden_claims_found, (
        "Expected forbidden_claims_found to surface poisoned text"
    )
    # The poisoned round shows up in rounds_failing_audit.
    failing = " | ".join(result.output_audit.rounds_failing_audit)
    assert str(pid_a) in failing
    assert "first_exposure" in failing
    # And there is a caveat warning operators that the audit failed.
    caveats_blob = " | ".join(result.caveats).lower()
    assert "forbidden-language audit found" in caveats_blob
