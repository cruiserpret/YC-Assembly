"""Factories that pick a SearchProvider / ExtractionProvider based on env
config. The pipeline calls these instead of instantiating providers directly,
so swapping Tavily ↔ Brave is a one-env-var change."""
from __future__ import annotations

import logging

from assembly.config import get_settings
from assembly.llm.errors import LLMProviderError
from assembly.retrieval.extraction_provider import (
    ExtractionProvider,
    HttpxExtractionProvider,
    MockExtractionProvider,
)
from assembly.retrieval.search_provider import MockSearchProvider, SearchProvider

logger = logging.getLogger(__name__)


def get_search_provider() -> SearchProvider:
    """Return a SearchProvider per `ASSEMBLY_SEARCH_PROVIDER`. Falls back to
    `mock` (returns no results) if the configured provider isn't installable."""
    s = get_settings()
    name = (s.search_provider or "mock").lower()

    if name == "mock":
        return MockSearchProvider()

    if name == "tavily":
        try:
            from assembly.retrieval.tavily import TavilySearchProvider
            return TavilySearchProvider()
        except (ImportError, LLMProviderError) as e:
            logger.warning(
                "search_provider.tavily_unavailable err=%s — falling back to mock", e
            )
            return MockSearchProvider()

    if name == "brave":
        try:
            from assembly.retrieval.brave import BraveSearchProvider
            return BraveSearchProvider()
        except (ImportError, LLMProviderError) as e:
            logger.warning(
                "search_provider.brave_unavailable err=%s — falling back to mock", e
            )
            return MockSearchProvider()

    if name == "serpapi":
        try:
            from assembly.retrieval.serpapi import SerpAPISearchProvider
            return SerpAPISearchProvider()
        except (ImportError, LLMProviderError) as e:
            logger.warning(
                "search_provider.serpapi_unavailable err=%s — falling back to mock", e
            )
            return MockSearchProvider()

    logger.warning(
        "search_provider.unknown name=%r — falling back to mock", name
    )
    return MockSearchProvider()


def get_extraction_provider() -> ExtractionProvider:
    """Return an ExtractionProvider per `ASSEMBLY_EXTRACTION_PROVIDER`."""
    s = get_settings()
    name = (s.extraction_provider or "httpx").lower()

    if name == "mock":
        return MockExtractionProvider()

    if name == "httpx":
        return HttpxExtractionProvider()

    if name == "firecrawl":
        try:
            from assembly.retrieval.firecrawl import FirecrawlExtractionProvider
            return FirecrawlExtractionProvider()
        except (ImportError, LLMProviderError) as e:
            logger.warning(
                "extraction_provider.firecrawl_unavailable err=%s — falling back to httpx", e
            )
            return HttpxExtractionProvider()

    if name == "jina":
        try:
            from assembly.retrieval.jina import JinaExtractionProvider
            return JinaExtractionProvider()
        except (ImportError, LLMProviderError) as e:
            logger.warning(
                "extraction_provider.jina_unavailable err=%s — falling back to httpx", e
            )
            return HttpxExtractionProvider()

    logger.warning(
        "extraction_provider.unknown name=%r — falling back to httpx", name
    )
    return HttpxExtractionProvider()
