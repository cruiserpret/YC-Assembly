"""Phase 8.2C — mocked adapter(s).

This module is the ONLY place in `pipeline/ingestion/` that defines an
adapter. No live network code. No PRAW / httpx / requests / aiohttp /
selenium / playwright / firecrawl / tavily / brave / jina / bs4 /
scrapy / tweepy / googleapiclient — the no-drift test asserts none of
those imports appear anywhere in the package.

`MockRedditPublicAPIAdapter` ships at compliance status='draft' by
default. Its `ingest_mocked` cannot run unless an operator (or a test
fixture) flips its `adapter_compliance_status` row to 'approved' with
populated approver + approved_at fields. Phase 8.2C does NOT authorize
that flip in production — Reddit remains a candidate source pending
human terms review and commercial-use clarification.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from assembly.pipeline.ingestion.adapter_base import SourceAdapter
from assembly.pipeline.ingestion.run_summary import (
    NormalizedSourcePayload,
    RawSourcePayload,
)


class _RedditMetadata(BaseModel):
    """Per-record metadata shape the mocked Reddit adapter declares.
    Real adapters subclass `BaseModel` similarly; the framework's
    METADATA_SCHEMA contract surfaces shape mismatches early."""

    model_config = ConfigDict(extra="forbid")

    subreddit: str = Field(min_length=1, max_length=64)
    post_id: str | None = None
    score: int | None = None
    num_comments: int | None = None
    is_comment: bool = False


class MockRedditPublicAPIAdapter(SourceAdapter):
    """Mocked-only adapter — no network code, no API client, no keys.

    The class exists so the framework end-to-end can be tested:
    compliance gate → fetch_mocked → normalize → redact → insert →
    AdapterRunSummary. Real Reddit ingestion is Phase 8.2D and requires:

      1. signed compliance memo (Reddit memo currently 'draft')
      2. compliance review of Reddit's developer-platform terms,
         including commercial-use clarification
      3. operator flip of `adapter_compliance_status.status='approved'`
      4. a fetch_live() implementation that uses an officially-supported
         client (e.g. PRAW) under explicit rate limits

    None of those happen in 8.2C.
    """

    NAME: ClassVar[str] = "reddit_public_api_mock"
    SOURCE_KIND: ClassVar[str] = "reddit_public_api"
    COMPLIANCE_TAG: ClassVar[str] = "public_api"
    MEMO_PATH: ClassVar[str] = "apps/api/docs/compliance/reddit_public_api.md"
    METADATA_SCHEMA: ClassVar[type[BaseModel]] = _RedditMetadata

    def __init__(self, payloads: Sequence[RawSourcePayload] | None = None) -> None:
        super().__init__()
        # Tests can inject custom payloads; production-shape default
        # below is illustrative only and never reaches a database
        # without an explicit 'approved' compliance flip.
        self._payloads: list[RawSourcePayload] = (
            list(payloads) if payloads is not None
            else _default_sample_payloads()
        )

    def fetch_mocked(self) -> Sequence[RawSourcePayload]:
        """Return the test/fixture payloads. No network involvement."""
        return list(self._payloads)

    def normalize_payload(
        self, raw: RawSourcePayload,
    ) -> NormalizedSourcePayload:
        # Validate metadata shape against the declared schema. This
        # raises if a fixture / test passes mismatched metadata, which
        # is the right behavior — adapter run_summary records the
        # rejection via the base class try/except.
        validated = self.METADATA_SCHEMA.model_validate(raw.metadata)
        return NormalizedSourcePayload(
            source_url=raw.source_url,
            captured_at=raw.captured_at,
            content=raw.content,
            raw_handle=raw.raw_handle,
            metadata=validated.model_dump(mode="json"),
            language="en",
        )


# ---------------------------------------------------------------------------
# Default illustrative payloads (used when tests don't inject their own)
# ---------------------------------------------------------------------------


def _default_sample_payloads() -> list[RawSourcePayload]:
    """Hardcoded illustrative payloads. Three records: clean, contains
    @handle (must be redacted), contains email + phone (must be
    redacted). They illustrate adapter shape; tests will inject their
    own to exercise specific rejection paths.

    These payloads contain NO real handles, real names, real emails,
    real phones, real addresses, or real subreddit content. They are
    constructed specifically as test fixtures."""
    base_when = datetime.now(UTC) - timedelta(days=7)
    return [
        RawSourcePayload(
            source_url="https://example.test/r/shopify/comments/aaa",
            captured_at=base_when,
            content=(
                "agents portraying mid-volume merchants tended to resist "
                "autonomous AI taking over brand control on a live store"
            ),
            raw_handle="testfixture_user_a",
            metadata={
                "subreddit": "shopify",
                "post_id": "aaa",
                "score": 12,
                "num_comments": 4,
                "is_comment": False,
            },
        ),
        RawSourcePayload(
            source_url="https://example.test/r/shopify/comments/bbb",
            captured_at=base_when - timedelta(days=2),
            content=(
                "ping me at [test] @anothertestfixture_user when the "
                "starter price drops; my plugin stack is overwhelming"
            ),
            raw_handle="testfixture_user_b",
            metadata={
                "subreddit": "shopify",
                "post_id": "bbb",
                "score": 7,
                "num_comments": 2,
                "is_comment": False,
            },
        ),
        RawSourcePayload(
            source_url="https://example.test/r/ecommerce/comments/ccc",
            captured_at=base_when - timedelta(days=3),
            content=(
                "reach out to fixture-only-test@example.test or call "
                "(555) 555-0199 if anyone wants to compare consolidation"
            ),
            raw_handle="testfixture_user_c",
            metadata={
                "subreddit": "ecommerce",
                "post_id": "ccc",
                "score": 3,
                "num_comments": 1,
                "is_comment": False,
            },
        ),
    ]
