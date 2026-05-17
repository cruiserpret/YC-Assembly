"""Phase 11D.1 — tech / startup market intelligence provider.

Scaffold-only. Provides:

  * Closed enums for SignalType / BuyerType / MarketContext /
    SentimentBucket (`signal_types`).
  * `RuleBasedTechMarketDistiller` + `DistilledTechSignal` —
    deterministic keyword-rule distiller for raw tech-market
    source text (`distiller`).
  * `InMemoryTechMarketSignalSource` + `TechMarketSignalRetriever`
    + `TechMarketEvidencePackage` — feature-flagged retrieval
    scaffold mirroring the Phase-11C Amazon design (`retrieval`).
  * `FixtureTechMarketSignalProvider` — dev/test provider that
    walks the Phase-11D.1 synthetic fixtures through the distiller.
    REFUSES to run unless `ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED=true`
    (`provider`).

Safety properties for Phase 11D.1:

  * Both feature gates default False. The retriever short-circuits
    to an empty package; the fixture provider raises
    `ProviderDisabledError`.
  * No production code path imports the fixture corpus or wires
    the retriever into the persona-injection pipeline yet — Phase
    11D.2 lands persistence + Phase 11D.3+ lands persona injection.
  * `RetrievedTechSignal` strips every PII-leaning field on the way
    out (author handle, raw post body, row id, created_at).
  * Snippets are hard-capped at 240 chars at distillation time.
  * No HTTP imports. No scraping verbs.
"""
from assembly.sources.tech_market_provider.distiller import (
    DistilledTechSignal,
    RuleBasedTechMarketDistiller,
    TechMarketSignalDistiller,
)
from assembly.sources.tech_market_provider.fixtures import (
    iter_phase_11d_1_fixtures,
    total_fixture_count,
)
from assembly.sources.tech_market_provider.ingestion import (
    CSV_OPTIONAL_COLUMNS,
    CSV_REQUIRED_COLUMNS,
    CSVRowParseResult,
    InMemoryTechMarketPersister,
    NullTechMarketPersister,
    PostgresTechMarketPersister,
    TechMarketIngestionStats,
    TechMarketSignalPersister,
    TechSignalIdentity,
    build_audit_payload,
    dedupe_identity_for,
    distill_csv_row,
    ingest_csv_rows,
)
from assembly.sources.tech_market_provider.provider import (
    FixtureTechMarketSignalProvider,
    ProviderDisabledError,
    TechMarketSignalProvider,
    TechMarketSignalProviderConfig,
)
from assembly.sources.tech_market_provider.retrieval import (
    InMemoryTechMarketSignalSource,
    RetrievedTechSignal,
    TechMarketEvidencePackage,
    TechMarketRetrievalConfig,
    TechMarketSignalRetriever,
    TechMarketSignalSource,
    TechProductBriefShape,
    TechSignalRow,
)
from assembly.sources.tech_market_provider.signal_types import (
    BUYER_TYPES,
    BuyerType,
    MARKET_CONTEXTS,
    MarketContext,
    PRODUCT_CATEGORIES,
    SENTIMENT_BUCKETS,
    SIGNAL_TYPES,
    SentimentBucket,
    SignalType,
)


__all__ = [
    "BUYER_TYPES",
    "BuyerType",
    "CSV_OPTIONAL_COLUMNS",
    "CSV_REQUIRED_COLUMNS",
    "CSVRowParseResult",
    "DistilledTechSignal",
    "FixtureTechMarketSignalProvider",
    "InMemoryTechMarketPersister",
    "InMemoryTechMarketSignalSource",
    "MARKET_CONTEXTS",
    "MarketContext",
    "NullTechMarketPersister",
    "PostgresTechMarketPersister",
    "PRODUCT_CATEGORIES",
    "ProviderDisabledError",
    "RetrievedTechSignal",
    "RuleBasedTechMarketDistiller",
    "SENTIMENT_BUCKETS",
    "SIGNAL_TYPES",
    "SentimentBucket",
    "SignalType",
    "TechMarketEvidencePackage",
    "TechMarketIngestionStats",
    "TechMarketRetrievalConfig",
    "TechMarketSignalDistiller",
    "TechMarketSignalPersister",
    "TechMarketSignalProvider",
    "TechMarketSignalProviderConfig",
    "TechMarketSignalRetriever",
    "TechMarketSignalSource",
    "TechProductBriefShape",
    "TechSignalIdentity",
    "TechSignalRow",
    "build_audit_payload",
    "dedupe_identity_for",
    "distill_csv_row",
    "ingest_csv_rows",
    "iter_phase_11d_1_fixtures",
    "total_fixture_count",
]
