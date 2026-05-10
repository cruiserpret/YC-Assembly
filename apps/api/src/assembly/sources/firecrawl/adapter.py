"""Phase 8.5G.1 — Firecrawl page-extraction adapter.

Bounded, defensive, universal. Converts a small set of top-ranked
search-result URLs into clean text/markdown. Reuses the existing
Phase 8.2x ingestion-side `FirecrawlClient` for the actual HTTP
call so we don't duplicate retry / paywall / extraction-quality logic.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


_FIRECRAWL_SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_PAGES = 8
_DEFAULT_MAX_PAGES_PER_DOMAIN = 3


@dataclass(frozen=True)
class FirecrawlAdapterConfig:
    max_pages: int = _DEFAULT_MAX_PAGES
    max_pages_per_domain: int = _DEFAULT_MAX_PAGES_PER_DOMAIN
    timeout_s: float = _DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class FirecrawlExtractResult:
    """One extracted page. Returns CLEAN text (markdown stripped of
    chrome). NO PII fields, NO image URLs, NO author identifiers."""
    url: str
    domain: str
    title: str
    markdown: str
    text_length: int
    extra: dict[str, Any] = field(default_factory=dict)


def is_firecrawl_key_present() -> bool:
    return bool(os.environ.get("FIRECRAWL_API_KEY"))


class FirecrawlExtractClient:
    """Minimal Firecrawl scrape client. Construction does NOT make a
    network call. Only `extract_top_urls()` requires the key."""

    def __init__(
        self, config: FirecrawlAdapterConfig | None = None,
    ) -> None:
        self._config = config or FirecrawlAdapterConfig()

    @property
    def config(self) -> FirecrawlAdapterConfig:
        return self._config

    async def extract_top_urls(
        self, *, urls: list[str],
    ) -> list[FirecrawlExtractResult]:
        """Extract clean markdown from up to `max_pages` URLs.
        Enforces per-domain cap."""
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "FIRECRAWL_API_KEY missing from environment; "
                "FirecrawlExtractClient.extract_top_urls() refuses "
                "to run."
            )
        # Per-domain cap
        per_domain: dict[str, int] = {}
        capped: list[str] = []
        for u in urls:
            try:
                domain = urlsplit(u).netloc.lower()
            except Exception:
                domain = "unknown"
            if per_domain.get(domain, 0) >= self._config.max_pages_per_domain:
                continue
            capped.append(u)
            per_domain[domain] = per_domain.get(domain, 0) + 1
            if len(capped) >= self._config.max_pages:
                break

        results: list[FirecrawlExtractResult] = []
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
            for u in capped:
                try:
                    resp = await client.post(
                        _FIRECRAWL_SCRAPE_ENDPOINT,
                        headers=headers,
                        json={
                            "url": u,
                            "formats": ["markdown"],
                            "onlyMainContent": True,
                            "removeBase64Images": True,
                        },
                    )
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                data = resp.json()
                payload = (data.get("data") or {})
                md = (payload.get("markdown") or "").strip()
                meta = payload.get("metadata") or {}
                title = (meta.get("title") or "").strip()
                if not md or len(md) < 100:
                    continue
                # Truncate for prompt safety
                md = md[:8000]
                domain = urlsplit(u).netloc.lower()
                results.append(FirecrawlExtractResult(
                    url=u,
                    domain=domain,
                    title=title,
                    markdown=md,
                    text_length=len(md),
                    extra={},
                ))
        return results


def __dir__() -> list[str]:
    return [
        "FirecrawlAdapterConfig",
        "FirecrawlExtractClient",
        "FirecrawlExtractResult",
        "is_firecrawl_key_present",
    ]
