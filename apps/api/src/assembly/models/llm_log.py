from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    from assembly.models.simulation import Simulation


class LLMCallLog(Base):
    """One row per LLM call. Powers the cost dashboard, per-stage cost breakdown,
    and the prompt-audit step that proves no post-cutoff evidence leaked into a
    backtest run."""

    __tablename__ = "llm_call_log"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    stage: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional structured snapshot of system+user prompt and parsed output, for
    # prompt-audit during validation. Heavy; can be turned off in production via
    # a future feature flag.
    prompt_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation | None] = relationship(back_populates="llm_calls")
