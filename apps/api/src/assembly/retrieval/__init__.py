"""Real-world evidence retrieval — Phase 5.5 foundation.

Two provider abstractions live here:

  - `SearchProvider`     — find URLs relevant to a brief (Tavily, Brave,
                            SerpAPI, mock).
  - `ExtractionProvider` — convert a URL into plain text we can store as
                            evidence (Firecrawl, Jina, httpx fallback, mock).

The Phase 4 evidence builder integrates this layer when `retrieval_enabled`
is True. By default it is OFF — the system runs zero-cost, zero-key out of
the box. Flip it via `ASSEMBLY_RETRIEVAL_ENABLED=true` and configure
`SEARCH_PROVIDER` / `EXTRACTION_PROVIDER` env vars when you want real
search/extraction.

Anti-hallucination guarantees preserved:
  - `retrieval honors evidence_cutoff_date` (refuses live retrieval when set).
  - `every retrieved page becomes a kind=direct or kind=analogical
     evidence_item with source_url + captured_at`.
  - `if search returns nothing, a kind=missing item is recorded for the gap`.
  - `the C1 source-bound LLM extractor still runs; every extracted phrase
     must appear verbatim in the fetched page text`.

PHASE-6-GATE (O1) preserved: nothing in this module calls an LLM directly.
The single LLM-using path (the existing `extract_category_language`) goes
through `provider.structured_output(...)` and is the call site the Phase 6
worker must wrap in `with_cost_guard`.
"""
from assembly.retrieval.extraction_provider import (
    ExtractedPage,
    ExtractionProvider,
    HttpxExtractionProvider,
    MockExtractionProvider,
)
from assembly.retrieval.factory import (
    get_extraction_provider,
    get_search_provider,
)
from assembly.retrieval.search_provider import (
    MockSearchProvider,
    SearchProvider,
    SearchResult,
)

__all__ = [
    "ExtractedPage",
    "ExtractionProvider",
    "HttpxExtractionProvider",
    "MockExtractionProvider",
    "MockSearchProvider",
    "SearchProvider",
    "SearchResult",
    "get_extraction_provider",
    "get_search_provider",
]


def __getattr__(name: str):
    """Lazy imports for optional providers — avoid forcing every SDK to be
    installed for unit tests that only use Mock providers."""
    if name == "TavilySearchProvider":
        from assembly.retrieval.tavily import TavilySearchProvider
        return TavilySearchProvider
    if name == "BraveSearchProvider":
        from assembly.retrieval.brave import BraveSearchProvider
        return BraveSearchProvider
    if name == "SerpAPISearchProvider":
        from assembly.retrieval.serpapi import SerpAPISearchProvider
        return SerpAPISearchProvider
    if name == "FirecrawlExtractionProvider":
        from assembly.retrieval.firecrawl import FirecrawlExtractionProvider
        return FirecrawlExtractionProvider
    if name == "JinaExtractionProvider":
        from assembly.retrieval.jina import JinaExtractionProvider
        return JinaExtractionProvider
    raise AttributeError(f"module 'assembly.retrieval' has no attribute {name!r}")
