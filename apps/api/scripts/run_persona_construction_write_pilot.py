"""Phase 8.2F — bounded write-mode pilot.

Runs the persona construction worker against the strong_persona_signal
shells produced by Phase 8.2F.5's expansion ingest. The pilot is
deliberately narrow:

  - filter to source_records with classification = strong_persona_signal
  - run LLMTraitExtractor through cost_guarded_chat (stage:
    'persona_trait_extraction'), cost-capped at $2.00
  - write_personas=True
  - persona discipline: ≥ 3 valid traits, every direct/inferred trait
    source-bound to a verbatim excerpt, display_name generated only

Operator-only. NO graph / cluster / simulation / UI writes happen.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import select


def _load_env() -> None:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for c in candidates:
        if c.is_file():
            load_dotenv(c, override=False)


HARD_CAP_USD = Decimal("2.00")
PILOT_STAGE = "persona_trait_extraction"
PILOT_MODEL = "claude-sonnet-4-6"  # Sonnet — sufficient + cheap


async def _amain() -> int:
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set after loading .env. Aborting.")
        return 2

    from assembly.db import get_sessionmaker
    from assembly.llm.anthropic import AnthropicProvider
    from assembly.models.llm_log import LLMCallLog
    from assembly.models.persona import (
        PersonaEvidenceLink,
        PersonaRecord,
        PersonaTrait,
        SourceRecord,
    )
    from assembly.models.simulation import Simulation
    from assembly.pipeline.persona_construction import (
        LLMTraitExtractor,
        run_persona_construction,
    )
    from assembly.pipeline.persona_construction.source_classifier import (
        SourceClassification,
        classify_source_record,
    )

    sessionmaker = get_sessionmaker()

    # 1) Load Tavily source_records.
    async with sessionmaker() as session:
        all_rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == "tavily_search_extract"
                )
            )
        ).scalars().all()

    # 2) Filter to strong_persona_signal only.
    strong_rows: list[SourceRecord] = []
    for r in all_rows:
        report = classify_source_record(
            content=r.content,
            source_url=r.source_url,
            metadata=r.metadata_,
            user_handle_hash=r.user_handle_hash,
        )
        if report.classification == SourceClassification.STRONG_PERSONA_SIGNAL:
            strong_rows.append(r)
    print(f"strong_persona_signal source_records: {len(strong_rows)}")

    if not strong_rows:
        print("ERROR: no strong_persona_signal records to construct from.")
        return 1

    # 3) Create an admin Simulation row to anchor the cost guard against.
    sim_id: UUID = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id="phase_8_2f_pilot",
                status="phase_8_2f_pilot_running",
                progress={"stage": "persona_construction_pilot"},
                total_cost_usd=Decimal("0"),
                total_latency_ms=0,
            ))

    # 4) Configure the LLM extractor.
    provider = AnthropicProvider()
    extractor = LLMTraitExtractor(
        sessionmaker=sessionmaker,
        simulation_id=sim_id,
        provider=provider,
        model=PILOT_MODEL,
        max_repair_attempts=1,
    )

    # 5) Run write-mode pilot.
    started = time.monotonic()
    try:
        summary = await run_persona_construction(
            sessionmaker=sessionmaker,
            source_records=strong_rows,
            extractor=extractor,
            write_personas=True,
        )
    except Exception as e:
        # Mark simulation failed so the cost-guard row is closed cleanly.
        async with sessionmaker() as session:
            async with session.begin():
                from sqlalchemy import update
                await session.execute(
                    update(Simulation)
                    .where(Simulation.id == sim_id)
                    .values(status="phase_8_2f_pilot_failed",
                            error={"type": type(e).__name__, "message": str(e)})
                )
        print(f"ERROR: pilot raised {type(e).__name__}: {e!r}")
        return 1
    elapsed_s = time.monotonic() - started

    # 6) Mark simulation completed.
    async with sessionmaker() as session:
        async with session.begin():
            from sqlalchemy import update
            await session.execute(
                update(Simulation)
                .where(Simulation.id == sim_id)
                .values(status="phase_8_2f_pilot_completed",
                        completed_at=datetime.now(UTC))
            )

    # 7) Pull llm_call_log + cost stats for this simulation.
    async with sessionmaker() as session:
        log_rows = (
            await session.execute(
                select(LLMCallLog).where(
                    LLMCallLog.simulation_id == sim_id,
                    LLMCallLog.stage == PILOT_STAGE,
                )
            )
        ).scalars().all()
    total_cost = sum(
        (r.cost_usd or Decimal("0")) for r in log_rows
    )
    avg_latency_ms = (
        sum(int(r.latency_ms or 0) for r in log_rows) / max(len(log_rows), 1)
    )

    # 8) Sample personas + traits + evidence for the report.
    async with sessionmaker() as session:
        persona_evidence_rows = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.source_record_id.in_([r.id for r in strong_rows])
                )
            )
        ).scalars().all()
        persona_ids = sorted({pl.persona_id for pl in persona_evidence_rows})
        sample_persona_ids = persona_ids[:3]
        personas = []
        for pid in sample_persona_ids:
            p = (await session.execute(
                select(PersonaRecord).where(PersonaRecord.id == pid)
            )).scalar_one()
            traits = (await session.execute(
                select(PersonaTrait).where(PersonaTrait.persona_id == pid)
            )).scalars().all()
            links = (await session.execute(
                select(PersonaEvidenceLink).where(PersonaEvidenceLink.persona_id == pid)
            )).scalars().all()
            personas.append((p, list(traits), list(links)))

    # 9) Write report.
    print("=" * 64)
    print("Phase 8.2F — bounded write-mode pilot report")
    print("=" * 64)
    print(f"simulation_id (admin row): {sim_id}")
    print(f"runtime_seconds:           {elapsed_s:.2f}")
    print(f"hard_cap_usd:              ${HARD_CAP_USD}")
    print(f"stage:                     {PILOT_STAGE}")
    print(f"model:                     {PILOT_MODEL}")
    print()
    print("Inputs:")
    print(f"  source_records used:                    {len(strong_rows)}")
    print(f"  candidate persona shells:               {summary.candidate_shells}")
    print()
    print("Outcomes:")
    print(f"  personas_created:                       {summary.personas_created}")
    print(f"  personas_skipped:                       {summary.personas_skipped}")
    print(f"  traits_created:                         {summary.traits_created}")
    print(f"  traits_rejected:                        {summary.traits_rejected}")
    print(f"  evidence_links_created:                 {summary.evidence_links_created}")
    print()
    print("Top skipped reasons:")
    breakdown = summary.reason_breakdown()
    if breakdown:
        for reason, n in breakdown.items():
            print(f"  - {reason}: {n}")
    else:
        print("  <none recorded>")
    print()
    print("Cost / LLM stats:")
    print(f"  llm_call_log rows (stage={PILOT_STAGE}): {len(log_rows)}")
    print(f"  total_cost_usd:                         ${total_cost:.4f}")
    print(f"  avg_latency_ms:                         {avg_latency_ms:.0f}")
    print()
    print("Sample personas (up to 3):")
    if not personas:
        print("  <no personas written>")
    for p, traits, links in personas:
        print(f"  persona_id={p.id} display_name={p.display_name!r}")
        for t in sorted(traits, key=lambda x: x.field_name):
            v = (t.value or "").replace("\n", " ")
            v = v[:80]
            print(f"    - {t.field_name} [{t.support_level}] "
                  f"conf={t.confidence} value={v!r}")
        link_count = len(links)
        print(f"    evidence_links: {link_count}")
        sample_link = links[0] if links else None
        if sample_link is not None:
            ex = (sample_link.excerpt or "").replace("\n", " ")[:80]
            print(f"      sample link → record={sample_link.source_record_id} "
                  f"field={sample_link.contribution_field} excerpt={ex!r}")
    print("=" * 64)
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
