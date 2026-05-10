"""Phase 10A — API/demo run-tracking tables.

Revision ID: 0011_phase_10_a
Revises: 0010_phase_9_e
Create Date: 2026-05-08

Why:
  Phase 10A wraps the existing 9-phase pipeline behind a frontend-ready
  API surface. We need a lightweight run-tracking table so the
  POST /assembly/runs endpoint can return a run_id, GET endpoints can
  poll status, and report endpoints can resolve artifact paths.

Tables (additive only):
  assembly_runs          — one row per founder-input run
  assembly_run_artifacts — pointers to generated artifact files per run
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_phase_10_a"
down_revision: str | None = "0010_phase_9_e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RUN_MODES = ("fixture_demo", "live_founder_brief")
_RUN_STATUSES = (
    "pending", "running", "complete", "failed", "skeletal",
)
_RUN_STAGES = (
    "validating_brief",
    "planning_evidence",
    "retrieving_evidence",
    "scoring_evidence",
    "building_personas",
    "enriching_psychology",
    "running_individual_simulation",
    "running_group_discussion",
    "repairing_incomplete_outputs",
    "building_cohorts",
    "inferring_simulated_intent",
    "running_society_wide_debate",
    "generating_report",
    "complete",
    "failed",
)
_ARTIFACT_TYPES = (
    "report_json", "report_markdown",
    "personas_json", "cohorts_json", "discussion_json", "intent_json",
    "audit_json", "discussion_quality_json", "cohort_quality_json",
    "intent_quality_json",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "assembly_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(128), nullable=True, index=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column(
            "product_brief",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_stage", sa.String(48), nullable=False),
        sa.Column(
            "stage_progress",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "artifact_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("linked_run_scope_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"mode IN {_in_clause(_RUN_MODES)}",
            name="ck_assembly_runs_mode",
        ),
        sa.CheckConstraint(
            f"status IN {_in_clause(_RUN_STATUSES)}",
            name="ck_assembly_runs_status",
        ),
        sa.CheckConstraint(
            f"current_stage IN {_in_clause(_RUN_STAGES)}",
            name="ck_assembly_runs_current_stage",
        ),
    )
    op.create_index(
        "ix_assembly_runs_status",
        "assembly_runs",
        ["status"],
    )

    op.create_table(
        "assembly_run_artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assembly_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("artifact_type", sa.String(48), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column(
            "content_type",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'application/json'"),
        ),
        sa.Column(
            "is_user_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "run_id", "artifact_type",
            name="uq_assembly_run_artifacts_run_type",
        ),
        sa.CheckConstraint(
            f"artifact_type IN {_in_clause(_ARTIFACT_TYPES)}",
            name="ck_assembly_run_artifacts_type",
        ),
        sa.CheckConstraint(
            "char_length(path) >= 1",
            name="ck_assembly_run_artifacts_path_nonempty",
        ),
    )


def downgrade() -> None:
    op.drop_table("assembly_run_artifacts")
    op.drop_index("ix_assembly_runs_status", table_name="assembly_runs")
    op.drop_table("assembly_runs")
