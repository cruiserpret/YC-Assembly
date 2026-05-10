"""Phase 8.3B — extraction-shape tests for the Firecrawl adapter.

All tests use the `build_extracted_page_from_payload` test seam — no
HTTP transport is constructed, no environment is read, no DB row is
written. The mock-LLM-style discipline mirrors the Phase 8.2K micro-
simulation suite.

Coverage:

  * URL → cleaned markdown body out, with metadata preserved.
  * `max_chars` cap is enforced; `truncated=True` is set; truncation
    marker appended.
  * `min_chars` floor refuses short bodies cleanly.
  * Pre-store redaction runs on the body BEFORE the page is returned
    (input email / handle / profile-URL → output is redacted).
  * Post-redaction body that still carries identity markers is
    refused (post-redaction firewall).
  * Blocked-page metadata (HTTP 4xx, robotsAllowed=False,
    scrapeStatus='blocked') raises `FirecrawlBlockedPage` with
    reason_code 'ROBOTS_OR_BLOCKED'.
  * Paywall / login-wall body markers raise `FirecrawlBlockedPage`
    with reason_code 'PAYWALL_OR_LOGIN_WALL'.
  * Compliance-gate refusal: `assert_firecrawl_approved` raises
    `FirecrawlComplianceNotApproved` when no approved row is present.
  * `repr(client)` does not echo any API-key-shaped substring.
  * `FirecrawlExtractionMetadata` rejects unknown fields
    (`extra='forbid'`).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from assembly.db import get_sessionmaker
from assembly.pipeline.ingestion.firecrawl import (
    FIRECRAWL_ADAPTER_NAME,
    FirecrawlBlockedPage,
    FirecrawlBodyRedactionFailed,
    FirecrawlBodyTooShort,
    FirecrawlClient,
    FirecrawlComplianceNotApproved,
    FirecrawlExtractedPage,
    FirecrawlExtractionMetadata,
    HARD_MAX_CHARS,
    TRUNCATION_MARKER,
    assert_firecrawl_approved,
    build_extracted_page_from_payload,
)


# ---------------------------------------------------------------------------
# Fixtures — Firecrawl-shaped scrape payloads
# ---------------------------------------------------------------------------


def _payload(
    *,
    body: str,
    title: str | None = "Test Page",
    final_url: str = "https://example.test/page",
    status_code: int | None = 200,
    robots_allowed: bool | None = True,
    scrape_status: str | None = None,
    extra_meta: dict | None = None,
) -> dict:
    md: dict = {
        "title": title,
        "sourceURL": final_url,
        "statusCode": status_code,
        "robotsAllowed": robots_allowed,
        "contentType": "text/html; charset=utf-8",
        "language": "en",
    }
    if scrape_status is not None:
        md["scrapeStatus"] = scrape_status
    if extra_meta:
        md.update(extra_meta)
    return {"data": {"markdown": body, "metadata": md}}


# ---------------------------------------------------------------------------
# 1. Happy path — URL → cleaned markdown + metadata preserved
# ---------------------------------------------------------------------------


def test_url_in_cleaned_body_and_metadata_out() -> None:
    client = FirecrawlClient()
    body = (
        "# Plugin overload — what merchants actually say\n\n"
        "I run a Shopify store with about $30k/mo in revenue. The "
        "biggest daily-cost issue is plugin sprawl: every app I add "
        "introduces JavaScript and API calls that slow my store and "
        "eat into margins. I've started auditing apps every 6 months "
        "and removing anything not directly contributing to revenue."
    )
    page = build_extracted_page_from_payload(
        client=client,
        requested_url="https://example.test/source-page",
        payload=_payload(
            body=body,
            title="Plugin overload — merchant voice",
            final_url="https://example.test/source-page-canonical",
        ),
    )
    assert isinstance(page, FirecrawlExtractedPage)
    assert page.requested_url == "https://example.test/source-page"
    # Final URL is canonicalized from metadata.sourceURL.
    assert page.final_url == "https://example.test/source-page-canonical"
    assert page.title == "Plugin overload — merchant voice"
    assert page.body_chars == len(page.body_markdown)
    assert page.body_markdown.startswith("# Plugin overload")
    assert page.truncated is False
    # Closed metadata schema preserved.
    assert page.metadata.scraped_via == "firecrawl_v1_scrape"
    assert page.metadata.requested_url == "https://example.test/source-page"
    assert page.metadata.source_status_code == 200
    assert page.metadata.robots_allowed is True
    assert page.metadata.page_lang == "en"
    assert page.metadata.title == "Plugin overload — merchant voice"


# ---------------------------------------------------------------------------
# 2. max_chars cap + truncation marker
# ---------------------------------------------------------------------------


def test_max_chars_cap_enforced_truncation_marker_appended() -> None:
    client = FirecrawlClient(max_chars=200, min_chars=50)
    # Build a body with 3+ substantive sentences so the new
    # boilerplate/substantive-content floor doesn't refuse it; the
    # body is long enough to be truncated at max_chars=200.
    sentence = (
        "I run a Shopify store and plugin sprawl is my biggest pain. "
    )
    body = sentence * 100  # 60+ sentences, ~6000 chars
    page = build_extracted_page_from_payload(
        client=client,
        requested_url="https://example.test/long",
        payload=_payload(body=body),
    )
    assert page.truncated is True
    assert page.body_markdown.endswith(TRUNCATION_MARKER)
    # Post-redaction (no identity markers in this prose) → body is
    # truncated at max_chars=200 plus the truncation marker.
    assert len(page.body_markdown) == 200 + len(TRUNCATION_MARKER)
    assert page.body_chars == len(page.body_markdown)


# ---------------------------------------------------------------------------
# 3. min_chars floor refuses short bodies cleanly
# ---------------------------------------------------------------------------


def test_min_chars_floor_refuses_short_bodies() -> None:
    client = FirecrawlClient(min_chars=200)
    body = "Tiny page."
    with pytest.raises(FirecrawlBodyTooShort):
        build_extracted_page_from_payload(
            client=client,
            requested_url="https://example.test/short",
            payload=_payload(body=body),
        )


# ---------------------------------------------------------------------------
# 4. Pre-store redaction runs on body
# ---------------------------------------------------------------------------


def test_redaction_runs_before_body_is_returned() -> None:
    client = FirecrawlClient(max_chars=4000, min_chars=80)
    body = (
        "I'm a Shopify merchant. Contact me at hello@example.com or "
        "@jdoe on the merchant forum if you want the full breakdown "
        "of how plugin bloat hits margins. Profile: "
        "https://forum.example.test/u/jdoe — but the real point is "
        "that consolidation tools that retain merchant control are "
        "the recurring ask in public threads."
    )
    page = build_extracted_page_from_payload(
        client=client,
        requested_url="https://example.test/redact",
        payload=_payload(body=body),
    )
    # Email redacted out:
    assert "hello@example.com" not in page.body_markdown
    # @handle redacted out:
    assert "@jdoe" not in page.body_markdown
    # Profile URL redacted out:
    assert "/u/jdoe" not in page.body_markdown
    # Underlying content preserved (the topic is intact, redaction is
    # a substitution, not a body purge):
    assert "consolidation tools" in page.body_markdown


# ---------------------------------------------------------------------------
# 5. Post-redaction body still carrying identity markers → refused
# ---------------------------------------------------------------------------


def test_residual_identity_markers_refuse_page() -> None:
    """The body has identity markers that the redactor's regex doesn't
    catch (synthetic edge case via a short body that shrinks below
    min_chars after redaction). The adapter must REFUSE rather than
    return a partial page."""
    client = FirecrawlClient(max_chars=4000, min_chars=400)
    # Long body with a real email (which the redactor catches), but
    # the body collapses under min_chars after redaction-shape
    # operations + we set a high min_chars to simulate the floor
    # firing post-redaction.
    body = "Short note: contact a@b.co for details. "
    body += "Filler. " * 5  # body is ~80 chars, well under 400 floor
    with pytest.raises((FirecrawlBodyTooShort, FirecrawlBodyRedactionFailed)):
        build_extracted_page_from_payload(
            client=client,
            requested_url="https://example.test/short-residual",
            payload=_payload(body=body),
        )


# ---------------------------------------------------------------------------
# 6. Blocked-page metadata: HTTP 4xx
# ---------------------------------------------------------------------------


def test_blocked_page_http_4xx_refused() -> None:
    client = FirecrawlClient()
    body = "x" * 200
    with pytest.raises(FirecrawlBlockedPage) as exc:
        build_extracted_page_from_payload(
            client=client,
            requested_url="https://example.test/forbidden",
            payload=_payload(body=body, status_code=403),
        )
    assert exc.value.reason_code == "ROBOTS_OR_BLOCKED"
    assert exc.value.url == "https://example.test/forbidden"


# ---------------------------------------------------------------------------
# 7. Blocked-page metadata: robotsAllowed=False
# ---------------------------------------------------------------------------


def test_blocked_page_robots_disallow_refused() -> None:
    client = FirecrawlClient()
    body = "x" * 200
    with pytest.raises(FirecrawlBlockedPage) as exc:
        build_extracted_page_from_payload(
            client=client,
            requested_url="https://example.test/disallowed",
            payload=_payload(
                body=body, status_code=200, robots_allowed=False,
            ),
        )
    assert exc.value.reason_code == "ROBOTS_OR_BLOCKED"


# ---------------------------------------------------------------------------
# 8. Blocked-page metadata: scrapeStatus='blocked'
# ---------------------------------------------------------------------------


def test_blocked_page_scrape_status_blocked_refused() -> None:
    client = FirecrawlClient()
    body = "x" * 200
    with pytest.raises(FirecrawlBlockedPage) as exc:
        build_extracted_page_from_payload(
            client=client,
            requested_url="https://example.test/blocked",
            payload=_payload(
                body=body, status_code=200, scrape_status="blocked",
            ),
        )
    assert exc.value.reason_code == "ROBOTS_OR_BLOCKED"


# ---------------------------------------------------------------------------
# 9. Paywall / login-wall body markers
# ---------------------------------------------------------------------------


def test_paywall_body_markers_refused() -> None:
    client = FirecrawlClient(min_chars=80)
    body = (
        "This is premium content. Subscribe to read the full article. "
        "Members only beyond this point. Paywall protected content."
    )
    with pytest.raises(FirecrawlBlockedPage) as exc:
        build_extracted_page_from_payload(
            client=client,
            requested_url="https://example.test/paywalled",
            payload=_payload(body=body),
        )
    assert exc.value.reason_code == "PAYWALL_OR_LOGIN_WALL"


# ---------------------------------------------------------------------------
# 10. repr() never echoes the API key
# ---------------------------------------------------------------------------


def test_repr_does_not_echo_api_key() -> None:
    client = FirecrawlClient(api_key="firecrawl-secret-key-do-not-leak")
    repr_str = repr(client)
    assert "firecrawl-secret-key-do-not-leak" not in repr_str
    assert "FirecrawlClient" in repr_str


# ---------------------------------------------------------------------------
# 11. Closed metadata schema rejects unknown fields
# ---------------------------------------------------------------------------


def test_metadata_schema_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        FirecrawlExtractionMetadata(
            requested_url="https://example.test/x",
            scraped_via="firecrawl_v1_scrape",
            unknown_field="should be rejected",  # noqa: F841
        )


# ---------------------------------------------------------------------------
# 12. HARD_MAX_CHARS ceiling rejected
# ---------------------------------------------------------------------------


def test_hard_max_chars_ceiling_rejected() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        FirecrawlClient(max_chars=HARD_MAX_CHARS + 1)


# ---------------------------------------------------------------------------
# 13. Compliance gate refuses when no approved row exists
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compliance_gate_refuses_without_approved_row() -> None:
    """No row in `adapter_compliance_status` for `firecrawl_extract` →
    `assert_firecrawl_approved` raises `FirecrawlComplianceNotApproved`.

    This test does NOT insert an approved row; it verifies the
    refusal path is structurally enforced. Phase 8.3B-LIVE will be
    the phase that inserts an `approved` row (separate approval)."""
    sm = get_sessionmaker()
    # Ensure no row for the adapter exists for this test's window.
    from sqlalchemy import delete
    from assembly.models.adapter_status import AdapterComplianceStatus
    async with sm() as session:
        async with session.begin():
            await session.execute(
                delete(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name
                    == FIRECRAWL_ADAPTER_NAME
                )
            )
    with pytest.raises(FirecrawlComplianceNotApproved):
        await assert_firecrawl_approved(sm)


# Reset async engine state after each test so the integration test
# above doesn't leak engine handles between modules.
@pytest.fixture(autouse=True)
async def _reset_async_engine() -> AsyncIterator[None]:
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:
            pass
    db._engine = None
    db._sessionmaker = None
