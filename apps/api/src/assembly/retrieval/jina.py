"""Jina Reader extraction provider skeleton.

Jina Reader (https://r.jina.ai/<url>) returns clean markdown for any URL.
A free tier exists; with an API key you get higher rate limits. The provider
works keyless against the public endpoint — but we still gate construction
on JINA_API_KEY presence to keep configuration explicit.
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

_JINA_ENDPOINT = "https://r.jina.ai/"


class JinaExtractionProvider(ExtractionProvider):
    """Live Jina Reader extraction."""

    name: ClassVar[str] = "jina"

    def __init__(self, *, api_key: str | None = None) -> None:
        # Jina works keyless; we still require explicit configuration so
        # the user opts in.
        key = api_key or get_settings().jina_api_key
        if not key:
            raise LLMProviderError("JINA_API_KEY not configured")
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
        if snapshot is not None:
            from assembly.retrieval.extraction_provider import HttpxExtractionProvider
            return await HttpxExtractionProvider().extract(
                url, cutoff_date=cutoff_date, snapshot=snapshot
            )

        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "text/markdown",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{_JINA_ENDPOINT}{url}", headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("jina.extract.http_error: %s", e)
                raise LLMProviderError(f"jina extract failed: {e}") from e

        text = response.text
        return ExtractedPage(
            url=url,
            final_url=url,
            title=None,
            text=text,
            captured_at=datetime.now(UTC),
            truncated=False,
            source_kind="extracted",
            metadata={"provider": "jina"},
        )
