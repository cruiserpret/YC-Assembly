"""Simulation endpoints.

Phase 6.5 wired — POST /simulations enqueues an arq job; clients poll via
GET /simulations/{id}/status.
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from assembly.api.deps import db_session
from assembly.config import get_settings
from assembly.models.output import SimulationOutput
from assembly.models.simulation import Simulation, SimulationInput
from assembly.schemas.brief import SimulationBriefIn, SimulationCreated
from assembly.schemas.report import SimulationReport, SimulationStatus

logger = logging.getLogger(__name__)
router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(db_session)]


@router.post(
    "",
    response_model=SimulationCreated,
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Submit a brief and enqueue a simulation",
)
async def create_simulation(
    brief: SimulationBriefIn,
    session: SessionDep,
    request: Request,
) -> SimulationCreated:
    """Persist the brief, enqueue an arq job, return 202.

    Poll `GET /simulations/{id}/status` for progress."""
    sim = Simulation(
        status="pending",
        evidence_cutoff_date=brief.evidence_cutoff_date,
        progress={"stage": "pending"},
    )

    sim_input = SimulationInput(
        product_type=brief.product_type,
        product_name=brief.product_name,
        description=brief.description,
        price_structure=brief.price_structure.model_dump(),
        target_society=brief.target_society.model_dump(),
        competitors=[c.model_dump() for c in brief.competitors],
        product_url=str(brief.product_url) if brief.product_url else None,
        additional_context=brief.additional_context,
        raw_brief=brief.model_dump(mode="json"),
    )
    sim.input = sim_input

    session.add(sim)
    await session.commit()
    await session.refresh(sim)

    # Phase 6.5: enqueue the arq job. The pool is set up in the lifespan
    # handler in main.py and stored on app.state. If the pool is missing
    # (e.g. tests), we silently no-op the enqueue and return — tests can
    # call run_full_pipeline directly.
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is not None:
        try:
            await pool.enqueue_job("run_pipeline", str(sim.id))
            logger.info("simulations.enqueued sim=%s", sim.id)
        except Exception as e:
            # Failure to enqueue should not lose the row — caller can re-enqueue.
            logger.warning(
                "simulations.enqueue_failed sim=%s err=%s", sim.id, e
            )
    else:
        logger.info(
            "simulations.no_arq_pool sim=%s — running outside worker context",
            sim.id,
        )

    return SimulationCreated(id=sim.id, status=sim.status, created_at=sim.created_at)  # type: ignore[arg-type]


@router.get(
    "",
    response_model=list[SimulationStatus],
    summary="List recent simulations",
)
async def list_simulations(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[SimulationStatus]:
    stmt = (
        select(Simulation)
        .order_by(Simulation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    sims = result.scalars().all()
    return [
        SimulationStatus(
            id=s.id,
            status=s.status,  # type: ignore[arg-type]
            created_at=s.created_at,
            started_at=s.started_at,
            completed_at=s.completed_at,
            error=s.error,
        )
        for s in sims
    ]


@router.get(
    "/{simulation_id}",
    response_model=SimulationReport,
    summary="Fetch a simulation's status (and report if completed)",
)
async def get_simulation(
    simulation_id: UUID,
    session: SessionDep,
) -> SimulationReport:
    stmt = (
        select(Simulation)
        .where(Simulation.id == simulation_id)
        .options(selectinload(Simulation.output))
    )
    result = await session.execute(stmt)
    sim = result.scalar_one_or_none()
    if sim is None:
        raise HTTPException(status_code=404, detail="simulation not found")

    output: SimulationOutput | None = sim.output

    report = SimulationReport(
        id=sim.id,
        status=sim.status,  # type: ignore[arg-type]
        created_at=sim.created_at,
        completed_at=sim.completed_at,
    )
    if output is not None:
        report.public_opinion_sentiment = output.public_opinion_sentiment  # type: ignore[assignment]
        report.persuasion_analysis = output.persuasion_analysis  # type: ignore[assignment]
        report.market_acceptance_requirement = output.market_acceptance_requirement  # type: ignore[assignment]
        report.product_trajectory = output.product_trajectory  # type: ignore[assignment]
        report.competitor_analysis = output.competitor_analysis  # type: ignore[assignment]
        report.recommendations = output.recommendations  # type: ignore[assignment]
        report.debate_shift_markers = output.debate_shift_markers.get("markers", [])  # type: ignore[assignment]
        report.confidence = output.confidence  # type: ignore[assignment]
        report.evidence_ledger = output.evidence_ledger  # type: ignore[assignment]
        report.validator_passed = output.validator_passed
        report.validator_notes = output.validator_notes
        report.schema_version = output.schema_version

    return report


@router.get(
    "/{simulation_id}/status",
    response_model=SimulationStatus,
    summary="Lightweight status check for polling",
)
async def get_simulation_status(
    simulation_id: UUID,
    session: SessionDep,
) -> SimulationStatus:
    stmt = select(Simulation).where(Simulation.id == simulation_id)
    sim = (await session.execute(stmt)).scalar_one_or_none()
    if sim is None:
        raise HTTPException(status_code=404, detail="simulation not found")

    return SimulationStatus(
        id=sim.id,
        status=sim.status,  # type: ignore[arg-type]
        created_at=sim.created_at,
        started_at=sim.started_at,
        completed_at=sim.completed_at,
        error=sim.error,
        failed_stage=sim.failed_stage,
        progress=sim.progress or None,
        current_round=(
            (sim.progress or {}).get("round_index")
            if sim.status == "simulating"
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Phase 6.5 — debug-only raw-state endpoint, gated by config flag
# ---------------------------------------------------------------------------


@router.get(
    "/{simulation_id}/raw-state",
    summary="DEBUG-ONLY: full raw simulation state (rounds + responses + turns)",
    include_in_schema=False,
)
async def get_simulation_raw_state(
    simulation_id: UUID,
    session: SessionDep,
) -> dict:
    """Debug-only endpoint exposing the raw simulation transcript.

    Gated by `ASSEMBLY_EXPOSE_RAW_STATE`. Returns 404 in production. NOT
    intended for end users — Phase 7 ships the user-facing 9-section
    report on `GET /simulations/{id}` once `simulation_outputs` is written.
    """
    settings = get_settings()
    if not settings.expose_raw_state:
        raise HTTPException(
            status_code=404,
            detail="raw-state endpoint disabled (ASSEMBLY_EXPOSE_RAW_STATE=false)",
        )

    from assembly.models.agent import Agent as AgentORM
    from assembly.models.evidence import EvidenceItem as EvidenceItemORM
    from assembly.models.round import (
        AgentResponse as AgentResponseORM,
    )
    from assembly.models.round import (
        DebateTurn as DebateTurnORM,
    )
    from assembly.models.round import (
        SimulationRound as SimulationRoundORM,
    )

    sim = (
        await session.execute(
            select(Simulation).where(Simulation.id == simulation_id)
        )
    ).scalar_one_or_none()
    if sim is None:
        raise HTTPException(status_code=404, detail="simulation not found")

    rounds_rows = (
        await session.execute(
            select(SimulationRoundORM)
            .where(SimulationRoundORM.simulation_id == simulation_id)
            .order_by(SimulationRoundORM.round_number)
        )
    ).scalars().all()

    rounds_out: list[dict] = []
    for r in rounds_rows:
        responses = (
            await session.execute(
                select(AgentResponseORM).where(AgentResponseORM.round_id == r.id)
            )
        ).scalars().all()
        debate_turns = (
            await session.execute(
                select(DebateTurnORM).where(DebateTurnORM.round_id == r.id)
            )
        ).scalars().all()
        rounds_out.append({
            "round_number": r.round_number,
            "round_type": r.round_type,
            "started_at": r.started_at,
            "completed_at": r.completed_at,
            "summary": r.summary,
            "agent_responses": [
                {
                    "agent_id": str(ar.agent_id),
                    "stance": ar.stance,
                    "reasoning": ar.reasoning,
                    "objections": ar.objections,
                    "persuasion_drivers": ar.persuasion_drivers,
                    "shift_from_previous": ar.shift_from_previous,
                }
                for ar in responses
            ],
            "debate_turns": [
                {
                    "speaker_agent_id": str(t.speaker_agent_id),
                    "target_agent_id": str(t.target_agent_id) if t.target_agent_id else None,
                    "argument": t.argument,
                    "caused_shifts": t.caused_shifts,
                }
                for t in debate_turns
            ],
        })

    return {
        "id": str(sim.id),
        "status": sim.status,
        "rounds": rounds_out,
    }
