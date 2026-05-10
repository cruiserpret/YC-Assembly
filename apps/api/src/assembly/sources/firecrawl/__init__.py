"""Phase 8.5G.1 — Firecrawl page-extraction adapter.

Firecrawl is used to convert a small set of top-ranked search-result
URLs into clean Markdown content. Strictly bounded:

  * max 20 pages per invocation
  * max 10 pages per domain
  * extracted content must pass PII / fake-use / generic scanners
    before flowing to source_records
  * no broad crawling, no auth-walled URLs
  * no PII / image / profile-URL storage

Critical safety properties (drift-tested):
  * `FIRECRAWL_API_KEY` is read ONLY from the environment.
  * `httpx` is the only HTTP transport.
  * Refuses to run if the key is missing.
"""

from assembly.sources.firecrawl.adapter import (
    FirecrawlAdapterConfig,
    FirecrawlExtractClient,
    FirecrawlExtractResult,
    is_firecrawl_key_present,
)

__all__ = [
    "FirecrawlAdapterConfig",
    "FirecrawlExtractClient",
    "FirecrawlExtractResult",
    "is_firecrawl_key_present",
]
