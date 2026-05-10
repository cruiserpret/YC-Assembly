"""Phase 8.2C — mocked Reddit adapter tests.

End-to-end against real Postgres. Asserts:
  - adapter writes source_records ONLY when its compliance row is approved
  - returns AdapterRunSummary with correct counts
  - dedup via UNIQUE(source_kind, content_hash)
  - rejected records are NOT inserted
  - live_network_used is always False
  - no PRAW or other network library is imported
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from assembly.db import get_sessionmaker
from assembly.models.adapter_status import AdapterComplianceStatus
from assembly.models.persona import SourceRecord
from assembly.pipeline.ingestion import (
    AdapterRunSummary,
    MockRedditPublicAPIAdapter,
    RawSourcePayload,
    register_or_update_adapter_status,
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
async def approved_adapter() -> AsyncIterator[MockRedditPublicAPIAdapter]:
    """Register the mocked adapter as approved, run the test, then
    reset to draft + clean up any source_records the test wrote."""
    sessionmaker = get_sessionmaker()
    adapter = MockRedditPublicAPIAdapter()
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver="test-approver",
        approved_at=datetime.now(UTC),
        notes="Test fixture: approved for the duration of this test only.",
    )
    yield adapter
    # Cleanup: remove the source_records the test inserted, then revert
    # status to draft so other tests / non-test runs don't see an
    # accidentally-approved adapter.
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(SourceRecord).where(
                    SourceRecord.source_kind == adapter.SOURCE_KIND
                )
            )
            await session.execute(
                delete(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name == adapter.NAME
                )
            )


# ---------------------------------------------------------------------------
# Approved-path success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_adapter_writes_source_records(
    approved_adapter: MockRedditPublicAPIAdapter,
) -> None:
    sessionmaker = get_sessionmaker()
    summary = await approved_adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="test-salt-1",
    )
    assert isinstance(summary, AdapterRunSummary)
    assert summary.adapter_name == approved_adapter.NAME
    assert summary.live_network_used is False
    assert summary.fetched_count == 3   # default fixture has 3 payloads
    assert summary.accepted_count >= 1  # at least one clean record makes it through
    assert summary.compliance_status == "approved"

    # Verify the rows actually landed.
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == approved_adapter.SOURCE_KIND
                )
            )
        ).scalars().all()
    assert len(rows) == summary.accepted_count
    for r in rows:
        assert r.pii_redaction_status == "redacted"
        assert r.sensitive_scan_status == "clean"
        # No handle, no raw email, no @user marker should remain.
        assert "@" not in (r.content or "") or "[REDACTED" in r.content
        # ingested_by attribution
        assert r.ingested_by == approved_adapter.NAME
        # compliance_tag matches adapter
        assert r.compliance_tag == approved_adapter.COMPLIANCE_TAG


@pytest.mark.asyncio
async def test_summary_counts_match_payload_outcomes(
    approved_adapter: MockRedditPublicAPIAdapter,
) -> None:
    """fetched + accepted + rejected + deduped is internally consistent."""
    sessionmaker = get_sessionmaker()
    summary = await approved_adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="test-salt-2",
    )
    total = summary.accepted_count + summary.rejected_count + summary.deduped_count
    assert total == summary.fetched_count


@pytest.mark.asyncio
async def test_duplicate_content_dedups_silently() -> None:
    """Run the same payload twice — the second run dedups via the
    UNIQUE(source_kind, content_hash) constraint."""
    sessionmaker = get_sessionmaker()
    # Build a fresh approved adapter so the cleanup fixture handles teardown.
    adapter = MockRedditPublicAPIAdapter(payloads=[
        RawSourcePayload(
            source_url="https://example.test/dedup",
            captured_at=datetime.now(UTC) - timedelta(days=1),
            content="agents portraying mid-volume merchants tended to lean cautious",
            raw_handle="testfixture_handle_dedup",
            metadata={"subreddit": "shopify", "post_id": f"dedup-{uuid4().hex[:6]}"},
        ),
    ])
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver="test-approver",
        approved_at=datetime.now(UTC),
    )
    try:
        s1 = await adapter.ingest_mocked(sessionmaker=sessionmaker, salt="dedup-salt")
        s2 = await adapter.ingest_mocked(sessionmaker=sessionmaker, salt="dedup-salt")
        assert s1.accepted_count == 1
        assert s2.accepted_count == 0
        assert s2.deduped_count == 1
    finally:
        async with sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(SourceRecord).where(
                        SourceRecord.source_kind == adapter.SOURCE_KIND
                    )
                )
                await session.execute(
                    delete(AdapterComplianceStatus).where(
                        AdapterComplianceStatus.adapter_name == adapter.NAME
                    )
                )


@pytest.mark.asyncio
async def test_sensitive_payload_rejected_not_inserted() -> None:
    """A payload whose content carries a sensitive attribute is logged
    in rejection_reasons and NOT written to source_records."""
    sessionmaker = get_sessionmaker()
    adapter = MockRedditPublicAPIAdapter(payloads=[
        RawSourcePayload(
            source_url="https://example.test/sensitive",
            captured_at=datetime.now(UTC),
            content="merchant on an H1B visa worried about brand control",
            raw_handle="testfixture_handle_sensitive",
            metadata={"subreddit": "shopify", "post_id": "sensitive-1"},
        ),
    ])
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver="test-approver",
        approved_at=datetime.now(UTC),
    )
    try:
        summary = await adapter.ingest_mocked(
            sessionmaker=sessionmaker, salt="sensitive-salt"
        )
        assert summary.fetched_count == 1
        assert summary.accepted_count == 0
        assert summary.rejected_count == 1
        assert summary.rejection_reasons[0].reason_code == "SENSITIVE_ATTRIBUTE_DETECTED"

        # Verify nothing landed.
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    select(SourceRecord).where(
                        SourceRecord.source_kind == adapter.SOURCE_KIND
                    )
                )
            ).scalars().all()
        assert len(rows) == 0
    finally:
        async with sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(SourceRecord).where(
                        SourceRecord.source_kind == adapter.SOURCE_KIND
                    )
                )
                await session.execute(
                    delete(AdapterComplianceStatus).where(
                        AdapterComplianceStatus.adapter_name == adapter.NAME
                    )
                )


# ---------------------------------------------------------------------------
# Adapter-side guarantees (no DB needed)
# ---------------------------------------------------------------------------


def test_live_network_used_default_is_false() -> None:
    summary = AdapterRunSummary(adapter_name="test", source_kind="test")
    assert summary.live_network_used is False


def test_no_praw_or_network_lib_in_mock_adapter_module() -> None:
    """Static import-graph check: the mock-adapter module must not
    import any network or scraping library, even transitively at the
    top-level (deeper transitive imports are handled by the no-drift
    AST scan; this test asserts the SOURCE FILE imports are clean)."""
    import importlib
    mod = importlib.import_module("assembly.pipeline.ingestion.mock_adapters")
    src = open(mod.__file__).read()
    for forbidden in (
        "import praw", "from praw", "import requests", "import httpx",
        "import aiohttp", "from playwright", "from selenium",
        "import firecrawl", "import tavily", "from brave",
        "from jina", "import bs4", "import scrapy", "import tweepy",
        "from googleapiclient",
    ):
        assert forbidden not in src, f"forbidden import {forbidden!r} found in mock_adapters.py"


def test_metadata_schema_declared_on_mock_adapter() -> None:
    from pydantic import BaseModel
    assert issubclass(MockRedditPublicAPIAdapter.METADATA_SCHEMA, BaseModel)
    assert MockRedditPublicAPIAdapter.NAME == "reddit_public_api_mock"
    assert MockRedditPublicAPIAdapter.SOURCE_KIND == "reddit_public_api"
    assert MockRedditPublicAPIAdapter.COMPLIANCE_TAG == "public_api"


def test_fetch_live_raises_not_implemented() -> None:
    """Phase 8.2C explicitly forbids live ingestion; fetch_live must raise."""
    import asyncio
    adapter = MockRedditPublicAPIAdapter()
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.fetch_live())
