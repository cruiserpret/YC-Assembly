"""Phase 8.2A — Population Mode foundation: schema only.

Revision ID: 0004_phase_8_2a
Revises: 0003_phase_6_75
Create Date: 2026-05-03

Why:
  Phase 8.2A creates the database + constraint foundation for Population
  Mode without implementing any ingestion, simulation, or UI. The intent
  is that future ingestion/retrieval/graph/simulation code CANNOT store
  unsupported persona fields, sensitive attributes, fake opinions, or
  user-visible real identities — because the schema makes it impossible.

Tables (all additive — no destructive changes to existing tables):
  source_records              — immutable snapshots; closed-enum compliance_tag
  persona_records             — anonymous nodes; no real-identity columns
  persona_traits              — per-field values with support_level CHECKs
  persona_evidence_links      — bind every supported trait to a source row
  persona_opinions            — future per-simulation persona stance rows
  persona_graph_edges         — future similarity / influence graph
  persona_clusters            — future community-detection output
  persona_cluster_membership
  audience_retrieval_runs     — future audit row per retrieval
  population_construction_audit — required artifact per Population-Mode run
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase_8_2a"
down_revision: str | None = "0003_phase_6_75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Closed enums — mirrored in pipeline/persona/constants.py.
_SUPPORT_LEVELS = ("direct", "inferred", "unknown", "missing")
_COMPLIANCE_TAGS = (
    "public_api", "public_html", "open_dataset", "open_aggregate", "manual_seed",
)
_PERSONA_FIELD_NAMES = (
    "interests",
    "role_or_context",
    "buying_constraints",
    "trust_triggers",
    "current_alternatives",
    "communication_style",
    "influence_signals",
    "price_sensitivity",
    "objection_patterns",
    "geography_broad",
)
_EDGE_TYPES = ("similar_to", "influences", "shares_segment", "shared_source", "bridge_to")
_EDGE_BASIS = ("embedding_cosine", "shared_source", "deterministic", "inferred")
_COVERAGE_LABELS = ("thin", "moderate", "strong")


def _in_clause(values: tuple[str, ...]) -> str:
    """Render a SQL IN ('a','b',...) clause for a closed enum CHECK."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # ----------------------------------------------------------------------
    # 1) source_records — immutable snapshots from public sources.
    # No raw handle, no name, no photo, no email — by schema design.
    # ----------------------------------------------------------------------
    op.create_table(
        "source_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_kind", sa.String(48), nullable=False, index=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("ingested_by", sa.String(64), nullable=False),
        sa.Column("compliance_tag", sa.String(48), nullable=False),
        sa.Column("user_handle_hash", sa.String(64), nullable=True),
        sa.Column(
            "pii_redaction_status",
            sa.String(32),
            nullable=False,
            server_default="not_run",
        ),
        sa.Column(
            "sensitive_scan_status",
            sa.String(32),
            nullable=False,
            server_default="not_run",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_kind", "content_hash",
            name="uq_source_records_kind_hash",
        ),
        sa.CheckConstraint(
            f"compliance_tag IN {_in_clause(_COMPLIANCE_TAGS)}",
            name="ck_source_records_compliance_tag",
        ),
    )

    # ----------------------------------------------------------------------
    # 2) persona_records — anonymous synthetic society node.
    # ----------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "persona_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("segment_label", sa.String(64), nullable=True, index=True),
        sa.Column("origin_market_broad", sa.String(64), nullable=True, index=True),
        sa.Column(
            "product_relevance_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("influence_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("susceptibility", sa.Numeric(4, 2), nullable=True),
        sa.Column(
            "population_weight",
            sa.Numeric(8, 3),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("source_strength_score", sa.Numeric(4, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "refreshed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "influence_score IS NULL OR (influence_score >= 0 AND influence_score <= 1)",
            name="ck_persona_records_influence_score_range",
        ),
        sa.CheckConstraint(
            "susceptibility IS NULL OR (susceptibility >= 0 AND susceptibility <= 1)",
            name="ck_persona_records_susceptibility_range",
        ),
    )
    # Embedding column added via raw SQL because pgvector type isn't a native
    # SQLAlchemy type without an extra adapter.
    op.execute(
        "ALTER TABLE persona_records ADD COLUMN embedding vector(1536) NULL"
    )

    # ----------------------------------------------------------------------
    # 3) persona_traits — per-field values with support_level CHECKs.
    # ----------------------------------------------------------------------
    op.create_table(
        "persona_traits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("support_level", sa.String(16), nullable=False),
        sa.Column(
            "source_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "confidence",
            sa.Numeric(4, 2),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "persona_id", "field_name",
            name="uq_persona_traits_persona_field",
        ),
        sa.CheckConstraint(
            f"support_level IN {_in_clause(_SUPPORT_LEVELS)}",
            name="ck_persona_traits_support_level",
        ),
        sa.CheckConstraint(
            f"field_name IN {_in_clause(_PERSONA_FIELD_NAMES)}",
            name="ck_persona_traits_field_name",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_persona_traits_confidence_range",
        ),
        # support_level / value / source_ids combinations:
        sa.CheckConstraint(
            "(support_level IN ('direct','inferred') "
            "  AND cardinality(source_ids) >= 1 "
            "  AND value IS NOT NULL "
            "  AND confidence > 0) "
            "OR (support_level = 'unknown' "
            "    AND value IS NULL "
            "    AND cardinality(source_ids) = 0) "
            "OR (support_level = 'missing' "
            "    AND value IS NULL)",
            name="ck_persona_traits_support_consistency",
        ),
    )

    # ----------------------------------------------------------------------
    # 4) persona_evidence_links — every supported trait must trace to source.
    # ----------------------------------------------------------------------
    op.create_table(
        "persona_evidence_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("contribution_kind", sa.String(32), nullable=False),
        sa.Column("contribution_field", sa.String(64), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_offset", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "persona_id", "source_record_id", "contribution_field",
            name="uq_persona_evidence_links_unique_contribution",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_persona_evidence_links_confidence_range",
        ),
        sa.CheckConstraint(
            f"contribution_field IN {_in_clause(_PERSONA_FIELD_NAMES)}",
            name="ck_persona_evidence_links_field",
        ),
    )

    # ----------------------------------------------------------------------
    # 5) persona_opinions — future per-simulation rows. Schema-only in 8.2A.
    # ----------------------------------------------------------------------
    op.create_table(
        "persona_opinions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("stance", sa.String(32), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("cluster_label", sa.String(64), nullable=True),
        sa.Column(
            "is_representative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("propagation_round", sa.Integer(), nullable=True),
        sa.Column(
            "influenced_by_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "persona_id", "simulation_id",
            name="uq_persona_opinions_persona_simulation",
        ),
    )

    # ----------------------------------------------------------------------
    # 6) persona_graph_edges — future graph. Schema-only in 8.2A.
    # ----------------------------------------------------------------------
    op.create_table(
        "persona_graph_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("edge_type", sa.String(32), nullable=False, index=True),
        sa.Column("strength", sa.Numeric(4, 2), nullable=False),
        sa.Column("basis", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_persona_id", "target_persona_id", "edge_type",
            name="uq_persona_graph_edges_unique_edge",
        ),
        sa.CheckConstraint(
            "source_persona_id <> target_persona_id",
            name="ck_persona_graph_edges_no_self_loop",
        ),
        sa.CheckConstraint(
            "strength >= 0 AND strength <= 1",
            name="ck_persona_graph_edges_strength_range",
        ),
        sa.CheckConstraint(
            f"edge_type IN {_in_clause(_EDGE_TYPES)}",
            name="ck_persona_graph_edges_edge_type",
        ),
        sa.CheckConstraint(
            f"basis IN {_in_clause(_EDGE_BASIS)}",
            name="ck_persona_graph_edges_basis",
        ),
    )

    # ----------------------------------------------------------------------
    # 7) persona_clusters
    # ----------------------------------------------------------------------
    op.create_table(
        "persona_clusters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "member_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("dominant_objection_pattern", sa.Text(), nullable=True),
        sa.Column("dominant_persuasion_driver", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "ALTER TABLE persona_clusters ADD COLUMN centroid_embedding vector(1536) NULL"
    )

    op.create_table(
        "persona_cluster_membership",
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "persona_id", "cluster_id",
            name="pk_persona_cluster_membership",
        ),
    )

    # ----------------------------------------------------------------------
    # 8) audience_retrieval_runs — future audit log per retrieval.
    # ----------------------------------------------------------------------
    op.create_table(
        "audience_retrieval_runs",
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
            "query",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("retrieved_count", sa.Integer(), nullable=False),
        sa.Column("filtered_count", sa.Integer(), nullable=False),
        sa.Column(
            "ranking_signals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "geography_coverage_label",
            sa.String(16),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"geography_coverage_label IS NULL "
            f"OR geography_coverage_label IN {_in_clause(_COVERAGE_LABELS)}",
            name="ck_audience_retrieval_runs_geography_coverage_label",
        ),
    )

    # ----------------------------------------------------------------------
    # 9) population_construction_audit — required artifact per Population run.
    # ----------------------------------------------------------------------
    op.create_table(
        "population_construction_audit",
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
            unique=True,
        ),
        sa.Column(
            "requested_society",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("retrieved_persona_count", sa.Integer(), nullable=False),
        sa.Column("final_persona_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False),
        sa.Column(
            "source_kind_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "direct_trait_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "inferred_trait_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "unknown_trait_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "missing_trait_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "trait_support_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "geography_coverage_label",
            sa.String(16),
            nullable=False,
        ),
        sa.Column("geography_coverage_notes", sa.Text(), nullable=True),
        sa.Column("source_freshness_label", sa.String(16), nullable=True),
        sa.Column(
            "representativeness_caveats",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "missing_evidence_warnings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "compliance_status",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("society_strength_label", sa.String(16), nullable=False),
        sa.Column("society_strength_explanation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"geography_coverage_label IN {_in_clause(_COVERAGE_LABELS)}",
            name="ck_population_audit_geography_coverage_label",
        ),
        sa.CheckConstraint(
            f"source_freshness_label IS NULL "
            f"OR source_freshness_label IN {_in_clause(_COVERAGE_LABELS)}",
            name="ck_population_audit_source_freshness_label",
        ),
        sa.CheckConstraint(
            f"society_strength_label IN {_in_clause(_COVERAGE_LABELS)}",
            name="ck_population_audit_society_strength_label",
        ),
        sa.CheckConstraint(
            "retrieved_persona_count >= 0 "
            "AND final_persona_count >= 0 "
            "AND cluster_count >= 0",
            name="ck_population_audit_counts_nonneg",
        ),
    )


def downgrade() -> None:
    op.drop_table("population_construction_audit")
    op.drop_table("audience_retrieval_runs")
    op.drop_table("persona_cluster_membership")
    op.drop_table("persona_clusters")
    op.drop_table("persona_graph_edges")
    op.drop_table("persona_opinions")
    op.drop_table("persona_evidence_links")
    op.drop_table("persona_traits")
    op.drop_table("persona_records")
    op.drop_table("source_records")
