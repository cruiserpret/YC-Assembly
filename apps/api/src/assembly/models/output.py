from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    from assembly.models.simulation import Simulation


class SimulationOutput(Base):
    """The 9 output sections of a completed simulation. One row per simulation.
    Stored as JSONB so the schema can evolve without migrations during prototype."""

    __tablename__ = "simulation_outputs"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    public_opinion_sentiment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    persuasion_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    market_acceptance_requirement: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    product_trajectory: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    competitor_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recommendations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    debate_shift_markers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_ledger: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    validator_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validator_notes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    schema_version: Mapped[str] = mapped_column(String(16), default="v0.1", nullable=False)

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation] = relationship(back_populates="output")
