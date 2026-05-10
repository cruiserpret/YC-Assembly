"""Phase 8.5G.1 — Tavily Search adapter for evidence expansion.

Tavily is a DISCOVERY-only provider. Result snippets and URLs MUST
flow through the existing redaction + sensitive-filter + dedup +
forbidden-claim pipeline before any persona ever sees them.

Critical safety properties (drift-tested):
  * `TAVILY_API_KEY` is read ONLY from the environment via
    `os.environ.get`. Never accepted via CLI, never logged, never
    written to audit JSON.
  * `httpx` is the only HTTP transport.
  * `TavilySearchClient.search` REFUSES to run if the key is missing.
  * Per-invocation hard caps: max_queries × max_results_per_query.

Phase 8.5G.1 does NOT write source_records itself — the orchestrator
stages results in memory until persona-coverage gates pass.
"""

from assembly.sources.tavily.adapter import (
    TavilyAdapterConfig,
    TavilyQueryResult,
    TavilySearchClient,
    is_tavily_key_present,
    redact_tavily_url_for_audit,
)

__all__ = [
    "TavilyAdapterConfig",
    "TavilyQueryResult",
    "TavilySearchClient",
    "is_tavily_key_present",
    "redact_tavily_url_for_audit",
]
