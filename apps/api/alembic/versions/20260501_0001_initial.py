"""initial schema: simulations + inputs + evidence + agents + edges
                    + rounds + responses + debate turns + outputs
                    + calibration + llm_call_log

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- simulations -------------------------------------------------------
    op.create_table(
        "simulations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("evidence_cutoff_date", sa.Date(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(10, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_latency_ms",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_simulations_user_id", "simulations", ["user_id"])
    op.create_index("ix_simulations_status", "simulations", ["status"])

    # --- simulation_inputs -------------------------------------------------
    op.create_table(
        "simulation_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("product_type", sa.String(128), nullable=False),
        sa.Column("product_name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_structure", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_society", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "competitors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("product_url", sa.String(2048), nullable=True),
        sa.Column("additional_context", sa.Text(), nullable=True),
        sa.Column("raw_brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_simulation_inputs_simulation_id",
        "simulation_inputs",
        ["simulation_id"],
    )

    # --- evidence_items ----------------------------------------------------
    op.create_table(
        "evidence_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_evidence_items_simulation_id", "evidence_items", ["simulation_id"])
    op.create_index("ix_evidence_items_kind", "evidence_items", ["kind"])
    op.create_index("ix_evidence_items_source_type", "evidence_items", ["source_type"])

    # --- agents ------------------------------------------------------------
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_label", sa.String(128), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("buyer_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "traits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_anchors",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agents_simulation_id", "agents", ["simulation_id"])

    # --- agent_edges -------------------------------------------------------
    op.create_table(
        "agent_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "influence_strength",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.5"),
        ),
        sa.Column("cluster_label", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agent_edges_simulation_id", "agent_edges", ["simulation_id"])
    op.create_index("ix_agent_edges_source", "agent_edges", ["source_agent_id"])
    op.create_index("ix_agent_edges_target", "agent_edges", ["target_agent_id"])

    # --- simulation_rounds -------------------------------------------------
    op.create_table(
        "simulation_rounds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("round_type", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_simulation_rounds_simulation_id", "simulation_rounds", ["simulation_id"]
    )
    op.create_unique_constraint(
        "uq_simulation_rounds_sim_round_number",
        "simulation_rounds",
        ["simulation_id", "round_number"],
    )

    # --- agent_responses ---------------------------------------------------
    op.create_table(
        "agent_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "round_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulation_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stance", sa.String(32), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column(
            "objections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "persuasion_drivers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "shift_from_previous",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "state_after",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "raw_output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agent_responses_round_id", "agent_responses", ["round_id"])
    op.create_index("ix_agent_responses_agent_id", "agent_responses", ["agent_id"])
    op.create_index("ix_agent_responses_stance", "agent_responses", ["stance"])

    # --- debate_turns ------------------------------------------------------
    op.create_table(
        "debate_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "round_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulation_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "speaker_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "responding_to_turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("debate_turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("argument", sa.Text(), nullable=False),
        sa.Column(
            "caused_shifts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_debate_turns_round_id", "debate_turns", ["round_id"])

    # --- simulation_outputs ------------------------------------------------
    op.create_table(
        "simulation_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "public_opinion_sentiment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "persuasion_analysis",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "market_acceptance_requirement",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "product_trajectory", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "competitor_analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "recommendations", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "debate_shift_markers", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("confidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "evidence_ledger", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "validator_passed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "validator_notes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "schema_version",
            sa.String(16),
            nullable=False,
            server_default="v0.1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_simulation_outputs_simulation_id",
        "simulation_outputs",
        ["simulation_id"],
    )

    # --- outcome_observations ----------------------------------------------
    op.create_table(
        "outcome_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outcome_type", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column(
            "is_post_cutoff",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_outcome_observations_simulation_id",
        "outcome_observations",
        ["simulation_id"],
    )

    # --- calibration_evaluations -------------------------------------------
    op.create_table(
        "calibration_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcome_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evaluator", sa.String(32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_calibration_evaluations_simulation_id",
        "calibration_evaluations",
        ["simulation_id"],
    )
    op.create_index(
        "ix_calibration_evaluations_outcome_id",
        "calibration_evaluations",
        ["outcome_id"],
    )
    op.create_index(
        "ix_calibration_evaluations_dimension",
        "calibration_evaluations",
        ["dimension"],
    )

    # --- llm_call_log ------------------------------------------------------
    op.create_table(
        "llm_call_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column(
            "prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(10, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "prompt_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_llm_call_log_simulation_id", "llm_call_log", ["simulation_id"])
    op.create_index("ix_llm_call_log_stage", "llm_call_log", ["stage"])


def downgrade() -> None:
    op.drop_table("llm_call_log")
    op.drop_table("calibration_evaluations")
    op.drop_table("outcome_observations")
    op.drop_table("simulation_outputs")
    op.drop_table("debate_turns")
    op.drop_table("agent_responses")
    op.drop_table("simulation_rounds")
    op.drop_table("agent_edges")
    op.drop_table("agents")
    op.drop_table("evidence_items")
    op.drop_table("simulation_inputs")
    op.drop_table("simulations")
