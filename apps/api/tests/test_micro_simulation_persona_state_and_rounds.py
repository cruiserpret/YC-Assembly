"""Phase 8.2K — persona-state loader + deterministic baseline +
LLM-round (mocked) tests."""
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
from assembly.models.simulation import Simulation
from assembly.pipeline.audience_retrieval.schemas import PersonaMatch
from assembly.pipeline.micro_simulation import (
    MicroPersonaStateLoadError,
    MicroRelevanceLabel,
    MicroRoundKind,
    MicroStance,
    load_micro_persona_state,
    run_baseline_round,
    run_llm_round,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification


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
# Fixture helpers — insert + cleanup a persona for the test
# ---------------------------------------------------------------------------


@pytest.fixture
async def shopify_merchant_persona() -> AsyncIterator[tuple[UUID, dict]]:
    """Insert a Shopify-merchant-shape persona; yield (id, traits).
    Always cleaned up at end."""
    sessionmaker = get_sessionmaker()
    persona_id = uuid4()
    src_id = uuid4()
    traits_data = {
        "role_or_context": "Shopify merchant doing $30k/month",
        "objection_patterns": "fed up with plugin bloat and too many apps",
        "current_alternatives": "Klaviyo Oberlo agency",
        "price_sensitivity": "high; cumulative monthly fees expensive",
        "trust_triggers": "wants brand control",
    }
    async with sessionmaker() as session:
        async with session.begin():
            session.add(SourceRecord(
                id=src_id, source_kind="phase_8_2k_test",
                source_url=None, captured_at=datetime.now(UTC),
                content="micro test source content", content_hash="k" + uuid4().hex[:60],
                language="en", metadata_={}, ingested_by="phase_8_2k",
                compliance_tag="manual_seed",
                pii_redaction_status="redacted", sensitive_scan_status="clean",
            ))
            session.add(PersonaRecord(
                id=persona_id, display_name="Test Merchant",
                segment_label="phase_8_2k_test", refreshed_at=datetime.now(UTC),
                product_relevance_tags=[],
                population_weight=Decimal("1.0"),
            ))
            await session.flush()
            for field, val in traits_data.items():
                session.add(PersonaTrait(
                    id=uuid4(), persona_id=persona_id, field_name=field,
                    value=val, support_level="direct",
                    source_ids=[src_id], confidence=Decimal("0.9"),
                    last_updated_at=datetime.now(UTC),
                ))
                session.add(PersonaEvidenceLink(
                    id=uuid4(), persona_id=persona_id, source_record_id=src_id,
                    contribution_kind="direct", contribution_field=field,
                    excerpt=val[:300], confidence=Decimal("0.9"),
                ))
    yield persona_id, traits_data
    # Cleanup
    from sqlalchemy import delete
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(delete(PersonaRecord).where(PersonaRecord.id == persona_id))
            await session.execute(delete(SourceRecord).where(SourceRecord.id == src_id))


def _match(persona_id: UUID, *, score: int, classification: RelevanceClassification,
           name: str = "Test Merchant") -> PersonaMatch:
    return PersonaMatch(
        persona_id=str(persona_id),
        display_name=name,
        matched_category_key="shopify_or_platform_merchant",
        matched_category_display_name="Platform merchant",
        relevance_score=score,
        classification=classification,
        evidence_link_count=5,
        why_included="test fixture",
    )


# ---------------------------------------------------------------------------
# Persona-state loader
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loader_rejects_not_relevant_persona(
    shopify_merchant_persona,
) -> None:
    persona_id, _ = shopify_merchant_persona
    sm = get_sessionmaker()
    bad = _match(
        persona_id, score=10, classification=RelevanceClassification.NOT_RELEVANT,
    )
    with pytest.raises(MicroPersonaStateLoadError):
        await load_micro_persona_state(
            sessionmaker=sm, persona_match=bad, include_weakly_relevant=False,
        )


@pytest.mark.asyncio
async def test_loader_rejects_weakly_relevant_without_optin(
    shopify_merchant_persona,
) -> None:
    persona_id, _ = shopify_merchant_persona
    sm = get_sessionmaker()
    weak = _match(
        persona_id, score=20, classification=RelevanceClassification.WEAKLY_RELEVANT,
    )
    with pytest.raises(MicroPersonaStateLoadError):
        await load_micro_persona_state(
            sessionmaker=sm, persona_match=weak, include_weakly_relevant=False,
        )


@pytest.mark.asyncio
async def test_loader_accepts_weakly_relevant_with_optin(
    shopify_merchant_persona,
) -> None:
    persona_id, _ = shopify_merchant_persona
    sm = get_sessionmaker()
    weak = _match(
        persona_id, score=20, classification=RelevanceClassification.WEAKLY_RELEVANT,
    )
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=weak, include_weakly_relevant=True,
    )
    assert state.relevance_label == MicroRelevanceLabel.WEAKLY_RELEVANT
    assert state.caveats, "weakly_relevant state must carry caveats"


@pytest.mark.asyncio
async def test_loader_loads_only_supported_traits(
    shopify_merchant_persona,
) -> None:
    persona_id, traits = shopify_merchant_persona
    sm = get_sessionmaker()
    m = _match(
        persona_id, score=27, classification=RelevanceClassification.RELEVANT,
    )
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=m, include_weakly_relevant=False,
    )
    # Every trait we inserted is direct; all should be in supported.
    assert set(state.supported_traits.keys()) == set(traits.keys())
    # Evidence excerpts populated.
    assert state.evidence_excerpts, "evidence_excerpts should be populated"


# ---------------------------------------------------------------------------
# Deterministic baseline round
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_is_pure_no_llm_call(shopify_merchant_persona) -> None:
    persona_id, _ = shopify_merchant_persona
    sm = get_sessionmaker()
    m = _match(persona_id, score=27, classification=RelevanceClassification.RELEVANT)
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=m, include_weakly_relevant=False,
    )
    rr = run_baseline_round(state)
    assert rr.llm_call_was_used is False
    assert rr.output_audit_passed is True
    assert rr.round_kind == MicroRoundKind.BASELINE
    assert rr.stance_before == rr.stance_after  # baseline does not shift


@pytest.mark.asyncio
async def test_baseline_initial_stance_skeptical_for_complaint_persona(
    shopify_merchant_persona,
) -> None:
    persona_id, _ = shopify_merchant_persona
    sm = get_sessionmaker()
    m = _match(persona_id, score=27, classification=RelevanceClassification.RELEVANT)
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=m, include_weakly_relevant=False,
    )
    # Persona has "fed up", "expensive", "bloat" — should land at RESISTANT
    # (fed up is the strongest marker; rip-off etc. weren't present so
    # RESISTANT is determined by "fed up" + "burned by"-style markers).
    # In our fixture, "fed up" is in objection_patterns → RESISTANT.
    assert state.initial_stance in (MicroStance.RESISTANT, MicroStance.SKEPTICAL)


# ---------------------------------------------------------------------------
# LLM round via cost_guarded_chat (with MockProvider)
# ---------------------------------------------------------------------------


@pytest.fixture
async def admin_simulation_id() -> AsyncIterator[UUID]:
    sm = get_sessionmaker()
    sim_id = uuid4()
    async with sm() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id, user_id="phase_8_2k_test",
                status="phase_8_2k_test", progress={},
                total_cost_usd=Decimal("0"), total_latency_ms=0,
            ))
    yield sim_id
    from sqlalchemy import delete
    async with sm() as session:
        async with session.begin():
            await session.execute(delete(Simulation).where(Simulation.id == sim_id))


@pytest.mark.asyncio
async def test_llm_round_routes_through_cost_guarded_chat(
    shopify_merchant_persona, admin_simulation_id,
) -> None:
    persona_id, _ = shopify_merchant_persona
    sim_id = admin_simulation_id
    sm = get_sessionmaker()
    m = _match(persona_id, score=27, classification=RelevanceClassification.RELEVANT)
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=m, include_weakly_relevant=False,
    )
    provider = MockProvider()
    # Mock returns the persona's CURRENT stance (no shift) so the audit
    # passes without needing a triggered_by_evidence_excerpt. This test
    # verifies routing through cost_guarded_chat, not shift attribution.
    provider.add_default(json.dumps({
        "stance_after": state.current_stance.value,
        "reasoning": (
            "MICRO-TEST: I'm a Shopify merchant. The product would only "
            "work if it didn't break my brand control."
        ),
        "objections": ["plugin bloat already wastes my time"],
        "evidence_citations": ["fed up with plugin bloat and too many apps"],
        "triggered_by_evidence_excerpt": None,
    }))
    rr = await run_llm_round(
        state=state, round_kind=MicroRoundKind.FIRST_EXPOSURE,
        brief_summary="Test brief.",
        sessionmaker=sm, simulation_id=sim_id,
        provider=provider, model="test-model",
    )
    assert rr.llm_call_was_used is True
    assert rr.output_audit_passed is True
    assert rr.stance_after == state.current_stance


@pytest.mark.asyncio
async def test_llm_round_rejects_forbidden_language(
    shopify_merchant_persona, admin_simulation_id,
) -> None:
    """If the LLM emits forbidden language, the audit_passed flag flips
    False and the categories fired are recorded in audit_notes."""
    persona_id, _ = shopify_merchant_persona
    sim_id = admin_simulation_id
    sm = get_sessionmaker()
    m = _match(persona_id, score=27, classification=RelevanceClassification.RELEVANT)
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=m, include_weakly_relevant=False,
    )
    provider = MockProvider()
    provider.add_default(json.dumps({
        "stance_after": "skeptical",
        "reasoning": "Amboras will succeed in this market.",  # forbidden
        "objections": [],
        "evidence_citations": [],
        "triggered_by_evidence_excerpt": None,
    }))
    rr = await run_llm_round(
        state=state, round_kind=MicroRoundKind.FIRST_EXPOSURE,
        brief_summary="Test brief.",
        sessionmaker=sm, simulation_id=sim_id,
        provider=provider, model="test-model",
    )
    assert rr.llm_call_was_used is True
    assert rr.output_audit_passed is False
    blob = " | ".join(rr.output_audit_notes)
    assert "forbidden language" in blob.lower()


@pytest.mark.asyncio
async def test_llm_round_rejects_stance_shift_without_trigger(
    shopify_merchant_persona, admin_simulation_id,
) -> None:
    """Stance shifted from current → different value, but
    triggered_by_evidence_excerpt is null. Audit fails."""
    persona_id, _ = shopify_merchant_persona
    sim_id = admin_simulation_id
    sm = get_sessionmaker()
    m = _match(persona_id, score=27, classification=RelevanceClassification.RELEVANT)
    state = await load_micro_persona_state(
        sessionmaker=sm, persona_match=m, include_weakly_relevant=False,
    )
    # current stance is RESISTANT (from baseline). Try to shift to
    # MILDLY_INTERESTED with no trigger.
    provider = MockProvider()
    provider.add_default(json.dumps({
        "stance_after": "mildly_interested",
        "reasoning": "MICRO-TEST: I'd consider it.",
        "objections": [],
        "evidence_citations": [],
        "triggered_by_evidence_excerpt": None,  # missing!
    }))
    rr = await run_llm_round(
        state=state, round_kind=MicroRoundKind.FIRST_EXPOSURE,
        brief_summary="Test brief.",
        sessionmaker=sm, simulation_id=sim_id,
        provider=provider, model="test-model",
    )
    assert rr.output_audit_passed is False
    blob = " | ".join(rr.output_audit_notes)
    assert "triggered_by_evidence_excerpt" in blob


@pytest.mark.asyncio
async def test_micro_llm_call_rejects_non_micro_stage_label() -> None:
    """The LLM seam refuses any stage label that doesn't start with
    `micro_` — structural guard."""
    from assembly.pipeline.micro_simulation.llm_call import micro_llm_call
    sm = get_sessionmaker()
    sim_id = uuid4()
    provider = MockProvider()
    with pytest.raises(ValueError, match="non-micro stage label"):
        await micro_llm_call(
            sessionmaker=sm, simulation_id=sim_id,
            stage="round_baseline",  # NOT micro_*
            messages=[], provider=provider,
        )
