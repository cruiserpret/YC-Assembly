"""Phase 8.2F.6 — extractor prompt recheck on the existing 22 strong
source_records.

Re-runs the LLMTraitExtractor against the same 22 strong-signal
source_records the Phase 8.2F pilot used. NO persona writes. NO new
ingestion. NO new live API except the Anthropic LLM extraction call,
which already has its own cost-guard discipline.

Reports per-field extraction quality (direct/inferred vs unknown) so
we can compare against the pilot's 21-persona outcome and confirm
the prompt fix actually moves coverage on the four under-extracted
fields:
    role_or_context, objection_patterns, trust_triggers,
    current_alternatives.

The recheck creates an admin Simulation row to anchor the cost guard
against. NO persona / trait / evidence-link / opinion / graph row is
written. The worker runs in `write_personas=False` (dry-run) but with
a real LLM extractor.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
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
PILOT_MODEL = "claude-sonnet-4-6"


async def _amain() -> int:
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set after loading .env. Aborting.")
        return 2

    from assembly.db import get_sessionmaker
    from assembly.llm.anthropic import AnthropicProvider
    from assembly.models.llm_log import LLMCallLog
    from assembly.models.persona import SourceRecord
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

    # Load the same Tavily-operator records the pilot used.
    async with sessionmaker() as session:
        all_rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == "tavily_search_extract"
                )
            )
        ).scalars().all()

    # Filter to strong_persona_signal only — same shells as the pilot.
    strong_rows: list[SourceRecord] = []
    for r in all_rows:
        if classify_source_record(
            content=r.content,
            source_url=r.source_url,
            metadata=r.metadata_,
            user_handle_hash=r.user_handle_hash,
        ).classification == SourceClassification.STRONG_PERSONA_SIGNAL:
            strong_rows.append(r)
    print(f"Strong-signal source records to re-extract: {len(strong_rows)}")
    if not strong_rows:
        print("No strong-signal source_records found. Aborting.")
        return 1

    # Admin simulation row for the cost-guard.
    sim_id: UUID = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id="phase_8_2f_6_recheck",
                status="phase_8_2f_6_recheck_running",
                progress={"stage": "extractor_prompt_recheck"},
                total_cost_usd=Decimal("0"),
                total_latency_ms=0,
            ))

    # Subclass extractor to capture per-shell outputs (we want per-field
    # supported-vs-unknown counts).
    captured_results: list[tuple[str, list]] = []

    class _Capturing(LLMTraitExtractor):
        async def extract(self, shell):
            out = await super().extract(shell)
            captured_results.append((shell.shell_id, list(out.candidates)))
            return out

    cap_extractor = _Capturing(
        sessionmaker=sessionmaker,
        simulation_id=sim_id,
        provider=AnthropicProvider(),
        model=PILOT_MODEL,
        max_repair_attempts=1,
    )

    started = time.monotonic()
    try:
        summary = await run_persona_construction(
            sessionmaker=sessionmaker,
            source_records=strong_rows,
            extractor=cap_extractor,
            write_personas=False,  # ← DRY-RUN; no persona writes
        )
    except Exception as e:
        async with sessionmaker() as session:
            async with session.begin():
                from sqlalchemy import update
                await session.execute(
                    update(Simulation)
                    .where(Simulation.id == sim_id)
                    .values(status="phase_8_2f_6_recheck_failed",
                            error={"type": type(e).__name__, "message": str(e)})
                )
        print(f"ERROR: recheck raised {type(e).__name__}: {e!r}")
        return 1
    elapsed_s = time.monotonic() - started

    # Mark simulation completed.
    async with sessionmaker() as session:
        async with session.begin():
            from sqlalchemy import update
            await session.execute(
                update(Simulation)
                .where(Simulation.id == sim_id)
                .values(status="phase_8_2f_6_recheck_completed",
                        completed_at=datetime.now(UTC))
            )

    # Pull llm_call_log + cost stats for this simulation.
    async with sessionmaker() as session:
        log_rows = (
            await session.execute(
                select(LLMCallLog).where(
                    LLMCallLog.simulation_id == sim_id,
                    LLMCallLog.stage == PILOT_STAGE,
                )
            )
        ).scalars().all()
    total_cost = sum((r.cost_usd or Decimal("0")) for r in log_rows)

    # Per-field extraction quality.
    per_field_supported: Counter[str] = Counter()
    per_field_unknown: Counter[str] = Counter()
    per_shell_supported_count: list[int] = []
    for _shell_id, candidates in captured_results:
        n_sup = 0
        for c in candidates:
            if c.support_level in ("direct", "inferred") and c.value:
                per_field_supported[c.field_name] += 1
                n_sup += 1
            else:
                per_field_unknown[c.field_name] += 1
        per_shell_supported_count.append(n_sup)

    target_fields = (
        "role_or_context", "objection_patterns",
        "trust_triggers", "current_alternatives",
        "price_sensitivity", "buying_constraints",
        "interests", "communication_style",
        "influence_signals", "geography_broad",
    )

    # Pilot reference numbers (from Phase 8.2F write-mode pilot report,
    # which used the prior prompt). Source: that run wrote 101 evidence
    # links across 21 personas (one per supported direct/inferred trait
    # — the persistence layer creates one link per source_id, which for
    # the pilot was effectively one link per supported trait since
    # excerpts only matched their originating record).
    pilot_reference = {
        "role_or_context": 14,
        "objection_patterns": 5,
        "current_alternatives": 17,
        "price_sensitivity": 18,
        "trust_triggers": 6,
        "buying_constraints": 14,
        "interests": 21,
        "communication_style": 4,
        "influence_signals": 0,
        "geography_broad": 0,
    }
    # Per the pilot stats: 21 personas, 4 under-extracted fields. We
    # care most about the delta on those four.

    # Report
    print("=" * 64)
    print("Phase 8.2F.6 — Extractor prompt RECHECK report")
    print("=" * 64)
    print(f"simulation_id (admin row):     {sim_id}")
    print(f"runtime_seconds:               {elapsed_s:.2f}")
    print(f"hard_cap_usd:                  ${HARD_CAP_USD}")
    print(f"shells re-extracted:           {len(strong_rows)}")
    print(f"shells with would-have-3+ valid traits: {summary.shells_with_three_or_more_valid_traits}")
    print(f"shells skipped:                {summary.personas_skipped}")
    print()
    print("LLM cost / call stats:")
    print(f"  llm_call_log rows: {len(log_rows)}")
    print(f"  repair-attempt ratio (calls / shells): "
          f"{(len(log_rows) / max(len(strong_rows), 1)):.2f}")
    print(f"  total_cost_usd:    ${total_cost:.4f}")
    print()
    print("Per-field extraction quality (this run vs Phase 8.2F pilot):")
    print(f"  {'field':<24}  {'this_run_supp':>14}  {'pilot_supp':>10}  delta")
    for f in target_fields:
        sup = per_field_supported[f]
        pilot = pilot_reference.get(f, 0)
        delta = sup - pilot
        sign = "+" if delta > 0 else ""
        print(f"  {f:<24}  {sup:>14}  {pilot:>10}  {sign}{delta}")
    print()
    avg_supp = (
        sum(per_shell_supported_count) / max(len(per_shell_supported_count), 1)
    )
    pilot_avg = (101 / 21)  # pilot avg supported traits per persona
    print(f"avg supported traits per shell:   {avg_supp:.2f} "
          f"(pilot avg per persona: {pilot_avg:.2f})")
    print()
    print("Confirmation:")
    print("  - dry-run wrote NO persona_records / persona_traits / "
          "persona_evidence_links")
    print("  - no Tavily live calls were issued")
    print("  - extraction LLM calls were cost-guarded; admin sim_id closed")
    print("=" * 64)
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
