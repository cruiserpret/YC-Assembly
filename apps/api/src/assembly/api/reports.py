"""Phase 7 — `GET /simulations/{id}/report` endpoint.

Returns the 9-section report ONLY when:
  - the simulation row exists, AND
  - status='reported', AND
  - simulation_outputs row exists.

Otherwise returns 404 (simulation missing) or 409 (simulation exists but
report not ready). Never serves a half-built report.

This endpoint does NOT trigger aggregation. It's read-only.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.db import get_session, get_sessionmaker
from assembly.models.output import SimulationOutput
from assembly.models.simulation import Simulation
from assembly.pipeline.evidence_graph import EvidenceGraphService

router = APIRouter()


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _collect_referenced_evidence_ids(report_payload: dict[str, Any]) -> set[UUID]:
    """Walk every JSONB section the report exposes and return every UUID
    that resolves to an evidence_items row reference.

    Picks up:
      - `evidence_anchors: list[UUID]` on every section
      - `simulation_references[*].target_id` where `kind == "evidence_item"`
      - `evidence_ledger.missing[*].evidence_id`
      - `evidence_ledger.claim_traceability[*].source_evidence_id`
      - any `factual_claims[*].source_evidence_id` inside any section

    Other UUIDs (agent_id, debate_turn_id, claim_id, simulation_round_id) are
    NOT included — those reference simulation state, not evidence rows.
    """
    out: set[UUID] = set()

    def _maybe_add(value: Any) -> None:
        if not isinstance(value, str):
            return
        if not _UUID_RE.match(value.strip()):
            return
        try:
            out.add(UUID(value))
        except (ValueError, TypeError):
            pass

    def _walk(value: Any, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                # `evidence_anchors` is the anchor list itself.
                if k == "evidence_anchors" and isinstance(v, list):
                    for u in v:
                        _maybe_add(u)
                    continue
                # `simulation_references` items: only evidence_item kind.
                if k == "simulation_references" and isinstance(v, list):
                    for ref in v:
                        if (
                            isinstance(ref, dict)
                            and ref.get("kind") == "evidence_item"
                        ):
                            _maybe_add(ref.get("target_id"))
                    continue
                # Direct keys that always reference evidence rows.
                if k in {"source_evidence_id", "evidence_id"}:
                    _maybe_add(v)
                    continue
                _walk(v, parent_key=k)
        elif isinstance(value, list):
            for item in value:
                _walk(item, parent_key=parent_key)

    _walk(report_payload)
    return out


async def _build_evidence_anchor_details(
    *,
    sessionmaker,
    simulation_id: UUID,
    referenced_ids: set[UUID],
) -> dict[str, dict[str, Any]]:
    """Look up every referenced evidence_id via EvidenceGraphService and
    return a `{evidence_id_string: details}` map.

    IDs that don't resolve (shouldn't happen in healthy data, but defensive)
    are silently dropped — they'll simply be unhydrated in the UI rather
    than 500 the response.
    """
    if not referenced_ids:
        return {}
    service = EvidenceGraphService(sessionmaker=sessionmaker)
    by_id = await service.get_evidence_by_ids(
        simulation_id, list(referenced_ids)
    )
    out: dict[str, dict[str, Any]] = {}
    for evidence_id, item in by_id.items():
        meta = dict(item.metadata_ or {})
        excerpt = meta.get("source_excerpt")
        out[str(evidence_id)] = {
            "evidence_id": str(evidence_id),
            "kind": item.kind,
            "node_class": item.node_class,
            "source_type": item.source_type,
            "source_url": item.source_url,
            "source_excerpt": excerpt,
            "content_preview": (item.content or "")[:400] if item.content else None,
            "captured_at": (
                item.captured_at.isoformat() if item.captured_at else None
            ),
            "node_class_confidence": float(item.node_class_confidence or 0),
        }
    return out


class SimulationReport(BaseModel):
    """API contract for the 9-section report. Mirrors `simulation_outputs`
    columns 1:1 plus simulation metadata."""

    model_config = ConfigDict(from_attributes=True)

    simulation_id: UUID
    status: str
    schema_version: str
    created_at: datetime

    public_opinion_sentiment: dict[str, Any]
    persuasion_analysis: dict[str, Any]
    market_acceptance_requirement: dict[str, Any]
    product_trajectory: dict[str, Any]
    competitor_analysis: dict[str, Any]
    recommendations: dict[str, Any]
    debate_shift_markers: dict[str, Any]
    confidence: dict[str, Any]
    evidence_ledger: dict[str, Any]

    validator_passed: bool
    validator_notes: dict[str, Any]

    # Phase 8 — hydrated evidence metadata for every UUID referenced in
    # the report's anchors / simulation_references / missing / claim
    # traceability. The frontend renders modals from this map without
    # ever querying evidence_items directly.
    evidence_anchor_details: dict[str, dict[str, Any]] = {}


@router.get(
    "/simulations/{simulation_id}/report",
    response_model=SimulationReport,
    tags=["simulations", "reports"],
)
async def get_report(
    simulation_id: UUID, session: AsyncSession = Depends(get_session),
) -> SimulationReport:
    sim = await session.get(Simulation, simulation_id)
    if sim is None:
        raise HTTPException(status_code=404, detail={"kind": "simulation_not_found"})

    output = (
        await session.execute(
            select(SimulationOutput).where(
                SimulationOutput.simulation_id == simulation_id
            )
        )
    ).scalar_one_or_none()

    if output is None or sim.status != "reported":
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "report_not_ready",
                "current_status": sim.status,
                "guidance": (
                    "The aggregator has not produced a report yet. Poll "
                    "/simulations/{id}/status until status='reported'."
                ),
            },
        )

    # Phase 8: build the evidence_anchor_details map. Walk every UUID the
    # report references (anchors, evidence_item simulation_references,
    # missing entries, claim source_evidence_ids), then hydrate via the
    # graph service so the frontend never queries evidence_items directly.
    payload_for_walk = {
        "public_opinion_sentiment": output.public_opinion_sentiment,
        "persuasion_analysis": output.persuasion_analysis,
        "market_acceptance_requirement": output.market_acceptance_requirement,
        "product_trajectory": output.product_trajectory,
        "competitor_analysis": output.competitor_analysis,
        "recommendations": output.recommendations,
        "debate_shift_markers": output.debate_shift_markers,
        "confidence": output.confidence,
        "evidence_ledger": output.evidence_ledger,
    }
    referenced = _collect_referenced_evidence_ids(payload_for_walk)
    evidence_anchor_details = await _build_evidence_anchor_details(
        sessionmaker=get_sessionmaker(),
        simulation_id=sim.id,
        referenced_ids=referenced,
    )

    return SimulationReport(
        simulation_id=sim.id,
        status=sim.status,
        schema_version=output.schema_version,
        created_at=output.created_at,
        public_opinion_sentiment=output.public_opinion_sentiment,
        persuasion_analysis=output.persuasion_analysis,
        market_acceptance_requirement=output.market_acceptance_requirement,
        product_trajectory=output.product_trajectory,
        competitor_analysis=output.competitor_analysis,
        recommendations=output.recommendations,
        debate_shift_markers=output.debate_shift_markers,
        confidence=output.confidence,
        evidence_ledger=output.evidence_ledger,
        validator_passed=output.validator_passed,
        validator_notes=output.validator_notes,
        evidence_anchor_details=evidence_anchor_details,
    )
