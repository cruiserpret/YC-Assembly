from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    from assembly.models.round import AgentResponse
    from assembly.models.simulation import Simulation


class Agent(Base):
    """Buyer-state agent — NOT a persona. Each agent represents a state of the
    market, grounded in real evidence via `evidence_anchors`."""

    __tablename__ = "agents"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    segment_label: Mapped[str] = mapped_column(String(128), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # buyer_state schema (V0): {
    #   current_alternatives: [string],
    #   budget: string,
    #   trust_threshold: string,
    #   switching_trigger: string,
    #   fear: string,
    #   desire: string,
    #   influence_score: float,  # 0..1
    #   price_sensitivity: string,
    #   current_behavior: string,
    #   objection_pattern: string,
    #   emotional_state: string,
    # }
    buyer_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    traits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_anchors: Mapped[list[Any]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation] = relationship(back_populates="agents")
    responses: Mapped[list[AgentResponse]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
    )


class AgentEdge(Base):
    """Directed influence edge. Used by the social-influence round to sample
    debates along high-weight edges instead of random pairings."""

    __tablename__ = "agent_edges"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_agent_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_agent_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    influence_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    cluster_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation] = relationship(back_populates="edges")
