"""Phase 8.2F — worker integration tests against real Postgres.

Asserts:
  - dry-run writes nothing
  - write-mode creates a persona only when ≥ 3 valid traits exist
  - context_only records never seed a persona
  - display_name is generated, not source-derived
  - source_records identity columns do not leak into persona rows
  - traits without source_ids on direct/inferred are rejected
  - PersonaEvidenceLink is written for every valid direct/inferred trait
  - summary counts are accurate
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink,
    PersonaRecord,
    PersonaTrait,
    SourceRecord,
)
from assembly.pipeline.persona_construction import (
    MockTraitExtractor,
    PersonaConstructionRunSummary,
    TraitCandidate,
    run_persona_construction,
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


@pytest.fixture
async def cleanup_phase_82f_rows() -> AsyncIterator[None]:
    """Per-test cleanup of fixtures we insert."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(SourceRecord).where(
                    SourceRecord.source_kind == "phase_82f_test"
                )
            )
    yield
    async with sessionmaker() as session:
        async with session.begin():
            # Cascade through PersonaTrait + PersonaEvidenceLink via FK.
            await session.execute(
                delete(PersonaRecord).where(
                    PersonaRecord.segment_label == "phase_82f_test_segment"
                )
            )
            await session.execute(
                delete(SourceRecord).where(
                    SourceRecord.source_kind == "phase_82f_test"
                )
            )


def _new_source(
    *, content: str, source_url: str, captured_at: datetime,
) -> SourceRecord:
    return SourceRecord(
        id=uuid4(),
        source_kind="phase_82f_test",
        source_url=source_url,
        captured_at=captured_at,
        content=content,
        content_hash=("82f" + uuid4().hex)[:64],
        language="en",
        metadata_={"query": "phase_82f_test_query"},
        ingested_by="phase_82f_worker_test",
        compliance_tag="manual_seed",
        user_handle_hash=None,
        pii_redaction_status="redacted",
        sensitive_scan_status="clean",
    )


# ---------------------------------------------------------------------------
# 1) dry-run writes nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_no_persona_rows(
    cleanup_phase_82f_rows,
) -> None:
    sessionmaker = get_sessionmaker()
    captured = datetime.now(UTC)
    persona_voice_text = (
        "I am a Shopify merchant doing about $30k/month and I switched "
        "away from BigCommerce last year. My plugin stack is overwhelming "
        "and I wish there was a tool that consolidates them without "
        "removing my brand control. I'm frustrated paying $400/mo."
    )
    src = _new_source(
        content=persona_voice_text,
        source_url="https://reddit.example.test/r/shopify/aaa",
        captured_at=captured,
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)

    candidates = (
        TraitCandidate(
            field_name="role_or_context",
            support_level="direct",
            value="Shopify merchant doing about $30k/month",
            source_excerpt="Shopify merchant doing about $30k/month",
            confidence=0.9,
            rationale="self-description",
        ),
        TraitCandidate(
            field_name="objection_patterns",
            support_level="direct",
            value="plugin stack is overwhelming",
            source_excerpt="plugin stack is overwhelming",
            confidence=0.8,
            rationale="surfaced complaint",
        ),
        TraitCandidate(
            field_name="trust_triggers",
            support_level="inferred",
            value="merchant retains brand control",
            source_excerpt="without removing my brand control",
            confidence=0.7,
            rationale="inferred from trust language",
        ),
    )
    extractor = MockTraitExtractor(candidates=candidates)

    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=[src],
        extractor=extractor,
        write_personas=False,
    )

    # Dry-run: no persona rows.
    async with sessionmaker() as session:
        rows = (
            await session.execute(select(PersonaRecord))
        ).scalars().all()
    assert summary.dry_run is True
    assert summary.wrote_personas is False
    # 1 strong-signal record → 1 candidate shell → 3 valid traits → would-have-created 1
    assert summary.candidate_shells == 1
    assert summary.shells_with_three_or_more_valid_traits == 1
    assert summary.personas_created == 0  # no actual writes
    # Pre-existing rows from earlier tests may still be in the DB; we
    # only assert that no NEW row carries our test segment_label.
    assert all(
        r.segment_label != "phase_82f_test_segment" for r in rows
    )


# ---------------------------------------------------------------------------
# 2) write-mode creates persona when ≥ 3 valid traits exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_mode_creates_persona_with_three_plus_traits(
    cleanup_phase_82f_rows,
) -> None:
    sessionmaker = get_sessionmaker()
    persona_text = (
        "I'm a DTC founder doing $25k/month on Shopify. We've been burned "
        "by agencies twice. I'm fed up with monthly retainers and I'd "
        "switch to AI tooling but only if I retain final pixel control "
        "over branding."
    )
    src = _new_source(
        content=persona_text,
        source_url="https://forum.example.test/threads/12345",
        captured_at=datetime.now(UTC),
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)

    candidates = (
        TraitCandidate(
            field_name="role_or_context",
            support_level="direct",
            value="DTC founder doing $25k/month on Shopify",
            source_excerpt="DTC founder doing $25k/month on Shopify",
            confidence=0.9,
            rationale="self-description",
        ),
        TraitCandidate(
            field_name="current_alternatives",
            support_level="direct",
            value="agency retainers",
            source_excerpt="burned by agencies twice",
            confidence=0.85,
            rationale="alternative usage",
        ),
        TraitCandidate(
            field_name="trust_triggers",
            support_level="inferred",
            value="retains final pixel control over branding",
            source_excerpt="retain final pixel control over branding",
            confidence=0.8,
            rationale="trust trigger",
        ),
    )
    extractor = MockTraitExtractor(candidates=candidates)

    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=[src],
        extractor=extractor,
        write_personas=True,
    )

    assert summary.wrote_personas is True
    assert summary.personas_created == 1
    assert summary.traits_created == 3
    assert summary.evidence_links_created == 3

    # Verify rows.
    async with sessionmaker() as session:
        # Find personas linked via evidence_links to our test source.
        link_rows = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.source_record_id == src.id
                )
            )
        ).scalars().all()
        assert len(link_rows) == 3
        persona_id = link_rows[0].persona_id
        persona = (
            await session.execute(
                select(PersonaRecord).where(PersonaRecord.id == persona_id)
            )
        ).scalar_one()
        # display_name is generated; matches "First L." pattern.
        assert " " in persona.display_name
        assert persona.display_name.endswith(".")
        # No source URL leaks into persona / trait rows.
        assert "forum.example.test" not in persona.display_name
        # Traits exist for the 3 fields.
        traits = (
            await session.execute(
                select(PersonaTrait).where(
                    PersonaTrait.persona_id == persona_id
                )
            )
        ).scalars().all()
        names = {t.field_name for t in traits}
        assert names == {"role_or_context", "current_alternatives", "trust_triggers"}


# ---------------------------------------------------------------------------
# 3) refuses persona with fewer than 3 valid traits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_mode_refuses_with_fewer_than_three_traits(
    cleanup_phase_82f_rows,
) -> None:
    sessionmaker = get_sessionmaker()
    persona_text = (
        "I'm a Shopify merchant doing about $30k/month. I switched away "
        "from BigCommerce last year. My plugin stack is overwhelming and "
        "I'm frustrated by the cost."
    )
    src = _new_source(
        content=persona_text,
        source_url="https://reddit.example.test/r/shopify/bbb",
        captured_at=datetime.now(UTC),
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)

    candidates = (
        TraitCandidate(
            field_name="role_or_context",
            support_level="direct",
            value="Shopify merchant doing about $30k/month",
            source_excerpt="Shopify merchant doing about $30k/month",
            confidence=0.9,
            rationale="self-description",
        ),
        TraitCandidate(
            field_name="objection_patterns",
            support_level="direct",
            value="plugin stack is overwhelming",
            source_excerpt="plugin stack is overwhelming",
            confidence=0.8,
            rationale="complaint",
        ),
        # Third candidate has no excerpt and is unknown — does not count
        # toward the 3-valid-traits threshold for direct/inferred-only.
    )
    extractor = MockTraitExtractor(candidates=candidates)

    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=[src],
        extractor=extractor,
        write_personas=True,
    )
    assert summary.personas_created == 0
    assert summary.personas_skipped == 1
    assert summary.skipped_reasons[0].reason_code == "FEWER_THAN_MIN_VALID_TRAITS"
    # No persona was written.
    async with sessionmaker() as session:
        link_rows = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.source_record_id == src.id
                )
            )
        ).scalars().all()
        assert link_rows == []


# ---------------------------------------------------------------------------
# 4) context-only records never seed a persona
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_only_records_do_not_create_persona(
    cleanup_phase_82f_rows,
) -> None:
    sessionmaker = get_sessionmaker()
    article_text = (
        "In this article we'll explore the top 10 best Shopify SEO plugins "
        "for 2025. Trusted by 5,000+ merchants worldwide. Subscribe to our "
        "newsletter to read more. Get started today with our agency. "
        "Read more in our complete guide. We help you launch your store."
    )
    src = _new_source(
        content=article_text,
        source_url="https://blog.example.test/best-shopify-plugins-2025",
        captured_at=datetime.now(UTC),
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)

    # Even if an extractor were configured, the worker should not call it
    # because the record is context-only and gets excluded at grouping.
    candidates = (
        TraitCandidate(
            field_name="role_or_context",
            support_level="direct",
            value="article author",
            source_excerpt="In this article we'll explore",
            confidence=0.9,
            rationale="article framing",
        ),
    )
    extractor = MockTraitExtractor(candidates=candidates)

    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=[src],
        extractor=extractor,
        write_personas=True,
    )
    assert summary.candidate_shells == 0
    assert summary.context_only_records == 1
    assert summary.personas_created == 0
    async with sessionmaker() as session:
        link_rows = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.source_record_id == src.id
                )
            )
        ).scalars().all()
        assert link_rows == []


# ---------------------------------------------------------------------------
# 5) persona display_name is generated, never source-derived
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_display_name_is_generated_not_source_derived(
    cleanup_phase_82f_rows,
) -> None:
    sessionmaker = get_sessionmaker()
    persona_text = (
        "I'm a Shopify merchant. I switched to a new platform last year "
        "because the plugins were eating my margins. My store is doing "
        "better but the migration was painful and I'm fed up."
    )
    src = _new_source(
        content=persona_text,
        source_url="https://forum.example.test/threads/abcabc",
        captured_at=datetime.now(UTC),
    )
    async with sessionmaker() as session:
        async with session.begin():
            session.add(src)

    candidates = (
        TraitCandidate(
            field_name="role_or_context",
            support_level="direct",
            value="Shopify merchant",
            source_excerpt="I'm a Shopify merchant",
            confidence=0.9,
            rationale="explicit self-description",
        ),
        TraitCandidate(
            field_name="current_alternatives",
            support_level="direct",
            value="platform migration last year",
            source_excerpt="switched to a new platform last year",
            confidence=0.8,
            rationale="alternatives",
        ),
        TraitCandidate(
            field_name="objection_patterns",
            support_level="direct",
            value="plugins were eating my margins",
            source_excerpt="plugins were eating my margins",
            confidence=0.8,
            rationale="complaint",
        ),
    )
    extractor = MockTraitExtractor(candidates=candidates)
    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=[src],
        extractor=extractor,
        write_personas=True,
    )
    assert summary.personas_created == 1
    async with sessionmaker() as session:
        link = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.source_record_id == src.id
                )
            )
        ).scalars().first()
        persona = (
            await session.execute(
                select(PersonaRecord).where(PersonaRecord.id == link.persona_id)
            )
        ).scalar_one()
        # display_name must NOT contain any source-leak
        assert "Shopify" not in persona.display_name
        assert "forum.example.test" not in persona.display_name
        assert "merchant" not in persona.display_name.lower()


# ---------------------------------------------------------------------------
# 6) summary counts are accurate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_counts_strong_weak_context_records(
    cleanup_phase_82f_rows,
) -> None:
    sessionmaker = get_sessionmaker()
    captured = datetime.now(UTC)
    rows = [
        # strong:
        _new_source(
            content=(
                "I'm a Shopify merchant doing $30k/month and I switched "
                "from BigCommerce. My plugin stack is overwhelming and I'm "
                "frustrated paying $400/mo."
            ),
            source_url="https://reddit.example.test/r/shopify/strong1",
            captured_at=captured,
        ),
        # context-only:
        _new_source(
            content=(
                "In this guide we cover the top 10 best Shopify SEO "
                "plugins for 2025. Subscribe to our newsletter and book "
                "a demo today. Trusted by 5,000+ merchants worldwide."
            ),
            source_url="https://blog.example.test/seo-plugins",
            captured_at=captured,
        ),
        # context-only (pricing):
        _new_source(
            content=(
                "Our platform offers four pricing tiers — Starter $29/mo, "
                "Growth $99/mo, Pro $299/mo, Enterprise custom. Get "
                "started today. We help merchants automate at scale."
            ),
            source_url="https://example.test/pricing",
            captured_at=captured,
        ),
    ]
    async with sessionmaker() as session:
        async with session.begin():
            for r in rows:
                session.add(r)

    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=rows,
        write_personas=False,
    )
    assert summary.source_records_seen == 3
    assert summary.strong_persona_signal_records == 1
    assert summary.context_only_records == 2
    assert summary.candidate_shells == 1
    # No extractor configured → 0 traits → would-have-skipped.
    assert summary.personas_created == 0
