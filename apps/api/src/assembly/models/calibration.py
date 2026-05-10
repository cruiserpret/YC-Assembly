from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    pass


CALIBRATION_DIMENSIONS = (
    "sentiment",
    "segment",
    "objection",
    "persuasion",
    "resistance",
    "competitor",
    "pricing",
    "trajectory",
    "confidence",
)

CALIBRATION_EVALUATORS = ("human", "llm_judge", "programmatic")


class OutcomeObservation(Base):
    """Real-world outcome data for a simulation, ingested AFTER the simulation
    output is locked. `is_post_cutoff=True` means this observation occurred after
    the simulation's `evidence_cutoff_date` and must NOT have been visible during
    the simulation."""

    __tablename__ = "outcome_observations"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    outcome_type: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_post_cutoff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[CreatedAt]


class CalibrationEvaluation(Base):
    """One score per (simulation, outcome, dimension) — e.g. did Assembly's
    `sentiment` output match the post-launch reality?"""

    __tablename__ = "calibration_evaluations"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    outcome_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outcome_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluator: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[CreatedAt]
