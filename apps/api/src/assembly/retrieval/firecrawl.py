"""Firecrawl extraction provider skeleton.

Firecrawl is purpose-built for LLM-grade clean text extraction (handles JS,
crawls multiple pages, returns markdown). Disabled unless FIRECRAWL_API_KEY
is set; the httpx fallback covers most static pages without it.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import httpx

from assembly.config import get_settings
from assembly.llm.errors import CutoffViolationError, LLMProviderError
from assembly.retrieval.extraction_provider import ExtractedPage, ExtractionProvider

logger = logging.getLogger(__name__)

_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


class FirecrawlExtractionProvider(ExtractionProvider):
    """Live Firecrawl extraction. Honors cutoff_date the same way as
    HttpxExtractionProvider — refuses live extraction when cutoff is set
    without a snapshot."""

    name: ClassVar[str] = "firecrawl"

    def __init__(self, *, api_key: str | None = None) -> None:
        key = api_key or get_settings().firecrawl_api_key
        if not key:
            raise LLMProviderError("FIRECRAWL_API_KEY not configured")
        self._key = key

    async def extract(
        self,
        url: str,
        *,
        cutoff_date: date | None = None,
        snapshot: str | Path | None = None,
    ) -> ExtractedPage:
        if cutoff_date is not None and snapshot is None:
            raise CutoffViolationError(
                f"refusing to live-extract {url!r}: simulation has "
                f"evidence_cutoff_date={cutoff_date} but no snapshot was provided."
            )

        # Snapshot path: fall back to httpx for the same behavior as
        # HttpxExtractionProvider so the rest of the pipeline doesn't have
        # to special-case snapshots per provider.
        if snapshot is not None:
            from assembly.retrieval.extraction_provider import HttpxExtractionProvider
            return await HttpxExtractionProvider().extract(
                url, cutoff_date=cutoff_date, snapshot=snapshot
            )

        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        body = {"url": url, "formats": ["markdown"]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    _FIRECRAWL_ENDPOINT, headers=headers, json=body
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("firecrawl.extract.http_error: %s", e)
                raise LLMProviderError(f"firecrawl extract failed: {e}") from e

        payload = response.json().get("data", {}) or {}
        text = payload.get("markdown") or payload.get("content") or ""
        title = (payload.get("metadata") or {}).get("title")
        final_url = (payload.get("metadata") or {}).get("sourceURL", url)

        return ExtractedPage(
            url=url,
            final_url=final_url,
            title=title,
            text=text,
            captured_at=datetime.now(UTC),
            truncated=False,
            source_kind="extracted",
            metadata={"provider": "firecrawl"},
        )
