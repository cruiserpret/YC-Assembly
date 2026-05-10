"""Phase 8.5A — Brave Search adapter scaffold.

Brave is a DISCOVERY-only provider in Assembly. Result snippets and
URLs surfaced here are CANDIDATE evidence — they MUST flow through
the existing Phase 8.2x extraction + redaction + sensitive-filter +
dedup pipeline before any persona ever sees them.

Compliance memo: ../../../../docs/source_compliance/brave_search.md
"""

from assembly.sources.brave.adapter import (
    BraveAdapterConfig,
    BraveQueryResult,
    BraveSearchClient,
    build_brave_query_set,
    is_brave_key_present,
    redact_url_for_audit,
)

__all__ = [
    "BraveAdapterConfig",
    "BraveQueryResult",
    "BraveSearchClient",
    "build_brave_query_set",
    "is_brave_key_present",
    "redact_url_for_audit",
]
