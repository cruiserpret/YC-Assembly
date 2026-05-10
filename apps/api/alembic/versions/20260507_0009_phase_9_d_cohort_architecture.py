"""Phase 9D — cohort / cluster architecture.

Revision ID: 0009_phase_9_d
Revises: 0008_phase_9_a_4
Create Date: 2026-05-07

Why:
  Phase 9D introduces a cohort/cluster summary layer over the existing
  9B run-scoped society. Cohorts are run-scoped, brief-scoped,
  evidence-anchored summaries of similar personas — never global, never
  permanent market segments. Three additive tables; no destructive
  changes.

Tables:
  society_cohorts                  — one row per cohort
  society_cohort_evidence_links    — traceability rows binding cohort
                                      claims to persona/source/turn/atom
  society_cohort_rollups           — single weighted-rollup summary per
                                      run scope
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0009_phase_9_d"
down_revision: str | None = "0008_phase_9_a_4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CLUSTERING_METHODS = (
    "deterministic_agglomerative_v1", "external_seed", "manual_audit",
)
_EVIDENCE_ROLES = (
    "objection", "proof_need", "stance_anchor", "psychology_anchor",
    "discussion_anchor", "evidence_anchor",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "society_cohorts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("cohort_label", sa.String(128), nullable=False),
        sa.Column("cohort_size", sa.Integer(), nullable=False),
        sa.Column(
            "cohort_weight", sa.Numeric(6, 4), nullable=False,
        ),
        sa.Column(
            "representative_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "member_persona_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("clustering_method", sa.String(64), nullable=False),
        sa.Column(
            "role_distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "stance_distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "psychology_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "objection_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "proof_need_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "discussion_behavior_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "caveats",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "cohort_size >= 1",
            name="ck_society_cohorts_size_positive",
        ),
        sa.CheckConstraint(
            "cohort_weight >= 0 AND cohort_weight <= 1",
            name="ck_society_cohorts_weight_range",
        ),
        sa.CheckConstraint(
            f"clustering_method IN {_in_clause(_CLUSTERING_METHODS)}",
            name="ck_society_cohorts_clustering_method",
        ),
    )
    op.create_index(
        "ix_society_cohorts_run_phase",
        "society_cohorts",
        ["run_scope_id", "phase"],
    )

    op.create_table(
        "society_cohort_evidence_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("society_cohorts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "discussion_turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discussion_turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "memory_atom_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_memory_atoms.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_role", sa.String(32), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column(
            "confidence", sa.Numeric(4, 2), nullable=False,
            server_default=sa.text("0.5"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"evidence_role IN {_in_clause(_EVIDENCE_ROLES)}",
            name="ck_society_cohort_evidence_links_role",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_society_cohort_evidence_links_confidence",
        ),
        sa.CheckConstraint(
            "(source_record_id IS NOT NULL) "
            "OR (discussion_turn_id IS NOT NULL) "
            "OR (memory_atom_id IS NOT NULL) "
            "OR (persona_id IS NOT NULL)",
            name="ck_society_cohort_evidence_links_at_least_one_origin",
        ),
        sa.CheckConstraint(
            "char_length(excerpt) >= 1",
            name="ck_society_cohort_evidence_links_excerpt_nonempty",
        ),
    )

    op.create_table(
        "society_cohort_rollups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("cohort_count", sa.Integer(), nullable=False),
        sa.Column("persona_count", sa.Integer(), nullable=False),
        sa.Column(
            "weighted_stance_distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "weighted_objection_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "weighted_proof_need_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "social_influence_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "resistance_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "caveats",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "quality_scores",
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
            "run_scope_id", "phase",
            name="uq_society_cohort_rollups_run_phase",
        ),
        sa.CheckConstraint(
            "cohort_count >= 1",
            name="ck_society_cohort_rollups_cohort_count",
        ),
        sa.CheckConstraint(
            "persona_count >= 1",
            name="ck_society_cohort_rollups_persona_count",
        ),
    )


def downgrade() -> None:
    op.drop_table("society_cohort_rollups")
    op.drop_table("society_cohort_evidence_links")
    op.drop_index(
        "ix_society_cohorts_run_phase",
        table_name="society_cohorts",
    )
    op.drop_table("society_cohorts")
