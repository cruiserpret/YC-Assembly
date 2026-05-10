"""Tavily search provider skeleton.

Tavily is purpose-built for LLM agents and has a generous free tier. Wired
through async httpx (no SDK required). Tests use `MockSearchProvider`, not
this. Real call is gated on `TAVILY_API_KEY` being set.

Key reference (Phase-6-gate is on LLM cost, not search; this provider does
not call any LLM).
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.retrieval.search_provider import SearchProvider, SearchResult

logger = logging.getLogger(__name__)


_TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearchProvider(SearchProvider):
    """Live Tavily search. Disabled unless TAVILY_API_KEY is set."""

    name: ClassVar[str] = "tavily"

    def __init__(self, *, api_key: str | None = None) -> None:
        key = api_key or get_settings().tavily_api_key
        if not key:
            raise LLMProviderError("TAVILY_API_KEY not configured")
        self._key = key

    async def search(
        self, query: str, *, max_results: int = 10
    ) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    _TAVILY_ENDPOINT,
                    json={
                        "api_key": self._key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                        "include_answer": False,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("tavily.search.http_error: %s", e)
                raise LLMProviderError(f"tavily search failed: {e}") from e

        body: dict[str, Any] = response.json()
        results: list[SearchResult] = []
        for i, hit in enumerate(body.get("results", [])):
            results.append(
                SearchResult(
                    url=hit.get("url", ""),
                    title=hit.get("title", ""),
                    snippet=hit.get("content", ""),
                    rank=i + 1,
                    raw=hit,
                )
            )
        return results[:max_results]
