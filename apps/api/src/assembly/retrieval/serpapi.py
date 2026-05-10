"""SerpAPI provider skeleton."""
from __future__ import annotations

import logging
from typing import ClassVar

import httpx

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.retrieval.search_provider import SearchProvider, SearchResult

logger = logging.getLogger(__name__)

_SERPAPI_ENDPOINT = "https://serpapi.com/search"


class SerpAPISearchProvider(SearchProvider):
    """Live SerpAPI (Google search). Disabled unless SERPAPI_API_KEY is set."""

    name: ClassVar[str] = "serpapi"

    def __init__(self, *, api_key: str | None = None) -> None:
        key = api_key or get_settings().serpapi_api_key
        if not key:
            raise LLMProviderError("SERPAPI_API_KEY not configured")
        self._key = key

    async def search(
        self, query: str, *, max_results: int = 10
    ) -> list[SearchResult]:
        params = {
            "engine": "google",
            "q": query,
            "api_key": self._key,
            "num": max_results,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(_SERPAPI_ENDPOINT, params=params)
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("serpapi.search.http_error: %s", e)
                raise LLMProviderError(f"serpapi search failed: {e}") from e

        body = response.json()
        results: list[SearchResult] = []
        for i, hit in enumerate(body.get("organic_results", [])):
            results.append(
                SearchResult(
                    url=hit.get("link", ""),
                    title=hit.get("title", ""),
                    snippet=hit.get("snippet", ""),
                    rank=i + 1,
                    raw=hit,
                )
            )
        return results[:max_results]
