"""Phase 6.75 — evidence graph: extend evidence_items, add evidence_edges + claims.

Revision ID: 0003_phase_6_75
Revises: 0002_phase_6_5
Create Date: 2026-05-02

Why:
  Phase 7 (aggregation) must read from a structured graph, not a flat list of
  evidence_items. This migration:

  1. Extends `evidence_items` with classification, hashing, embedding, and
     dedup-group columns.
  2. Adds `evidence_edges` for typed relationships between evidence atoms.
  3. Adds `claims` for claim-to-source binding (every claim must reference
     an existing evidence_items row).
  4. Extends `simulations` with `evidence_graph_built_at` for idempotent
     resume of the graph-build pipeline stage.

Migration safety (Correction 1):
  - `content_hash` is added NULLABLE first.
  - The upgrade backfills `content_hash` for every existing row using a
    deterministic fallback chain: normalized content → source_url+excerpt →
    id-only fallback. No row is left invalid.
  - Only AFTER the backfill commits do we set `content_hash NOT NULL` and
    create the index.

  The `vector(1536)` column for embeddings is created NULLABLE; embedding is
  always optional. pgvector extension was enabled in the Phase 4 migration.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase_6_75"
down_revision: str | None = "0002_phase_6_5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Closed enums — kept in sync with the Python-side constants.
NODE_CLASSES = (
    "competitor",
    "pricing",
    "review",
    "buyer_pain",
    "objection",
    "claim",
    "claim_risk",
    "claim_support",
    "category_language",
    "current_alternative",
    "switching_trigger",
    "trust_barrier",
    "analogical_market",
    "segment_behavior",
    "unknown",
)

EDGE_TYPES = (
    "supports",
    "contradicts",
    "similar_to",
    "causes_objection",
    "reduces_objection",
    "maps_to_segment",
    "maps_to_price_sensitivity",
    "maps_to_switching_trigger",
    "maps_to_trust_barrier",
    "maps_to_competitor",
    "maps_to_category_language",
    "maps_to_recommendation",
    "priced_against",
    "competes_with",
)


def upgrade() -> None:
    # ----------------------------------------------------------------------
    # 1) Extend evidence_items.
    # ----------------------------------------------------------------------

    # node_class — classification, defaults to 'unknown' so existing rows are valid.
    op.add_column(
        "evidence_items",
        sa.Column(
            "node_class",
            sa.String(32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        "ix_evidence_items_node_class",
        "evidence_items",
        ["node_class"],
    )

    # node_class_confidence — 0.0–1.0; existing rows default to 0.0.
    op.add_column(
        "evidence_items",
        sa.Column(
            "node_class_confidence",
            sa.Numeric(4, 2),
            nullable=False,
            server_default="0.0",
        ),
    )

    # content_hash — NULLABLE first so backfill can run.
    op.add_column(
        "evidence_items",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )

    # Backfill content_hash for every existing row using a deterministic
    # fallback chain. md5 is a Postgres builtin (no extension required) and
    # is sufficient for content-equivalence checks here — collision risk on
    # human-written evidence content is negligible at our scale.
    #
    # Fallback chain (Correction 1):
    #   1. normalized content (lower + trim + collapse whitespace)
    #   2. if content empty: source_url + '|' + (metadata->>'source_excerpt')
    #   3. if both missing: id::text (always non-null)
    op.execute(
        """
        UPDATE evidence_items
        SET content_hash = md5(
            COALESCE(
                NULLIF(
                    regexp_replace(lower(trim(content)), '\\s+', ' ', 'g'),
                    ''
                ),
                NULLIF(
                    coalesce(source_url, '') || '|' ||
                    coalesce(metadata->>'source_excerpt', ''),
                    '|'
                ),
                id::text
            )
        )
        WHERE content_hash IS NULL
        """
    )

    # Now lock content_hash to NOT NULL + create the index.
    op.alter_column(
        "evidence_items",
        "content_hash",
        nullable=False,
        existing_type=sa.String(64),
    )
    op.create_index(
        "ix_evidence_items_content_hash",
        "evidence_items",
        ["content_hash"],
    )

    # dedup_group_id — NULL means "not yet deduped" or "singleton group".
    # Indexed because the retriever filters by it when collapse_duplicates=True.
    op.add_column(
        "evidence_items",
        sa.Column(
            "dedup_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_evidence_items_dedup_group_id",
        "evidence_items",
        ["dedup_group_id"],
    )

    # Enable pgvector. Image is pgvector/pgvector — extension files ship with
    # it; we just need to install it. Idempotent on re-run.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Embedding column — nullable. Vector dimension matches OpenAI's
    # `text-embedding-3-small` default (1536). Mock provider produces
    # vectors of the same dimension so the column is interchangeable
    # between modes.
    op.execute(
        "ALTER TABLE evidence_items ADD COLUMN embedding vector(1536) NULL"
    )
    op.add_column(
        "evidence_items",
        sa.Column("embedding_model", sa.String(64), nullable=True),
    )
    op.add_column(
        "evidence_items",
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # IVFFlat index on embedding for ANN queries (built lazily by the graph
    # builder once enough rows are populated; index creation here is a best-
    # effort hint — postgres will skip if the extension is missing).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_evidence_items_embedding "
        "ON evidence_items USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 50)"
    )

    # ----------------------------------------------------------------------
    # 2) evidence_edges.
    # ----------------------------------------------------------------------
    op.create_table(
        "evidence_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("edge_type", sa.String(48), nullable=False, index=True),
        sa.Column("strength", sa.Numeric(4, 2), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 2), nullable=False),
        # 'direct' (deterministic) | 'analogical' | 'inferred' (LLM-derived).
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "simulation_id",
            "source_evidence_id",
            "target_evidence_id",
            "edge_type",
            name="uq_evidence_edges_unique_edge",
        ),
        sa.CheckConstraint(
            "strength >= 0 AND strength <= 1",
            name="ck_evidence_edges_strength_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_evidence_edges_confidence_range",
        ),
        sa.CheckConstraint(
            "source_evidence_id <> target_evidence_id",
            name="ck_evidence_edges_no_self_loop",
        ),
    )

    # ----------------------------------------------------------------------
    # 3) claims — every claim must bind to an existing evidence_items row.
    #    ON DELETE RESTRICT means orphan claims cannot exist.
    # ----------------------------------------------------------------------
    op.create_table(
        "claims",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "source_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False, index=True),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_claims_confidence_range",
        ),
    )

    # ----------------------------------------------------------------------
    # 4) simulations.evidence_graph_built_at — idempotent resume flag.
    # ----------------------------------------------------------------------
    op.add_column(
        "simulations",
        sa.Column(
            "evidence_graph_built_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("simulations", "evidence_graph_built_at")
    op.drop_table("claims")
    op.drop_table("evidence_edges")

    op.execute("DROP INDEX IF EXISTS ix_evidence_items_embedding")
    op.drop_column("evidence_items", "embedded_at")
    op.drop_column("evidence_items", "embedding_model")
    op.execute("ALTER TABLE evidence_items DROP COLUMN IF EXISTS embedding")
    op.drop_index("ix_evidence_items_dedup_group_id", "evidence_items")
    op.drop_column("evidence_items", "dedup_group_id")
    op.drop_index("ix_evidence_items_content_hash", "evidence_items")
    op.drop_column("evidence_items", "content_hash")
    op.drop_column("evidence_items", "node_class_confidence")
    op.drop_index("ix_evidence_items_node_class", "evidence_items")
    op.drop_column("evidence_items", "node_class")
