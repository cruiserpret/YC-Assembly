"""Phase 11D.1 — tech_market_signal scaffold table.

Revision ID: 0015_phase_11_d_1
Revises: 0014_phase_11_b_6
Create Date: 2026-05-17

Why:
  Phase 11D.1 lays the schema groundwork for tech / startup market
  intelligence — distilled buyer-language signals from public
  tech-market sources (SaaS reviews, dev-tool forums, B2B procurement
  complaints). The provider stays gated off
  (`ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED=false`) and the table ships
  empty. Phase 11D.2 will wire actual ingestion.

Tables (additive only):
  tech_market_signal — one row per distilled tech-market signal.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0015_phase_11_d_1"
down_revision: str | None = "0014_phase_11_b_6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SENTIMENT_BUCKETS = ("positive", "negative", "mixed")
_SIGNAL_TYPES = (
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
)
_BUYER_TYPES = (
    "user", "buyer", "developer", "founder",
    "admin", "investor", "unknown",
)
_MARKET_CONTEXTS = (
    "B2C", "B2B", "prosumer", "devtool",
    "marketplace", "AI_tool", "unknown",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "tech_market_signal",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_provider", sa.String(64), nullable=False),
        sa.Column("source_category", sa.String(96), nullable=True),
        sa.Column("product_category", sa.String(96), nullable=False),
        sa.Column("company_or_product", sa.String(160), nullable=True),
        sa.Column("competitor_name", sa.String(160), nullable=True),
        sa.Column("signal_type", sa.String(48), nullable=False),
        sa.Column("sentiment_bucket", sa.String(16), nullable=False),
        sa.Column("buyer_type", sa.String(24), nullable=False),
        sa.Column("market_context", sa.String(24), nullable=False),
        sa.Column("theme", sa.String(96), nullable=True),
        sa.Column("short_snippet", sa.Text(), nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column(
            "source_timestamp", sa.BigInteger(), nullable=True,
        ),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"sentiment_bucket IN {_in_clause(_SENTIMENT_BUCKETS)}",
            name="ck_tech_market_signal_sentiment_bucket",
        ),
        sa.CheckConstraint(
            f"signal_type IN {_in_clause(_SIGNAL_TYPES)}",
            name="ck_tech_market_signal_signal_type",
        ),
        sa.CheckConstraint(
            f"buyer_type IN {_in_clause(_BUYER_TYPES)}",
            name="ck_tech_market_signal_buyer_type",
        ),
        sa.CheckConstraint(
            f"market_context IN {_in_clause(_MARKET_CONTEXTS)}",
            name="ck_tech_market_signal_market_context",
        ),
        sa.CheckConstraint(
            "relevance_score IS NULL "
            "OR (relevance_score >= 0 AND relevance_score <= 1)",
            name="ck_tech_market_signal_relevance_score_range",
        ),
    )
    op.create_index(
        "ix_tech_market_signal_product_category",
        "tech_market_signal",
        ["product_category"],
    )
    op.create_index(
        "ix_tech_market_signal_signal_type",
        "tech_market_signal",
        ["signal_type"],
    )
    op.create_index(
        "ix_tech_market_signal_market_context",
        "tech_market_signal",
        ["market_context"],
    )
    op.create_index(
        "ix_tech_market_signal_source_provider",
        "tech_market_signal",
        ["source_provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tech_market_signal_source_provider",
        table_name="tech_market_signal",
    )
    op.drop_index(
        "ix_tech_market_signal_market_context",
        table_name="tech_market_signal",
    )
    op.drop_index(
        "ix_tech_market_signal_signal_type",
        table_name="tech_market_signal",
    )
    op.drop_index(
        "ix_tech_market_signal_product_category",
        table_name="tech_market_signal",
    )
    op.drop_table("tech_market_signal")
