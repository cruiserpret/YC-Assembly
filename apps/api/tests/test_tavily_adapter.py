"""Phase 8.2E — Tavily search/extract adapter tests.

Covers:
  - adapter REFUSES live mode without TAVILY_API_KEY
  - adapter REFUSES to ingest without compliance approval (mocked path)
  - adapter does NOT log / expose the api key (no string match in any
    captured fixture or repr)
  - mocked Tavily-shaped result normalizes into a SourceRecord payload
  - public Product-Hunt-style URL surfaced via Tavily is allowed (it is
    NOT a direct Product Hunt adapter — it is a Tavily result)
  - public review/forum/blog/pricing URLs normalize correctly
  - paywall / login / private result is REJECTED at normalize time
  - identity-shaped profile URL is REJECTED at normalize time
  - sensitive content is REJECTED at the redaction firewall
  - duplicate content dedupes via UNIQUE (source_kind, content_hash)
  - AdapterRunSummary tallies accepted / rejected / deduped
  - `live_network_used` is True only when ingest_live runs
  - the live-API smoke test is integration-marked + opt-in via
    `ASSEMBLY_RUN_LIVE_INGESTION_TESTS=true`

Unit-level tests inject a fake httpx-like client to avoid network
calls. The real httpx import lives only inside `tavily_adapter.py`
(drift test enforces).
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from assembly.db import get_sessionmaker
from assembly.models.adapter_status import AdapterComplianceStatus
from assembly.models.persona import SourceRecord
from assembly.pipeline.ingestion import (
    ComplianceError,
    ComplianceErrorCode,
    NormalizationRejection,
    RawSourcePayload,
    TavilyApiKeyMissing,
    TavilySearchExtractAdapter,
    register_or_update_adapter_status,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
async def isolated_tavily_status() -> AsyncIterator[None]:
    """Reset the Tavily status row before AND after each test that uses it."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name == "tavily_search_extract"
                )
            )
    yield
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name == "tavily_search_extract"
                )
            )


# ---------------------------------------------------------------------------
# 1) Adapter refuses live mode without TAVILY_API_KEY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_live_refuses_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    adapter = TavilySearchExtractAdapter(queries=["test query"])
    with pytest.raises(TavilyApiKeyMissing):
        await adapter.fetch_live()


# ---------------------------------------------------------------------------
# 2) Adapter refuses to ingest_mocked without compliance approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_mocked_refuses_without_compliance_approval(
    isolated_tavily_status,
) -> None:
    sessionmaker = get_sessionmaker()
    adapter = TavilySearchExtractAdapter(queries=["test"])
    with pytest.raises(ComplianceError) as excinfo:
        await adapter.ingest_mocked(sessionmaker=sessionmaker, salt="t")
    assert excinfo.value.code is ComplianceErrorCode.ADAPTER_NOT_REGISTERED


@pytest.mark.asyncio
async def test_ingest_live_refuses_without_compliance_approval(
    isolated_tavily_status,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the API key is present, an unregistered adapter must
    refuse the live path before any HTTP call."""
    sessionmaker = get_sessionmaker()
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")
    adapter = TavilySearchExtractAdapter(queries=["test"])
    with pytest.raises(ComplianceError) as excinfo:
        await adapter.ingest_live(sessionmaker=sessionmaker, salt="t")
    assert excinfo.value.code is ComplianceErrorCode.ADAPTER_NOT_REGISTERED


# ---------------------------------------------------------------------------
# 3) Adapter does not log / expose the api key
# ---------------------------------------------------------------------------


def test_adapter_repr_does_not_expose_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    SECRET = "tvly-secret-test-only-fake-value-001"
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    adapter = TavilySearchExtractAdapter(queries=["q"])
    # repr / str must not include the key.
    assert SECRET not in repr(adapter)
    assert SECRET not in str(adapter)
    # Class vars must not include the key.
    assert SECRET not in str(adapter.__class__.__dict__)


@pytest.mark.asyncio
async def test_fetch_live_failure_log_does_not_include_api_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the live HTTP call raises, the log line must NOT include the
    api key value."""
    SECRET = "tvly-test-fake-secret-002"
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)

    class _BoomClient:
        def __init__(self, key: str) -> None:
            # Intentionally ignore key — it's already in env; we just
            # want to make sure it's not echoed anywhere.
            self._key = key

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            raise RuntimeError("simulated network failure")

    def _factory(api_key: str) -> _BoomClient:
        return _BoomClient(api_key)

    adapter = TavilySearchExtractAdapter(
        queries=["q"], http_client_factory=_factory,
    )
    caplog.set_level(logging.WARNING)
    payloads = await adapter.fetch_live()
    # All queries failed → empty result list, but no exception.
    assert payloads == []
    # And no log line included the secret.
    for rec in caplog.records:
        assert SECRET not in rec.getMessage()
        assert SECRET not in str(getattr(rec, "args", ""))


# ---------------------------------------------------------------------------
# 4) Mocked Tavily response normalizes into a payload (full pipeline)
# ---------------------------------------------------------------------------


@pytest.fixture
async def approved_tavily(isolated_tavily_status) -> AsyncIterator[None]:
    sessionmaker = get_sessionmaker()
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name="tavily_search_extract",
        status="approved",
        memo_path="apps/api/docs/compliance/tavily_search_extract.md",
        approver="phase_8_2e_test",
        approved_at=datetime.now(UTC),
        notes="Phase 8.2E test fixture; not production approval.",
    )
    yield


@pytest.fixture
async def cleanup_tavily_source_records() -> AsyncIterator[None]:
    """Phase 8.2F.5 — SAFE cleanup.

    Earlier versions of this fixture deleted EVERY row with
    `source_kind='tavily_search_extract'`. That was unsafe: any live
    operator run (Phase 8.2E smoke test, Phase 8.2F.5 expansion) wrote
    rows with the same source_kind, and a subsequent integration test
    run would silently wipe them.

    This fixture now deletes ONLY rows whose metadata explicitly carries
    `test_fixture=true`. Operator-inserted rows (with
    `operator_run=true` and `test_fixture=false`) are preserved.

    The accompanying regression test in
    `tests/test_tavily_cleanup_safety.py` proves the rule on real
    Postgres.
    """
    await _delete_only_test_fixture_tavily_rows()
    yield
    await _delete_only_test_fixture_tavily_rows()


async def _delete_only_test_fixture_tavily_rows() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            # Delete only rows that carry metadata.test_fixture == true.
            # `JSONB ->> 'test_fixture'` returns text; `'true'` matches a
            # JSON boolean true.
            await session.execute(
                delete(SourceRecord).where(
                    SourceRecord.source_kind == "tavily_search_extract",
                    SourceRecord.metadata_["test_fixture"].astext == "true",
                )
            )


def _payload(
    *, url: str, content: str, query: str, rank: int,
    title: str | None = None, domain: str | None = None,
) -> RawSourcePayload:
    """Test-fixture payload builder. ALWAYS sets `test_fixture=true` so
    the safe cleanup fixture picks the row up. Live operator runs DO
    NOT use this helper; they go through the live adapter which sets
    `operator_run=true, test_fixture=false`."""
    return RawSourcePayload(
        source_url=url,
        captured_at=datetime.now(UTC),
        content=content,
        raw_handle=None,
        metadata={
            "query": query,
            "result_rank": rank,
            "title": title,
            "domain": domain or url.split("/")[2] if "://" in url else None,
            "tavily_score": 0.5,
            "published_date": None,
            # Phase 8.2F.5: tag every test fixture row.
            "test_fixture": True,
            "operator_run": False,
            "run_purpose": "test_fixture",
        },
    )


@pytest.mark.asyncio
async def test_mocked_run_writes_clean_source_records(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    payloads = [
        _payload(
            url="https://community.example.test/topic/aaa",
            content=(
                "merchants describe plugin bloat as the daily cost; "
                "consolidation that retains brand control is the recurring ask"
            ),
            query="Shopify merchants plugin bloat complaints",
            rank=0,
            title="Public discussion: plugin consolidation",
        ),
        _payload(
            url="https://blog.example.test/founder-cost",
            content=(
                "founders describe agency-design cost as a recurring monthly "
                "burden; many seek tooling that lets them retain final-pixel control"
            ),
            query="DTC founders agency cost store design complaints",
            rank=0,
            title="Founder cost",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.adapter_name == "tavily_search_extract"
    assert summary.live_network_used is False
    assert summary.accepted_count == 2
    assert summary.rejected_count == 0
    assert summary.deduped_count == 0

    async with sessionmaker() as session:
        # Filter to ONLY this test's fixture rows — operator-inserted
        # rows from prior live runs (Phase 8.2E / 8.2F.5) coexist in
        # the same source_kind namespace and must not be conflated.
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == "tavily_search_extract",
                    SourceRecord.metadata_["test_fixture"].astext == "true",
                )
            )
        ).scalars().all()
    assert len(rows) == 2
    for r in rows:
        assert r.compliance_tag == "public_api"
        assert r.ingested_by == "tavily_search_extract"
        assert r.pii_redaction_status == "redacted"
        assert r.sensitive_scan_status == "clean"
        assert r.user_handle_hash is None
        assert "query" in r.metadata_
        assert "result_rank" in r.metadata_


# ---------------------------------------------------------------------------
# 5) Public Product-Hunt-style URL via Tavily is allowed (NOT a dedicated PH
#    adapter — it's a Tavily result that goes through the normal pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_producthunt_style_url_via_tavily_is_accepted(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    payloads = [
        _payload(
            url="https://www.producthunt.example.test/posts/some-launch",
            content=(
                "public launch page describes commerce founders' brand "
                "control and automation concerns; no login wall; full text "
                "snippet visible to public visitors"
            ),
            query="ecommerce founders brand control automation concerns",
            rank=0,
            title="Some launch — Product Hunt",
            domain="producthunt.example.test",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 1
    # Crucially, the SOURCE_KIND is the Tavily one — not a dedicated
    # `producthunt_*` source kind. There is no Product Hunt adapter.
    assert summary.source_kind == "tavily_search_extract"


# ---------------------------------------------------------------------------
# 6) Public review/forum/blog/pricing URLs normalize correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_review_forum_blog_pricing_urls_all_normalize(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    payloads = [
        _payload(
            url="https://reviews.example.test/saas/store-builder/all",
            content=(
                "review aggregate excerpt: merchants discuss brand control "
                "and price sensitivity in public reviews"
            ),
            query="ecommerce founders brand control automation concerns",
            rank=0,
            title="Reviews aggregate",
        ),
        _payload(
            url="https://forum.example.test/threads/12345",
            content=(
                "forum thread excerpt: founders discuss switching costs "
                "and automation transparency requirements"
            ),
            query="DTC founders agency cost store design complaints",
            rank=1,
            title="Forum thread",
        ),
        _payload(
            url="https://blog.example.test/founder-notes",
            content=(
                "blog post excerpt: agency cost vs in-house tooling "
                "tradeoffs from a public founder writeup"
            ),
            query="DTC founders agency cost store design complaints",
            rank=2,
            title="Founder notes",
        ),
        _payload(
            url="https://example.test/pricing",
            content=(
                "public pricing page text: starter tier vs scale tier "
                "pricing structure documented for public reference"
            ),
            query="Shopify store automation pricing concerns",
            rank=3,
            title="Pricing",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 4
    assert summary.rejected_count == 0


# ---------------------------------------------------------------------------
# 7) Paywall / login / private / error pages are rejected at normalize time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("snippet", [
    "Subscribe to read the full article behind our paywall today",
    "Sign in to view this private discussion thread",
    "Members only — this article is premium content",
    "403 Forbidden — you do not have access to this resource",
    "Account required to read this thread; please sign in",
    "Login required to continue past this preview",
])
async def test_paywall_login_error_snippet_rejected(
    approved_tavily, cleanup_tavily_source_records, snippet: str,
) -> None:
    sessionmaker = get_sessionmaker()
    payloads = [
        _payload(
            url="https://example.test/some-article",
            content=snippet,
            query="ecommerce founders brand control automation concerns",
            rank=0,
            title="Locked content",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
    assert summary.rejection_reasons[0].reason_code == "PAYWALL_OR_LOGIN_WALL"


# ---------------------------------------------------------------------------
# 8) Identity-shaped profile URL is rejected at normalize time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://example.test/u/some_handle",
    "https://example.test/user/another_handle",
    "https://example.test/@yet_another_handle",
    "https://example.test/profile/deep_handle",
])
async def test_identity_shaped_profile_url_rejected(
    approved_tavily, cleanup_tavily_source_records, url: str,
) -> None:
    sessionmaker = get_sessionmaker()
    payloads = [
        _payload(
            url=url,
            content=(
                "public discussion content with no obvious paywall markers; "
                "this content is rejected because the URL is identity-shaped"
            ),
            query="ecommerce founders brand control automation concerns",
            rank=0,
            title="Some user page",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
    assert summary.rejection_reasons[0].reason_code == "IDENTITY_URL_REJECTED"


# ---------------------------------------------------------------------------
# 9) Sensitive content is rejected at the redaction firewall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensitive_content_rejected_at_redaction_firewall(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    # Content carries a phone number — sensitive filter rejects.
    payloads = [
        _payload(
            url="https://example.test/article",
            content=(
                "public article excerpt: please reach out at "
                "(555) 555-0199 for further commerce-merchant discussion"
            ),
            query="ecommerce founders brand control automation concerns",
            rank=0,
            title="Article",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    # Phone is redacted by stage-1; if no other sensitive markers remain,
    # the record can still be accepted. We expect REDACTION + acceptance.
    # The opposite case (race/ethnicity etc.) is exercised below.
    assert summary.accepted_count == 1
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == "tavily_search_extract"
                )
            )
        ).scalars().all()
    # Phone has been replaced with [REDACTED_PHONE] in the stored content.
    assert any("[REDACTED_PHONE]" in r.content for r in rows)


@pytest.mark.asyncio
async def test_race_ethnicity_content_rejected(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    payloads = [
        _payload(
            url="https://example.test/article",
            content=(
                "public excerpt: this commenter described their black "
                "ethnicity as part of their persona summary"
            ),
            query="ecommerce founders brand control automation concerns",
            rank=0,
            title="Article",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
    assert (
        summary.rejection_reasons[0].reason_code
        == "SENSITIVE_ATTRIBUTE_DETECTED"
    )


# ---------------------------------------------------------------------------
# 10) Duplicate content dedupes via UNIQUE (source_kind, content_hash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_content_dedupes(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    same_content = (
        "merchants describe plugin bloat as the daily cost; consolidation "
        "that retains brand control is the recurring ask in public threads"
    )
    payloads = [
        _payload(
            url="https://example.test/a",
            content=same_content,
            query="Shopify merchants plugin bloat complaints",
            rank=0,
            title="A",
        ),
        _payload(
            url="https://example.test/b",
            content=same_content,
            query="Shopify merchants plugin bloat complaints",
            rank=1,
            title="B",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 1
    assert summary.deduped_count == 1


# ---------------------------------------------------------------------------
# 11) AdapterRunSummary tallies accepted / rejected / deduped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_summary_tallies_all_outcomes(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    sessionmaker = get_sessionmaker()
    same = (
        "public excerpt: public commerce-merchant discussion of plugin "
        "consolidation tradeoffs and price sensitivity considerations"
    )
    payloads = [
        # 2 accepted (one is a duplicate of the other → 1 accept + 1 dedup)
        _payload(url="https://example.test/a", content=same, query="q", rank=0, title="A"),
        _payload(url="https://example.test/b", content=same, query="q", rank=1, title="B"),
        # 1 paywall reject
        _payload(
            url="https://example.test/c",
            content="Subscribe to read the full article behind our paywall",
            query="q", rank=2, title="C",
        ),
        # 1 identity-url reject
        _payload(
            url="https://example.test/u/handle",
            content=(
                "public profile-shaped url; content has no paywall markers "
                "but the url path indicates a personal profile"
            ),
            query="q", rank=3, title="D",
        ),
    ]
    adapter = TavilySearchExtractAdapter(
        queries=["q"], mocked_payloads=payloads,
    )
    summary = await adapter.ingest_mocked(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.accepted_count == 1
    assert summary.deduped_count == 1
    assert summary.rejected_count == 2
    codes = {r.reason_code for r in summary.rejection_reasons}
    assert codes == {"PAYWALL_OR_LOGIN_WALL", "IDENTITY_URL_REJECTED"}


# ---------------------------------------------------------------------------
# 12) live_network_used flag flips with ingest_live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_live_sets_live_network_used_true(
    approved_tavily,
    cleanup_tavily_source_records,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessionmaker = get_sessionmaker()

    captured_at = datetime.now(UTC)

    class _FakeResp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return self._payload

    class _FakeClient:
        def __init__(self, key: str) -> None:
            self._calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            self._calls += 1
            return _FakeResp({
                "results": [
                    {
                        "url": "https://blog.example.test/p/1",
                        "title": "Public blog excerpt",
                        "content": (
                            "public blog excerpt about plugin consolidation "
                            "tradeoffs and brand control concerns from merchants"
                        ),
                        "score": 0.7,
                    },
                ],
            })

    def factory(api_key: str) -> _FakeClient:
        return _FakeClient(api_key)

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-fake-key-only")
    # test_fixture=True so the safe cleanup fixture cleans this row up.
    adapter = TavilySearchExtractAdapter(
        queries=["q1"],
        http_client_factory=factory,
        test_fixture=True,
        run_purpose="test_ingest_live_flag",
    )
    summary = await adapter.ingest_live(
        sessionmaker=sessionmaker, salt="phase_8_2e_test_salt",
    )
    assert summary.live_network_used is True
    assert summary.fetched_count == 1
    assert summary.accepted_count == 1


# ---------------------------------------------------------------------------
# 13) Optional live smoke test — only runs when explicitly enabled
# ---------------------------------------------------------------------------


_LIVE_OPT_IN = (
    os.environ.get("ASSEMBLY_RUN_LIVE_INGESTION_TESTS", "").strip().lower()
    in ("1", "true", "yes")
)


@pytest.mark.skipif(
    not _LIVE_OPT_IN,
    reason=(
        "Live Tavily smoke test disabled by default. "
        "Set ASSEMBLY_RUN_LIVE_INGESTION_TESTS=true to opt in. "
        "Requires TAVILY_API_KEY and an approved adapter_compliance_status row."
    ),
)
@pytest.mark.asyncio
async def test_live_smoke_is_opt_in(
    approved_tavily, cleanup_tavily_source_records,
) -> None:
    """Tightly capped live smoke test. Runs only when the operator
    explicitly opts in via environment variable. Written defensively:
    even when this runs, no key string is asserted in any output."""
    sessionmaker = get_sessionmaker()
    if not os.environ.get("TAVILY_API_KEY"):
        pytest.skip("TAVILY_API_KEY not set in environment.")
    adapter = TavilySearchExtractAdapter()
    summary = await adapter.ingest_live(
        sessionmaker=sessionmaker,
        salt="phase_8_2e_live_smoke",
        accepted_cap=adapter.MAX_ACCEPTED,
    )
    assert summary.live_network_used is True
    # Sanity bounds.
    assert summary.accepted_count <= adapter.MAX_ACCEPTED
    assert summary.fetched_count >= 0
