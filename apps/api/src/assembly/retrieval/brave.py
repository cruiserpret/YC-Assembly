"""Brave Search provider skeleton."""
from __future__ import annotations

import logging
from typing import ClassVar

import httpx

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.retrieval.search_provider import SearchProvider, SearchResult

logger = logging.getLogger(__name__)

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchProvider(SearchProvider):
    """Live Brave Search. Disabled unless BRAVE_SEARCH_API_KEY is set."""

    name: ClassVar[str] = "brave"

    def __init__(self, *, api_key: str | None = None) -> None:
        key = api_key or get_settings().brave_search_api_key
        if not key:
            raise LLMProviderError("BRAVE_SEARCH_API_KEY not configured")
        self._key = key

    async def search(
        self, query: str, *, max_results: int = 10
    ) -> list[SearchResult]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._key,
        }
        params = {"q": query, "count": max_results}
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(
                    _BRAVE_ENDPOINT, headers=headers, params=params
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("brave.search.http_error: %s", e)
                raise LLMProviderError(f"brave search failed: {e}") from e

        body = response.json()
        results: list[SearchResult] = []
        web_block = body.get("web", {}) or {}
        for i, hit in enumerate(web_block.get("results", [])):
            results.append(
                SearchResult(
                    url=hit.get("url", ""),
                    title=hit.get("title", ""),
                    snippet=hit.get("description", ""),
                    rank=i + 1,
                    raw=hit,
                )
            )
        return results[:max_results]
