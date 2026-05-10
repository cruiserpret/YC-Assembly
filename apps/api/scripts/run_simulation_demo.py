"""Live end-to-end Phase 6 demo.

Runs basic_brief through:
  intake_parser → evidence_builder (no live retrieval) → society_builder
  → engine (7 rounds, society size 6 for cost)

Prints per-round stance distribution + top objections / persuasion drivers
+ debate shifts + total cost + total latency.

Requires:
  - ANTHROPIC_API_KEY (or OPENAI_API_KEY) in .env
  - Postgres + Redis running (`docker compose up -d`)
  - Alembic migrations applied (`uv run alembic upgrade head`)

Usage:
  cd apps/api
  uv run python scripts/run_simulation_demo.py

Estimated cost: ~$1.00–$2.00 (capped at ASSEMBLY_COST_HARD_USD).
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from uuid import uuid4

from assembly.config import get_settings
from assembly.db import get_sessionmaker
from assembly.llm.errors import CostCapExceeded, LLMProviderError, LLMRepairExhausted
from assembly.models.simulation import Simulation, SimulationInput
from assembly.pipeline.evidence_builder import build_evidence
from assembly.pipeline.intake_parser import parse_brief
from assembly.pipeline.simulation.engine import run_simulation
from assembly.pipeline.society_builder import build_society
from assembly.schemas.brief import (
    CompetitorRef,
    PriceStructure,
    SimulationBriefIn,
    TargetSociety,
)


def _make_brief() -> SimulationBriefIn:
    return SimulationBriefIn(
        product_type="ai_commerce_platform",
        product_name="Amboras",
        description=(
            "Amboras is an AI commerce platform that builds and operates "
            "Shopify stores autonomously for merchants who do not want to "
            "manage plugins or hire agencies. Founders worry the AI will "
            "damage brand identity. Merchants would switch if they saw "
            "proof that they retain final control over branding and pricing."
        ),
        price_structure=PriceStructure(
            model="subscription_monthly",
            amount="$49/mo starter",
            notes="performance tier later",
        ),
        target_society=TargetSociety(
            description=(
                "Shopify merchants doing $10k-$80k/month, frustrated with "
                "plugin bloat and overwhelmed by managing apps."
            ),
            geography="US/Canada",
            known_segments=["mid-volume merchants", "premium brand operators"],
        ),
        competitors=[
            CompetitorRef(name="Shopify Magic", url="https://example.com/magic"),
            CompetitorRef(name="Conversion AI Tool"),
        ],
        product_url=None,
        additional_context="Founders worry about brand control and trust.",
    )


def _print_round_summary(round_result) -> None:
    print(f"\n  Round {round_result.round_number} ({round_result.round_type})")
    print(f"    duration: {(round_result.completed_at - round_result.started_at).total_seconds():.1f}s")
    summary = round_result.summary
    dist = summary.get("stance_distribution", {})
    if dist:
        print(f"    stance distribution: {dist}")
    top_obj = summary.get("top_objections", [])
    if top_obj:
        print(f"    top objection categories:")
        for o in top_obj[:3]:
            print(f"      - {o['category']} ×{o['count']}: {o['example'][:100]}")
    top_drv = summary.get("top_persuasion_drivers", [])
    if top_drv:
        print(f"    top persuasion drivers:")
        for d in top_drv[:3]:
            print(f"      - {d['category']} ×{d['count']}: {d['example'][:100]}")
    shifts = summary.get("debate_shifts", [])
    if shifts:
        print(f"    debate shifts: {len(shifts)}")
        for s in shifts[:3]:
            print(f"      - {s['from_stance']} → {s['to_stance']}: {s['reason'][:80]}")


async def main() -> int:
    settings = get_settings()
    if not (settings.anthropic_api_key or settings.openai_api_key):
        print("FAIL: no LLM provider key configured (.env)")
        return 2

    # Pick provider per settings.
    if settings.llm_primary_provider == "anthropic" and settings.anthropic_api_key:
        from assembly.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider()
    elif settings.openai_api_key:
        from assembly.llm.openai import OpenAIProvider
        provider = OpenAIProvider()
    else:
        from assembly.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider()

    sessionmaker = get_sessionmaker()
    brief = _make_brief()

    # Persist a Simulation row so the engine has something to update.
    sim_id = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            sim = Simulation(id=sim_id, status="pending")
            sim.input = SimulationInput(
                product_type=brief.product_type,
                product_name=brief.product_name,
                description=brief.description,
                price_structure=brief.price_structure.model_dump(),
                target_society=brief.target_society.model_dump(),
                competitors=[c.model_dump() for c in brief.competitors],
                product_url=None,
                additional_context=brief.additional_context,
                raw_brief=brief.model_dump(mode="json"),
            )
            session.add(sim)

    print(f"=== Assembly Phase 6 demo (simulation_id={sim_id}) ===\n")
    started = datetime.now()

    # 1. Intake parser
    print("[1/4] intake parser …")
    pio_result = await parse_brief(
        brief,
        provider=provider,
        simulation_id=sim_id,
        model=settings.llm_synthesis_model,
    )
    print(f"      repair_attempts={pio_result.repair_attempts_used}")

    # 2. Evidence builder
    print("[2/4] evidence builder (no live retrieval) …")
    evidence = await build_evidence(
        brief,
        simulation_id=sim_id,
    )
    print(f"      {len(evidence.items)} evidence items "
          f"(direct={sum(1 for i in evidence.items if i.kind=='direct')}, "
          f"analogical={sum(1 for i in evidence.items if i.kind=='analogical')}, "
          f"missing={sum(1 for i in evidence.items if i.kind=='missing')})")

    # 3. Society builder (size 6 to keep demo cost down)
    print("[3/4] society builder (size=6) …")
    society = await build_society(
        simulation_id=sim_id,
        brief=brief,
        pio=pio_result.product_intelligence,
        evidence=evidence,
        provider=provider,
        model=settings.llm_synthesis_model,
        desired_size=6,
    )
    print(f"      {len(society.agents)} agents, {len(society.edges)} edges, "
          f"{len(society.segments)} segments")

    # 4. Engine
    print("[4/4] simulation engine (7 rounds × 6 agents = ~42 LLM calls) …")
    try:
        result = await run_simulation(
            simulation_id=sim_id,
            brief=brief,
            pio=pio_result.product_intelligence,
            evidence=evidence,
            society=society,
            provider=provider,
            sessionmaker=sessionmaker,
        )
    except CostCapExceeded as e:
        print(f"\n!! cost cap exceeded: {e}")
        return 1
    except LLMRepairExhausted as e:
        print(f"\n!! repair loop exhausted: {e}")
        return 1
    except LLMProviderError as e:
        print(f"\n!! provider error: {e}")
        return 1

    # Per-round summaries
    for r in result.rounds:
        _print_round_summary(r)

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n=== complete ===")
    print(f"  total cost:    ${result.total_cost_usd:.4f}")
    print(f"  total latency: {result.total_latency_ms} ms (sum of LLM calls)")
    print(f"  wall time:     {elapsed:.1f}s")
    print(f"  simulation_id: {sim_id}")
    print(f"\n  Phase 6 produced raw simulation state. Phase 7 (aggregation)")
    print(f"  is the next phase — it will turn this into the 9-section report.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
