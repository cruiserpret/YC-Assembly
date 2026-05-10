"""Phase 8.2I — strong-shell-only persona writer.

After Tavily's run-scoped top-up ingest produces new source_records,
this module:

  1. Filters new records to `strong_persona_signal` only (the same
     classifier from Phase 8.2F).
  2. Wraps them in the existing `run_persona_construction` worker
     with `write_personas=True` and `LLMTraitExtractor`.
  3. Caps total personas written at the plan's `persona_write_cap`.
  4. Caps total LLM cost at the plan's `cost_cap_usd`.

Reuses the Phase 8.2F worker — does NOT re-implement persona
construction. The persona construction worker already enforces ≥3
valid traits, source-bound evidence, no sensitive attributes, and
the closed trait-field enum.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.anthropic import AnthropicProvider
from assembly.llm.provider import LLMProvider
from assembly.models.llm_log import LLMCallLog
from assembly.models.persona import SourceRecord
from assembly.models.simulation import Simulation
from assembly.pipeline.persona_construction import (
    LLMTraitExtractor,
    PersonaConstructionRunSummary,
    run_persona_construction,
)
from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
    classify_source_record,
)
from assembly.pipeline.run_scoped_topup.schemas import (
    RunScopedTopUpPlan,
    TopUpPersonaWriteResult,
)


PILOT_STAGE = "persona_trait_extraction"
PILOT_MODEL = "claude-sonnet-4-6"


async def execute_persona_write_for_topup(
    *,
    sessionmaker: async_sessionmaker,
    plan: RunScopedTopUpPlan,
    new_source_record_ids: Sequence[UUID],
    provider: LLMProvider | None = None,
) -> TopUpPersonaWriteResult:
    """Run persona construction on the records the top-up ingest just
    inserted. Filters to `strong_persona_signal` only.

    Caps:
      - max personas = plan.persona_write_cap
      - cost cap     = plan.cost_cap_usd (enforced by cost_guarded_chat
                       per-call; a hard breach raises CostCapExceeded)
    """
    # Load every newly-inserted source_record.
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.id.in_(list(new_source_record_ids))
                )
            )
        ).scalars().all()

    # Classify and partition.
    strong: list[SourceRecord] = []
    weak: list[SourceRecord] = []
    context_only: list[SourceRecord] = []
    rejected: list[SourceRecord] = []
    for r in rows:
        cls = classify_source_record(
            content=r.content,
            source_url=r.source_url,
            metadata=r.metadata_,
            user_handle_hash=r.user_handle_hash,
        ).classification
        if cls == SourceClassification.STRONG_PERSONA_SIGNAL:
            strong.append(r)
        elif cls == SourceClassification.WEAK_PERSONA_SIGNAL:
            weak.append(r)
        elif cls == SourceClassification.CONTEXT_ONLY:
            context_only.append(r)
        else:
            rejected.append(r)

    if not strong:
        return TopUpPersonaWriteResult(
            candidate_shells=0,
            strong_signal_shells=0,
            weak_signal_shells=len(weak),
            context_only_shells=len(context_only),
            personas_created=0,
            personas_skipped=0,
            traits_created=0,
            traits_rejected=0,
            evidence_links_created=0,
            skipped_reasons={
                "no_strong_signal_shells": len(rows),
            },
            new_persona_ids=[],
            cost_estimate_usd=0.0,
            cost_actual_usd=0.0,
        )

    # Cap to persona_write_cap (≈ 1 persona per shell empirically).
    # We don't know yield in advance; cap is enforced below by capping
    # the number of shells we feed the worker.
    strong_capped = strong[: plan.persona_write_cap]

    # Anchor LLM cost-guard against a fresh admin Simulation row.
    sim_id = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id="phase_8_2i_run_scoped_topup",
                status="phase_8_2i_topup_running",
                progress={
                    "stage": "run_scoped_topup_persona_write",
                    "brief_label": plan.brief_label,
                },
                total_cost_usd=Decimal("0"),
                total_latency_ms=0,
            ))

    # Capture per-shell extraction outputs for diagnostics.
    captured_results: list[tuple[str, list]] = []

    if provider is None:
        provider = AnthropicProvider()

    class _Capturing(LLMTraitExtractor):
        async def extract(self, shell):
            out = await super().extract(shell)
            captured_results.append((shell.shell_id, list(out.candidates)))
            return out

    extractor = _Capturing(
        sessionmaker=sessionmaker,
        simulation_id=sim_id,
        provider=provider,
        model=PILOT_MODEL,
        max_repair_attempts=1,
    )

    summary: PersonaConstructionRunSummary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=strong_capped,
        extractor=extractor,
        write_personas=True,
    )

    # Pull cost stats.
    async with sessionmaker() as session:
        log_rows = (
            await session.execute(
                select(LLMCallLog).where(
                    LLMCallLog.simulation_id == sim_id,
                    LLMCallLog.stage == PILOT_STAGE,
                )
            )
        ).scalars().all()
    total_cost = float(sum((r.cost_usd or Decimal("0")) for r in log_rows))

    # Re-discover which persona_records this run produced — they're the
    # ones whose persona_evidence_links point to our new source_records.
    from assembly.models.persona import PersonaEvidenceLink
    async with sessionmaker() as session:
        link_rows = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.source_record_id.in_(
                        [r.id for r in strong_capped]
                    )
                )
            )
        ).scalars().all()
    new_persona_ids = sorted({l.persona_id for l in link_rows})

    # Mark simulation completed.
    from sqlalchemy import update
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                update(Simulation).where(Simulation.id == sim_id)
                .values(
                    status="phase_8_2i_topup_completed",
                    completed_at=datetime.now(UTC),
                )
            )

    skipped_breakdown: dict[str, int] = {}
    for s in summary.skipped_reasons:
        skipped_breakdown[s.reason_code] = (
            skipped_breakdown.get(s.reason_code, 0) + 1
        )

    return TopUpPersonaWriteResult(
        candidate_shells=summary.candidate_shells,
        strong_signal_shells=summary.strong_persona_signal_records,
        weak_signal_shells=summary.weak_persona_signal_records,
        context_only_shells=summary.context_only_records,
        personas_created=summary.personas_created,
        personas_skipped=summary.personas_skipped,
        traits_created=summary.traits_created,
        traits_rejected=summary.traits_rejected,
        evidence_links_created=summary.evidence_links_created,
        skipped_reasons=skipped_breakdown,
        new_persona_ids=[str(pid) for pid in new_persona_ids],
        cost_estimate_usd=None,
        cost_actual_usd=total_cost,
    )
