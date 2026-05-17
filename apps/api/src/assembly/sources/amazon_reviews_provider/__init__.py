"""Phase 11A — Amazon Reviews ingestion provider.

Higher-level façade over the Phase 8.5A/B local-dataset reader. Where
8.5A streams raw `AmazonReviewRecord` rows off disk, this provider
*distills* each accepted raw row into one or more buyer-language
signals (objections, praise, switch reasons, etc.) suitable for
feeding persona generation and the report's evidence ledger.

Phase 11A is the SCAFFOLD: provider interface + rule-based distiller
+ tiny fixtures. Real category ingestion arrives in Phase 11B; live
runtime retrieval arrives in Phase 11C. The provider is gated OFF by
default via `ASSEMBLY_AMAZON_REVIEWS_ENABLED=false` and never
auto-loads at startup.

Safety properties carried forward from Phase 8.5A:

  * NO live Amazon scraping or API calls. The provider only reads
    from a local on-disk dataset path the operator points it at.
  * Image URLs are never stored.
  * Raw `user_id` is never persisted — only a SHA-256 hash prefix.
  * Full raw review text is never persisted — only short distilled
    snippets that capture one specific signal at a time.
"""
from assembly.sources.amazon_reviews_provider.distiller import (
    DistilledSignal,
    DistillerConfig,
    distill_review_signals,
    is_review_eligible,
)
from assembly.sources.amazon_reviews_provider.ingestion import (
    CategoryIngestPlan,
    IngestionStats,
    InMemorySignalPersister,
    NullSignalPersister,
    SignalPersister,
    build_audit_payload,
    ingest_category,
)
from assembly.sources.amazon_reviews_provider.retrieval import (
    AmazonEvidencePackage,
    AmazonSignalRetriever,
    CandidatePoolStats,
    InMemorySignalSource,
    ProductBriefShape,
    RetrievalConfig,
    RetrievedSignal,
    SignalRow,
    SignalSource,
    classify_brief_to_category,
)
from assembly.sources.amazon_reviews_provider.provider import (
    AmazonReviewsProvider,
    AmazonReviewsProviderConfig,
    ProviderUnavailableError,
)
from assembly.sources.amazon_reviews_provider.signal_types import (
    SENTIMENT_BUCKETS,
    SIGNAL_TYPES,
    SentimentBucket,
    SignalType,
)

__all__ = [
    "AmazonEvidencePackage",
    "AmazonReviewsProvider",
    "AmazonReviewsProviderConfig",
    "AmazonSignalRetriever",
    "CandidatePoolStats",
    "CategoryIngestPlan",
    "DistilledSignal",
    "DistillerConfig",
    "InMemorySignalPersister",
    "InMemorySignalSource",
    "IngestionStats",
    "NullSignalPersister",
    "ProductBriefShape",
    "ProviderUnavailableError",
    "RetrievalConfig",
    "RetrievedSignal",
    "SENTIMENT_BUCKETS",
    "SIGNAL_TYPES",
    "SentimentBucket",
    "SignalPersister",
    "SignalRow",
    "SignalSource",
    "SignalType",
    "build_audit_payload",
    "classify_brief_to_category",
    "distill_review_signals",
    "ingest_category",
    "is_review_eligible",
]
