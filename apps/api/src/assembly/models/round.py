from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    from assembly.models.agent import Agent
    from assembly.models.simulation import Simulation


ROUND_TYPES = (
    "baseline",
    "first_exposure",
    "objection_formation",
    "competitor_comparison",
    "proof_exposure",
    "social_influence",
    "final_stance",
)

STANCE_VALUES = (
    "strongly_interested",
    "mildly_interested",
    "curious_hesitant",
    "confused",
    "skeptical",
    "resistant",
)


class SimulationRound(Base):
    __tablename__ = "simulation_rounds"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    round_type: Mapped[str] = mapped_column(String(32), nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation] = relationship(back_populates="rounds")
    responses: Mapped[list[AgentResponse]] = relationship(
        back_populates="round",
        cascade="all, delete-orphan",
    )
    debate_turns: Mapped[list[DebateTurn]] = relationship(
        back_populates="round",
        cascade="all, delete-orphan",
    )


class AgentResponse(Base):
    __tablename__ = "agent_responses"

    id: Mapped[UUIDPk]
    round_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_rounds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    stance: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    objections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    persuasion_drivers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    shift_from_previous: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state_after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[CreatedAt]

    round: Mapped[SimulationRound] = relationship(back_populates="responses")
    agent: Mapped[Agent] = relationship(back_populates="responses")


class DebateTurn(Base):
    """A single turn in a debate during the social-influence round."""

    __tablename__ = "debate_turns"

    id: Mapped[UUIDPk]
    round_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_rounds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    speaker_agent_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_agent_id: Mapped[UUIDFk | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
    )
    responding_to_turn_id: Mapped[UUIDFk | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("debate_turns.id", ondelete="SET NULL"),
        nullable=True,
    )

    argument: Mapped[str] = mapped_column(Text, nullable=False)
    caused_shifts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    created_at: Mapped[CreatedAt]

    round: Mapped[SimulationRound] = relationship(back_populates="debate_turns")
