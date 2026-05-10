from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    from assembly.models.agent import Agent, AgentEdge
    from assembly.models.evidence import EvidenceItem
    from assembly.models.llm_log import LLMCallLog
    from assembly.models.output import SimulationOutput
    from assembly.models.round import SimulationRound


# Phase 6.5 — granular status enum. Operational, not product-shaped.
# `aggregating` and `reported` are RESERVED for Phase 7.
SIMULATION_STATUS_VALUES = (
    "pending",                    # row created, job not yet picked up
    "parsing",                    # intake_parser stage
    "evidence_building",          # evidence_builder stage (incl. retrieval if enabled)
    "evidence_graph_building",    # Phase 6.75 — typed graph + dedup + embeddings
    "society_building",           # society_builder stage
    "simulating",                 # engine.run_simulation stage
    "simulation_completed",       # raw state done; aggregation NOT yet run
    "aggregating",                # Phase 7 — RESERVED
    "reported",                   # Phase 7 — RESERVED
    "failed",                     # any stage threw; failed_stage stored
)


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[UUIDPk]
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    evidence_cutoff_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    total_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0, nullable=False)
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Phase 6.5 — operational columns added by migration 0002_phase_6_5
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    parsed_pio: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Phase 6.75 — idempotent resume flag for the evidence_graph_building stage.
    # A non-null value means the graph build has completed for this simulation;
    # the orchestrator skips the stage on retry. Explicit rebuild requires
    # nulling this column AND clearing evidence_edges for the simulation.
    evidence_graph_built_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[CreatedAt]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    input: Mapped[SimulationInput | None] = relationship(
        back_populates="simulation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    evidence: Mapped[list[EvidenceItem]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
    )
    agents: Mapped[list[Agent]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
    )
    edges: Mapped[list[AgentEdge]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
    )
    rounds: Mapped[list[SimulationRound]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
        order_by="SimulationRound.round_number",
    )
    output: Mapped[SimulationOutput | None] = relationship(
        back_populates="simulation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    llm_calls: Mapped[list[LLMCallLog]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
    )


class SimulationInput(Base):
    """Normalized brief, snapshot of what the user submitted plus the parsed shape."""

    __tablename__ = "simulation_inputs"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    product_type: Mapped[str] = mapped_column(String(128), nullable=False)
    product_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_structure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_society: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    competitors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    product_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    additional_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_brief: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation] = relationship(back_populates="input")
