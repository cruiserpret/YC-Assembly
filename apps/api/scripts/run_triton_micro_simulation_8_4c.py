"""Phase 8.4C — 21-person expanded Triton Drinks micro-simulation.

Same brief as Phase 8.4B, but uses ALL production-retrieved included
personas (CORE_RELEVANT + ADJACENT_RELEVANT, no EXCLUDED). Routes
through the existing Phase 8.2K + 8.4B.2 micro-simulation harness
with the stricter objection-specificity contract and the new
diversity-aware debate-pair selector (Phase 8.4C runner change).

Caps:
  * max LLM calls (informational, not runtime-enforced): 75
  * cost cap: $3.00
  * max debate turns: 10

Mandatory caveats: emitted by `build_micro_simulation_caveats`
(Phase 8.4B.1) — product-correct, no Amboras leak, includes
not-a-forecast / unlaunched-product / adjacent-tier markers.

NO new ingestion. NO new personas. NO trait/evidence-link writes.
NO graph / society / UI writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


# Caps
TARGET_BRIEF_TAG = "triton_drinks"
APPROVER_LABEL = "phase_8_4c_triton_micro_simulation"
COST_CAP_USD = Decimal("3.00")
MAX_DEBATE_TURNS = 10
PILOT_MODEL = "claude-sonnet-4-6"


def _triton_brief():
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput
    return ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can. Targeted "
            "at college students, athletes, gym-goers, and busy young "
            "adults. Competes with Red Bull and Monster on the energy "
            "drink shelf, and overlaps with Celsius, Prime, Gatorade, "
            "pre-workout drinks, cold brew, and electrolyte drinks. "
            "Triton is unlaunched; relevance is market-entry-anchored, "
            "not direct-product."
        ),
        price_or_price_structure="$3.99 per can (single-serve)",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society=(
            "California consumers in the energy / sports / functional-"
            "beverage occasion."
        ),
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, "
            "pre-workout powders, electrolyte drinks. Triton is unlaunched."
        ),
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )


async def _retrieve_full_audience():
    from sqlalchemy import select
    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink, SourceRecord,
    )
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.audience_retrieval.inclusion_tier import (
        InclusionTier,
    )
    from assembly.pipeline.run_scoped_topup.executor import (
        _load_audience_inputs,
    )
    from assembly.pipeline.target_society import build_target_society_plan

    sm = get_sessionmaker()
    brief = _triton_brief()
    plan = build_target_society_plan(brief)

    async with sm() as session:
        triton_sr = (await session.execute(
            select(SourceRecord)
            .where(
                SourceRecord.metadata_["target_brief"].astext
                == TARGET_BRIEF_TAG
            )
        )).scalars().all()
    triton_sr_ids = {r.id for r in triton_sr}
    async with sm() as session:
        triton_links = (await session.execute(
            select(PersonaEvidenceLink)
            .where(PersonaEvidenceLink.source_record_id.in_(triton_sr_ids))
        )).scalars().all() if triton_sr_ids else []
    triton_pids = {el.persona_id for el in triton_links}

    audience_inputs, domain_map = await _load_audience_inputs(sm)
    triton_only = [
        ai for ai in audience_inputs if ai.persona_id in triton_pids
    ]
    audience_result = retrieve_personas_for_target_society(
        brief=brief, plan=plan, personas=triton_only,
        domain_by_record_id=domain_map,
    )

    core: list = []
    adj: list = []
    for m in audience_result.matched_personas:
        if m.final_tier == InclusionTier.CORE_RELEVANT.value:
            core.append(m)
        elif m.final_tier == InclusionTier.ADJACENT_RELEVANT.value:
            adj.append(m)
    core.sort(key=lambda m: -m.relevance_score)
    adj.sort(key=lambda m: -m.relevance_score)
    return audience_result, core, adj, plan, brief, sm


async def _live_run() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Aborting live run.")
        return 2

    from assembly.llm.anthropic import AnthropicProvider
    from assembly.pipeline.audience_retrieval.schemas import PersonaMatch
    from assembly.pipeline.micro_simulation import (
        MicroSimulationRefused, run_micro_simulation,
        map_micro_stance_to_market_entry,
    )

    print("=" * 72)
    print("Phase 8.4C — Triton 21-Person Expanded Micro-Simulation LIVE RUN")
    print("=" * 72)
    audience_result, core, adj, plan, brief, sm = await _retrieve_full_audience()
    n_total = len(core) + len(adj)
    if n_total == 0:
        print("ERROR: production retrieval returned 0 included personas.")
        return 2
    if any(m.final_tier == "excluded" for m in audience_result.matched_personas):
        print("ERROR: matched_personas contains excluded tier; aborting.")
        return 2
    print(
        f"audience: {len(core)} CORE + {len(adj)} ADJACENT = "
        f"{n_total} total included. EXCLUDED count: "
        f"{len(audience_result.excluded_personas)}"
    )
    for m in core:
        print(
            f"  CORE     {m.display_name:18s} score={m.relevance_score} "
            f"cat={m.matched_category_key} anchors={list(m.anchor_types)}"
        )
    for m in adj:
        print(
            f"  ADJACENT {m.display_name:18s} score={m.relevance_score} "
            f"cat={m.matched_category_key} anchors={list(m.anchor_types)}"
        )

    # Trim audience_result to ONLY the included personas (already
    # the case via retriever, but be explicit).
    trimmed_matched: list[PersonaMatch] = list(core) + list(adj)
    trimmed_audience = audience_result.model_copy(update={
        "matched_personas": trimmed_matched,
    })

    started = time.monotonic()
    provider = AnthropicProvider()
    try:
        result = await run_micro_simulation(
            sessionmaker=sm,
            brief=brief,
            audience_result=trimmed_audience,
            brief_label="triton_drinks_8_4c",
            provider=provider,
            dry_run=False,
            include_weakly_relevant=True,
            enable_debate=True,
            max_relevant_personas=len(core),
            max_weakly_relevant_personas=len(adj),
            max_debate_turns=MAX_DEBATE_TURNS,
            cost_cap_usd=COST_CAP_USD,
            model=PILOT_MODEL,
        )
    except MicroSimulationRefused as e:
        print(f"ERROR: runner refused: {e}")
        return 1
    elapsed = time.monotonic() - started

    final_stances: list[dict] = []
    for s in result.persona_states_final:
        final_stances.append({
            "persona_id": s.persona_id,
            "display_name": s.display_name,
            "internal_stance": s.current_stance.value,
            "market_entry_stance": (
                map_micro_stance_to_market_entry(s.current_stance).value
            ),
        })

    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = json.loads(result.model_dump_json())
    summary["phase"] = "8_4c_triton_micro_simulation_live"
    summary["final_stances_market_entry"] = final_stances
    summary["runtime_s"] = round(elapsed, 1)
    summary["n_core"] = len(core)
    summary["n_adjacent"] = len(adj)
    summary["audience_total"] = n_total
    out_path = out_dir / "triton_micro_simulation_live_8_4c.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print(result.summary_text)
    print()
    print("=" * 72)
    print("Phase 8.4C — TRITON 21-PERSON MICRO-SIMULATION RESULT")
    print("=" * 72)
    print(f"runtime: {elapsed:.1f}s")
    print(f"llm_call_count: {result.llm_call_count}")
    print(f"cost_actual_usd: ${result.cost_actual_usd:.4f}")
    print(f"cost_cap_usd:    ${result.cost_cap_usd}")
    print(
        f"audit.forbidden_claims_found: "
        f"{result.output_audit.forbidden_claims_found}"
    )
    print(
        f"audit.rounds_failing_audit: "
        f"{result.output_audit.rounds_failing_audit}"
    )
    print(f"debate_turns: {len(result.trace.debate_turns)}")
    print()
    print("Final stances (market-entry mapping):")
    fs_counts: Counter = Counter(
        f["market_entry_stance"] for f in final_stances
    )
    for v, c in fs_counts.most_common():
        print(f"  {v}: {c}")
    print(f"\n→ live audit JSON: {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 8.4C — Triton 21-person expanded micro-simulation."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Run the live LLM micro-simulation. Requires explicit "
             "operator approval. Default is to abort.",
    )
    args = parser.parse_args()
    _load_env()
    if not args.live:
        print(
            "Phase 8.4C requires --live and operator approval. "
            "No dry-run mode for this script."
        )
        return 0
    return asyncio.run(_live_run())


if __name__ == "__main__":
    sys.exit(main())
