"""Phase 6.5 — Operational progress object.

Stored in `simulations.progress` JSONB. Updated by the orchestrator at every
status transition and by the engine on every round completion. Read by
`GET /simulations/{id}/status` so clients (eventually a frontend) can show
where a simulation is.

This is OPERATIONAL state — round counts, agent counts, current stage. It
is NOT product-shaped — no predictions, no scores, no verdicts.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assembly.models.simulation import SIMULATION_STATUS_VALUES, Simulation


# String enum mirrored on the model. Subset that's used as `progress.stage`.
ProgressStage = Literal[
    "pending",
    "parsing",
    "evidence_building",
    "evidence_graph_building",  # Phase 6.75
    "society_building",
    "simulating",
    "simulation_completed",
    "aggregating",
    "reported",
    "failed",
]


class Progress(BaseModel):
    """Per-simulation operational progress object.

    All non-mandatory fields default to None; the orchestrator and engine
    fill in stage-specific fields as they apply.
    """

    model_config = ConfigDict(extra="forbid")

    stage: ProgressStage = "pending"
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Stage-specific (all optional; relevant for `simulating` stage).
    current_round: str | None = None
    round_index: int | None = None
    total_rounds: int = 7
    agents_completed: int | None = None
    agents_total: int | None = None

    # Stage-specific for `evidence_building`.
    evidence_items_collected: int | None = None
    fetched_pages: int | None = None

    notes: str | None = None

    def with_update(self, **changes) -> "Progress":
        """Return a new Progress with `changes` applied + last_updated_at refreshed."""
        data = self.model_dump()
        data.update(changes)
        data["last_updated_at"] = datetime.now(UTC)
        return Progress(**data)


# ---------------------------------------------------------------------------
# Helpers — write progress + status atomically
# ---------------------------------------------------------------------------


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"parsing", "failed"},
    "parsing": {"evidence_building", "failed"},
    "evidence_building": {"evidence_graph_building", "society_building", "failed"},
    "evidence_graph_building": {"society_building", "failed"},
    "society_building": {"simulating", "failed"},
    "simulating": {"simulation_completed", "failed", "aggregating"},
    "simulation_completed": {"aggregating", "reported"},  # Phase 7
    "aggregating": {"reported", "failed"},
    "reported": set(),
    "failed": {  # retry from any stage
        "parsing", "evidence_building", "evidence_graph_building",
        "society_building", "simulating",
    },
}


class IllegalStatusTransition(Exception):
    """Raised when a status transition is not in the allowed graph."""


def _validate_transition(current: str, next_: str) -> None:
    if current == next_:
        return  # idempotent reassignment is fine
    if next_ not in SIMULATION_STATUS_VALUES:
        raise IllegalStatusTransition(
            f"unknown target status {next_!r} (must be one of {SIMULATION_STATUS_VALUES})"
        )
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if next_ not in allowed:
        raise IllegalStatusTransition(
            f"illegal transition {current!r} -> {next_!r}; allowed: {sorted(allowed)}"
        )


async def update_status_and_progress(
    sessionmaker: async_sessionmaker,
    *,
    simulation_id: UUID,
    new_status: ProgressStage | None = None,
    progress_changes: dict | None = None,
    failed_stage: str | None = None,
    error: dict | None = None,
) -> None:
    """Atomically update `simulations.status`, `simulations.progress`, and
    optionally failed_stage / error in a single transaction.

    Validates the transition. Raises `IllegalStatusTransition` on bad moves.
    """
    async with sessionmaker() as session:
        async with session.begin():
            sim = await session.get(Simulation, simulation_id)
            if sim is None:
                raise ValueError(f"simulation {simulation_id} not found")

            if new_status is not None:
                _validate_transition(sim.status, new_status)
                sim.status = new_status

            if progress_changes is not None:
                current_progress = Progress(**(sim.progress or {}))
                # If we're advancing the stage, sync progress.stage too.
                if new_status is not None and "stage" not in progress_changes:
                    progress_changes["stage"] = new_status
                updated = current_progress.with_update(**progress_changes)
                sim.progress = updated.model_dump(mode="json")

            if failed_stage is not None:
                sim.failed_stage = failed_stage
            if error is not None:
                sim.error = error


async def read_progress(
    session: AsyncSession, simulation_id: UUID
) -> Progress:
    """Read the current progress object for a simulation."""
    sim = await session.get(Simulation, simulation_id)
    if sim is None:
        raise ValueError(f"simulation {simulation_id} not found")
    return Progress(**(sim.progress or {}))


__all__ = [
    "IllegalStatusTransition",
    "Progress",
    "ProgressStage",
    "read_progress",
    "update_status_and_progress",
]
