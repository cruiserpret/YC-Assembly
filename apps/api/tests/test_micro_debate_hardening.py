"""Phase 8.2K.1 — debate-turn hardening tests.

Four scenarios:

  1. Mock LLM emits a canonical stance value → debate passes; no
     repair attempt fires.
  2. Mock LLM emits a non-canonical stance, then a canonical one on
     retry → repair succeeds; turn is `output_audit_passed=True` and
     `output_audit_notes` records that a repair was needed.
  3. Mock LLM emits a non-canonical stance twice → repair exhausted;
     turn is `output_audit_passed=False` with explicit notes; the
     target stance is preserved (no silent coercion).
  4. Mock LLM emits a canonical stance but with forbidden language in
     the argument → still fails audit, with the forbidden-language
     note added on top of the (clean) repair-loop notes.
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
from assembly.models.simulation import Simulation
from assembly.pipeline.audience_retrieval.schemas import PersonaMatch
from assembly.pipeline.micro_simulation import (
    MicroStance,
    load_micro_persona_state,
)
from assembly.pipeline.micro_simulation.debate import run_debate_turn
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
# Fixture: TWO Shopify-merchant personas
# ---------------------------------------------------------------------------


@pytest.fixture
async def two_personas_in_db() -> AsyncIterator[tuple[UUID, UUID]]:
    sessionmaker = get_sessionmaker()
    pid_a = uuid4()
    pid_b = uuid4()
    src_a = uuid4()
    src_b = uuid4()
    traits = {
        "role_or_context": "Shopify merchant doing $30k/month",
        "objection_patterns": "fed up with bloated apps",
        "current_alternatives": "Klaviyo and freelance designers",
        "price_sensitivity": "high; cumulative monthly fees expensive",
        "trust_triggers": "wants brand control",
    }

    async def _seed(session, pid, src, name):
        session.add(SourceRecord(
            id=src, source_kind="phase_8_2k1_test",
            source_url=None, captured_at=datetime.now(UTC),
            content="micro test source content",
            content_hash="h" + uuid4().hex[:60],
            language="en", metadata_={}, ingested_by="phase_8_2k1",
            compliance_tag="manual_seed",
            pii_redaction_status="redacted",
            sensitive_scan_status="clean",
        ))
        session.add(PersonaRecord(
            id=pid, display_name=name,
            segment_label="phase_8_2k1_test",
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
            await _seed(session, pid_a, src_a, "Speaker Persona")
            await _seed(session, pid_b, src_b, "Target Persona")

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


@pytest.fixture
async def admin_simulation_id() -> AsyncIterator[UUID]:
    sm = get_sessionmaker()
    sim_id = uuid4()
    async with sm() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id, user_id="phase_8_2k1_test",
                status="phase_8_2k1_test", progress={},
                total_cost_usd=Decimal("0"), total_latency_ms=0,
            ))
    yield sim_id
    from sqlalchemy import delete
    async with sm() as session:
        async with session.begin():
            await session.execute(
                delete(Simulation).where(Simulation.id == sim_id)
            )


def _match(persona_id: UUID, name: str) -> PersonaMatch:
    return PersonaMatch(
        persona_id=str(persona_id),
        display_name=name,
        matched_category_key="shopify_or_platform_merchant",
        matched_category_display_name="Platform merchant",
        relevance_score=27,
        classification=RelevanceClassification.RELEVANT,
        evidence_link_count=5,
        why_included="phase 8.2k.1 hardening test",
    )


async def _load_speaker_target(
    pid_a: UUID, pid_b: UUID,
):
    sm = get_sessionmaker()
    speaker = await load_micro_persona_state(
        sessionmaker=sm,
        persona_match=_match(pid_a, "Speaker Persona"),
        include_weakly_relevant=False,
    )
    target = await load_micro_persona_state(
        sessionmaker=sm,
        persona_match=_match(pid_b, "Target Persona"),
        include_weakly_relevant=False,
    )
    return speaker, target


# ---------------------------------------------------------------------------
# 1. Canonical stance → passes; no repair attempt fires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debate_canonical_stance_passes_without_repair(
    two_personas_in_db, admin_simulation_id,
) -> None:
    pid_a, pid_b = two_personas_in_db
    sm = get_sessionmaker()
    speaker, target = await _load_speaker_target(pid_a, pid_b)

    provider = MockProvider()
    provider.add_default(json.dumps({
        "argument": (
            "MICRO-TEST: I'm not sold on the pitch; I've been burned "
            "before by automation that loses control of brand pricing."
        ),
        "cited_evidence_excerpt": "wants brand control",
        "target_stance_after": MicroStance.SKEPTICAL.value,
    }))
    turn = await run_debate_turn(
        speaker=speaker, target=target,
        sessionmaker=sm, simulation_id=admin_simulation_id,
        provider=provider, model="test-model",
    )
    assert turn.output_audit_passed is True
    assert turn.target_stance_after == MicroStance.SKEPTICAL
    # Exactly one LLM call (no repair).
    assert len(provider.calls) == 1
    # Notes empty when first attempt is canonical.
    assert turn.output_audit_notes == []


# ---------------------------------------------------------------------------
# 2. Non-canonical first, canonical second → repair succeeds, audit passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debate_repair_succeeds_on_second_attempt(
    two_personas_in_db, admin_simulation_id,
) -> None:
    pid_a, pid_b = two_personas_in_db
    sm = get_sessionmaker()
    speaker, target = await _load_speaker_target(pid_a, pid_b)

    bad_then_good = [
        json.dumps({  # attempt 1: non-canonical (capitalized + suffix)
            "argument": (
                "MICRO-TEST: persona-voice argument citing my evidence."
            ),
            "cited_evidence_excerpt": "wants brand control",
            "target_stance_after": "Skeptical (no shift)",
        }),
        json.dumps({  # attempt 2: canonical, repair succeeds
            "argument": (
                "MICRO-TEST: persona-voice argument citing my evidence."
            ),
            "cited_evidence_excerpt": "wants brand control",
            "target_stance_after": MicroStance.SKEPTICAL.value,
        }),
    ]
    provider = MockProvider()
    provider.add_response_sequence("micro_debate_turn", bad_then_good)
    turn = await run_debate_turn(
        speaker=speaker, target=target,
        sessionmaker=sm, simulation_id=admin_simulation_id,
        provider=provider, model="test-model",
    )
    assert turn.output_audit_passed is True
    assert turn.target_stance_after == MicroStance.SKEPTICAL
    # Two LLM calls: original + repair.
    assert len(provider.calls) == 2
    # Notes record the repair.
    notes_blob = " | ".join(turn.output_audit_notes)
    assert "attempt_1" in notes_blob
    assert "Skeptical (no shift)" in notes_blob
    assert "repair succeeded" in notes_blob


# ---------------------------------------------------------------------------
# 3. Non-canonical twice → audit fails visibly, target stance preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debate_repair_exhausted_marks_failed_and_preserves_stance(
    two_personas_in_db, admin_simulation_id,
) -> None:
    pid_a, pid_b = two_personas_in_db
    sm = get_sessionmaker()
    speaker, target = await _load_speaker_target(pid_a, pid_b)
    target_stance_before = target.current_stance

    bad_twice = [
        json.dumps({
            "argument": "MICRO-TEST: speaker argument 1.",
            "cited_evidence_excerpt": "wants brand control",
            "target_stance_after": "STILL skeptical",
        }),
        json.dumps({
            "argument": "MICRO-TEST: speaker argument 2.",
            "cited_evidence_excerpt": "wants brand control",
            "target_stance_after": "skeptical_no_shift",  # also invalid
        }),
    ]
    provider = MockProvider()
    provider.add_response_sequence("micro_debate_turn", bad_twice)
    turn = await run_debate_turn(
        speaker=speaker, target=target,
        sessionmaker=sm, simulation_id=admin_simulation_id,
        provider=provider, model="test-model",
    )
    # Audit must surface the failure visibly.
    assert turn.output_audit_passed is False
    # Target stance is preserved (no silent coercion to a different value).
    assert turn.target_stance_after == target_stance_before
    # Two LLM calls fired.
    assert len(provider.calls) == 2
    # Notes call out exhaustion, both bad values appear, no
    # "repair succeeded" entry.
    notes_blob = " | ".join(turn.output_audit_notes)
    assert "STILL skeptical" in notes_blob
    assert "skeptical_no_shift" in notes_blob
    assert "repair exhausted" in notes_blob
    assert "repair succeeded" not in notes_blob


# ---------------------------------------------------------------------------
# 4. Forbidden language still fails even with valid enum
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debate_forbidden_language_fails_even_with_valid_enum(
    two_personas_in_db, admin_simulation_id,
) -> None:
    pid_a, pid_b = two_personas_in_db
    sm = get_sessionmaker()
    speaker, target = await _load_speaker_target(pid_a, pid_b)

    provider = MockProvider()
    provider.add_default(json.dumps({
        # `target_stance_after` IS canonical; forbidden language is in
        # the argument text. Audit must still fail.
        "argument": (
            "Amboras will dominate this market and the market reaction "
            "is positive."
        ),
        "cited_evidence_excerpt": "wants brand control",
        "target_stance_after": MicroStance.SKEPTICAL.value,
    }))
    turn = await run_debate_turn(
        speaker=speaker, target=target,
        sessionmaker=sm, simulation_id=admin_simulation_id,
        provider=provider, model="test-model",
    )
    assert turn.output_audit_passed is False
    notes_blob = " | ".join(turn.output_audit_notes)
    assert "forbidden language detected" in notes_blob
    # No repair attempt fired (the enum was valid).
    assert len(provider.calls) == 1
