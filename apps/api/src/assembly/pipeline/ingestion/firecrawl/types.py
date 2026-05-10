"""Phase 8.3B — Firecrawl-extraction Pydantic types.

Closed schemas (`extra='forbid'`) for the public adapter surface. The
metadata schema deliberately restricts the persistable field set;
expanding the schema requires a memo update + status re-review.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FirecrawlExtractionMetadata(BaseModel):
    """Closed metadata schema for one Firecrawl extraction.

    Fields persisted alongside the extracted body; the closed enum of
    keys here matches Section 8 of the compliance memo
    (`docs/source_compliance/firecrawl.md`). Anything not listed is
    dropped at parse time.
    """

    model_config = ConfigDict(extra="forbid")

    requested_url: str = Field(min_length=1, max_length=2000)
    final_url: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=400)
    source_status_code: int | None = Field(default=None, ge=100, le=599)
    content_type: str | None = Field(default=None, max_length=120)
    page_lang: str | None = Field(default=None, max_length=16)
    robots_allowed: bool | None = None
    scraped_via: str = Field(default="firecrawl_v1_scrape", max_length=64)


class FirecrawlExtractedPage(BaseModel):
    """One Firecrawl extraction result, AFTER pre-store redaction.

    The adapter contract: `body_markdown` is the redacted, capped body.
    Callers persist this object via the existing
    `prepare_source_record_insert` pipeline (Phase 8.2C); the adapter
    itself never writes to the database.

    `truncated=True` indicates the upstream body exceeded `max_chars`
    and was cut to length with a sentinel marker.
    """

    model_config = ConfigDict(extra="forbid")

    requested_url: str = Field(min_length=1, max_length=2000)
    final_url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=400)
    body_markdown: str = Field(min_length=1, max_length=200_000)
    body_chars: int = Field(ge=1, le=200_000)
    captured_at: datetime
    truncated: bool = False
    metadata: FirecrawlExtractionMetadata


__all__ = [
    "FirecrawlExtractedPage",
    "FirecrawlExtractionMetadata",
]
