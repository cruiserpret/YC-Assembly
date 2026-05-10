"""Phase 7 — `run_aggregation` orchestrator.

Composes:
  1. reader.load_report_inputs()           — read-only state + graph bundles
  2. mechanical.build_*                    — debate shifts, confidence, evidence ledger
  3. synthesis.run_call_a / b / c          — three LLM calls
  4. claim_validator.validate_claim        — every factual claim
  5. persistence.write_simulation_output   — one row, refuses overwrite

Status transitions are owned by the caller (orchestrator). This service
just builds + persists; it does not flip status.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.config import get_settings
from assembly.embeddings.provider import EmbeddingProvider
from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.provider import LLMProvider
from assembly.pipeline.aggregation.claim_validator import validate_claim
from assembly.pipeline.aggregation.mechanical import (
    build_confidence_section,
    build_debate_shift_markers,
    build_evidence_ledger_section,
)
from assembly.pipeline.aggregation.persistence import (
    SimulationOutputAlreadyExists,
    write_simulation_output,
)
from assembly.pipeline.aggregation.reader import load_report_inputs
from assembly.pipeline.aggregation.synthesis import (
    collect_factual_claims,
    run_call_a,
    run_call_b,
    run_call_c,
)
from assembly.models.output import SimulationOutput

logger = logging.getLogger(__name__)


class AggregationFailed(Exception):
    """Raised when aggregation cannot complete cleanly — schema repair
    exhausted, claim validation rejected the report, or persistence
    refused. Caller marks the simulation `failed`."""


async def run_aggregation_v7(
    *,
    simulation_id: UUID,
    sessionmaker: async_sessionmaker,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
) -> SimulationOutput:
    """End-to-end aggregation. Returns the persisted SimulationOutput row.

    Caller is responsible for status transitions (`aggregating` → `reported`).
    """
    settings = get_settings()
    model = settings.llm_synthesis_model

    bundle = await load_report_inputs(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        embedding_provider=embedding_provider,
    )

    # Mechanical sections — pure aggregation, deterministic.
    debate_section = build_debate_shift_markers(bundle)
    confidence_section = build_confidence_section(bundle)
    evidence_ledger_section = build_evidence_ledger_section(bundle)

    # LLM synthesis (3 calls).
    try:
        section_a = await run_call_a(
            sessionmaker=sessionmaker, bundle=bundle,
            provider=provider, model=model,
        )
        section_b = await run_call_b(
            sessionmaker=sessionmaker, bundle=bundle,
            provider=provider, model=model,
        )
        section_c = await run_call_c(
            sessionmaker=sessionmaker, bundle=bundle,
            section_a=section_a, section_b=section_b,
            provider=provider, model=model,
        )
    except LLMRepairExhausted as e:
        raise AggregationFailed(
            f"aggregation synthesis exhausted repair attempts: {e}"
        ) from e

    # Quality gate (Phase 7 follow-up): pre-build a (evidence_id → source_url)
    # map from the bundle so accepted claim rows carry the bound evidence's
    # real source_url. Persisting source_url=None when the evidence has a
    # real URL would lose that audit information.
    url_by_evidence_id: dict[UUID, str | None] = {}
    for b in (
        bundle.competitor_evidence, bundle.pricing_evidence,
        bundle.trust_barrier_evidence, bundle.positioning_evidence,
        bundle.market_acceptance_evidence,
    ):
        for r in b.ranked:
            url_by_evidence_id[r.item.id] = r.item.source_url
        for it in b.missing:
            url_by_evidence_id[it.id] = it.source_url
    for items in bundle.missing_evidence.by_node_class.values():
        for it in items:
            url_by_evidence_id[it.id] = it.source_url
    for ct in bundle.claim_traceability:
        if ct.source_evidence is not None:
            url_by_evidence_id[ct.source_evidence.id] = ct.source_evidence.source_url

    # Claim validation — every factual_claim must bind to evidence.
    factual_claims = collect_factual_claims(section_a, section_b, section_c)
    rejected: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for c in factual_claims:
        result = await validate_claim(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            text=c.text,
            source_evidence_id=c.source_evidence_id,
            source_excerpt=c.source_excerpt,
            claim_type=c.claim_type,
            basis=c.basis,
        )
        if result.passed:
            accepted.append({
                "text": c.text,
                "source_evidence_id": c.source_evidence_id,
                "source_excerpt": c.source_excerpt,
                # Carry the bound evidence's real source_url when present
                # so the persisted claims row has the same provenance the
                # graph knows about. Falls back to None for rows whose
                # source has no URL (e.g. user_input).
                "source_url": url_by_evidence_id.get(c.source_evidence_id),
                "claim_type": c.claim_type,
                "basis": c.basis,
                "confidence": c.confidence,
            })
        else:
            rejected.append({
                "text": c.text,
                "violations": [
                    {"rule_id": v.rule_id, "detail": v.detail}
                    for v in result.violations
                ],
            })

    if rejected:
        # Per Phase 7 plan: do not write a report whose factual claims
        # cannot be evidence-bound. Surface as failure so the orchestrator
        # marks the simulation 'failed' — never silently drop claims.
        raise AggregationFailed(
            f"{len(rejected)} factual claim(s) failed claim_validator and could "
            f"not be bound to evidence. First failures: "
            f"{rejected[:3]}"
        )

    # Build the JSONB payload. Each column maps 1:1 to a section.
    sections = {
        "public_opinion_sentiment": section_a.public_opinion_sentiment.model_dump(mode="json"),
        # Persuasion analysis is the union of "persuaded" + "not_persuaded".
        "persuasion_analysis": {
            "persuaded": section_a.persuaded.model_dump(mode="json"),
            "not_persuaded": section_a.not_persuaded.model_dump(mode="json"),
        },
        "market_acceptance_requirement": section_a.market_acceptance_requirement.model_dump(mode="json"),
        "product_trajectory": section_b.product_trajectory.model_dump(mode="json"),
        "competitor_analysis": section_b.competitor_analysis.model_dump(mode="json"),
        "recommendations": {
            "target_audience": section_c.target_audience.model_dump(mode="json"),
            "positioning": section_c.positioning.model_dump(mode="json"),
            "price_structure": section_c.price_structure.model_dump(mode="json"),
        },
        "debate_shift_markers": debate_section.model_dump(mode="json"),
        "confidence": confidence_section.model_dump(mode="json"),
        "evidence_ledger": evidence_ledger_section.model_dump(mode="json"),
    }

    validator_notes: dict[str, Any] = {
        "claims_accepted": len(accepted),
        "claims_rejected": 0,
    }

    try:
        row = await write_simulation_output(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            sections=sections,
            claims=accepted,
            validator_passed=True,
            validator_notes=validator_notes,
        )
    except SimulationOutputAlreadyExists as e:
        raise AggregationFailed(
            f"simulation_outputs row already exists for {simulation_id}; "
            "refusing to overwrite. Operators must call "
            "clear_simulation_output() explicitly."
        ) from e

    logger.info(
        "aggregation.completed simulation=%s claims=%d markers=%d",
        simulation_id, len(accepted), len(debate_section.markers),
    )
    return row
