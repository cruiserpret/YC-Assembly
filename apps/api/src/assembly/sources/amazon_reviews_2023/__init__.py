"""Phase 8.5A — Amazon Reviews 2023 LOCAL dataset adapter scaffold.

This adapter ONLY reads from a local on-disk copy of the Amazon
Reviews 2023 dataset (https://amazon-reviews-2023.github.io/). It
does NOT call any Amazon API and does NOT scrape Amazon.com — both
forbidden, drift-tested.

Reviews loaded here are CANDIDATE evidence — they MUST flow through
the existing redaction + sensitive-filter + dedup discipline before
any persona ever sees them.

Compliance memo: ../../../../docs/source_compliance/amazon_reviews_2023.md
"""

from assembly.sources.amazon_reviews_2023.adapter import (
    AmazonReviewsAdapterConfig,
    AmazonReviewRecord,
    AmazonReviewsLocalReader,
    discover_category_files,
    looks_like_low_quality_review,
    matches_search_terms,
    parse_amazon_review_line,
    resolve_categories,
)
from assembly.sources.amazon_reviews_2023.filters import (
    AmazonProductMetadata,
    MetadataIndex,
    PrimeContext,
    ReviewConfidence,
    ReviewScoreDetail,
    TIGHTENED_SEARCH_TERMS,
    flavor_qualifies,
    prime_context_classification,
    score_review,
)

__all__ = [
    "AmazonReviewsAdapterConfig",
    "AmazonReviewRecord",
    "AmazonReviewsLocalReader",
    "discover_category_files",
    "looks_like_low_quality_review",
    "matches_search_terms",
    "parse_amazon_review_line",
    "resolve_categories",
    # 8.5B additions:
    "AmazonProductMetadata",
    "MetadataIndex",
    "PrimeContext",
    "ReviewConfidence",
    "ReviewScoreDetail",
    "TIGHTENED_SEARCH_TERMS",
    "flavor_qualifies",
    "prime_context_classification",
    "score_review",
]
