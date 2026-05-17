"""Phase 11D.1 — distilled tech_market_signal table.

This table is the persistence layer for the tech / startup market
intelligence scaffold. One row = one distilled buyer-language signal
extracted from a tech-market source (e.g. a SaaS review, a developer
forum post, a B2B procurement complaint).

The table is INTENTIONALLY narrow and free of any raw user
identifiers. Distillation collapses the source text into a short
snippet plus structured columns (signal_type, sentiment_bucket,
buyer_type, market_context) that the persona-injection layer can
balance across without ever seeing raw post bodies.

Constraints (CHECK):
  * `sentiment_bucket` ∈ SENTIMENT_BUCKETS
  * `signal_type` ∈ SIGNAL_TYPES
  * `buyer_type` ∈ BUYER_TYPES
  * `market_context` ∈ MARKET_CONTEXTS
  * `relevance_score` ∈ [0, 1] (when provided)

Phase 11D.1 ships this table EMPTY. The feature flag
`ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED` defaults False — no provider
writes here until 11D.2 wires up ingestion.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


# Closed enum values — kept in lockstep with the
# `assembly.sources.tech_market_provider.signal_types` Literal
# definitions and the matching DB CHECK constraints below.
SENTIMENT_BUCKETS: tuple[str, ...] = ("positive", "negative", "mixed")

SIGNAL_TYPES: tuple[str, ...] = (
    "pain_urgency",
    "switching_objection",
    "pricing_objection",
    "trust_security_concern",
    "integration_friction",
    "onboarding_friction",
    "support_complaint",
    "competitor_comparison",
    "willingness_to_pay",
    "nice_to_have_risk",
    "feature_not_company_risk",
    "workflow_fit",
    "developer_skepticism",
    "procurement_friction",
    "feature_inquiry",
)

BUYER_TYPES: tuple[str, ...] = (
    "user",
    "buyer",
    "developer",
    "founder",
    "admin",
    "investor",
    "unknown",
)

MARKET_CONTEXTS: tuple[str, ...] = (
    "B2C",
    "B2B",
    "prosumer",
    "devtool",
    "marketplace",
    "AI_tool",
    "unknown",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


class TechMarketSignal(Base):
    __tablename__ = "tech_market_signal"
    __table_args__ = (
        CheckConstraint(
            f"sentiment_bucket IN {_in_clause(SENTIMENT_BUCKETS)}",
            name="ck_tech_market_signal_sentiment_bucket",
        ),
        CheckConstraint(
            f"signal_type IN {_in_clause(SIGNAL_TYPES)}",
            name="ck_tech_market_signal_signal_type",
        ),
        CheckConstraint(
            f"buyer_type IN {_in_clause(BUYER_TYPES)}",
            name="ck_tech_market_signal_buyer_type",
        ),
        CheckConstraint(
            f"market_context IN {_in_clause(MARKET_CONTEXTS)}",
            name="ck_tech_market_signal_market_context",
        ),
        CheckConstraint(
            "relevance_score IS NULL "
            "OR (relevance_score >= 0 AND relevance_score <= 1)",
            name="ck_tech_market_signal_relevance_score_range",
        ),
        Index(
            "ix_tech_market_signal_product_category", "product_category",
        ),
        Index(
            "ix_tech_market_signal_signal_type", "signal_type",
        ),
        Index(
            "ix_tech_market_signal_market_context", "market_context",
        ),
        Index(
            "ix_tech_market_signal_source_provider", "source_provider",
        ),
    )

    id: Mapped[UUIDPk]

    # Where the signal came from (provider name like
    # 'g2_reviews_synthetic', 'producthunt_comments', 'hn_threads').
    source_provider: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    # The provider's own category slug (the upstream classifier's
    # label, kept verbatim so we can roll back if our mapping is
    # wrong).
    source_category: Mapped[str | None] = mapped_column(
        String(96), nullable=True,
    )
    # Assembly-side product category (controlled vocabulary —
    # 'ai_saas', 'browser_extension', 'devtool_api',
    # 'b2b_workflow_saas', 'consumer_mobile_app', 'marketplace',
    # 'unknown').
    product_category: Mapped[str] = mapped_column(
        String(96), nullable=False,
    )
    company_or_product: Mapped[str | None] = mapped_column(
        String(160), nullable=True,
    )
    competitor_name: Mapped[str | None] = mapped_column(
        String(160), nullable=True,
    )
    signal_type: Mapped[str] = mapped_column(String(48), nullable=False)
    sentiment_bucket: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    buyer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    market_context: Mapped[str] = mapped_column(
        String(24), nullable=False,
    )
    theme: Mapped[str | None] = mapped_column(String(96), nullable=True)
    short_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )
    # Unix epoch seconds OR milliseconds — provider decides. BigInteger
    # mirrors the Phase-11B.6 Amazon-side fix that prevents 32-bit
    # overflow on ms timestamps.
    source_timestamp: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    relevance_score: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    # Provider-specific metadata (rating, helpful votes, post score,
    # tags, etc.). Keep PII out — distillation drops user_id /
    # author handles.
    metadata_json: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True,
    )
    created_at: Mapped[CreatedAt]


__all__ = [
    "BUYER_TYPES",
    "MARKET_CONTEXTS",
    "SENTIMENT_BUCKETS",
    "SIGNAL_TYPES",
    "TechMarketSignal",
]
