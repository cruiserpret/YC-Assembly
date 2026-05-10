"""Phase 6.5 — Worker-path full-pipeline demo.

Runs the full backend pipeline through the orchestrator (the same code
path the arq worker uses). Persists rows to Postgres at every stage.

Two modes:
  --inline:   call run_full_pipeline directly (no arq worker required)
  --queue:    POST to the API which enqueues; poll until done
              (requires the API + the arq worker to be running)

Usage:
  cd apps/api
  uv run python scripts/run_pipeline_demo.py [--inline | --queue]

Estimated cost: ~$1.50–$3.00 against real Anthropic at society size 6.
The cost cap (ASSEMBLY_COST_HARD_USD) defaults to 5.00 for full-pipeline
runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from assembly.config import get_settings
from assembly.db import get_sessionmaker
from assembly.models.simulation import Simulation, SimulationInput
from assembly.pipeline.orchestration import run_full_pipeline
from assembly.schemas.brief import (
    CompetitorRef,
    PriceStructure,
    SimulationBriefIn,
    TargetSociety,
)
from assembly.workers import build_provider


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


async def _persist_brief() -> UUID:
    sessionmaker = get_sessionmaker()
    brief = _make_brief()
    sim_id = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            sim = Simulation(id=sim_id, status="pending", progress={"stage": "pending"})
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
    return sim_id


async def _print_status(sim_id: UUID) -> dict:
    """Read the live status row + progress directly from the DB."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        sim = await session.get(Simulation, sim_id)
        if sim is None:
            return {"status": "missing"}
        return {
            "id": str(sim.id),
            "status": sim.status,
            "failed_stage": sim.failed_stage,
            "progress": sim.progress,
            "error": sim.error,
            "completed_at": sim.completed_at.isoformat() if sim.completed_at else None,
        }


async def run_inline() -> int:
    print("=== Phase 6.5 demo — INLINE mode (no arq worker required) ===", flush=True)
    sim_id = await _persist_brief()
    print(f"\nsimulation_id={sim_id}", flush=True)
    print("watch progress with:", flush=True)
    print(
        f'  docker compose exec -T postgres psql -U assembly -d assembly -c '
        f'"SELECT status, progress FROM simulations WHERE id=\'{sim_id}\';"',
        flush=True,
    )

    sessionmaker = get_sessionmaker()
    provider = build_provider()
    print(f"\nprovider={provider.name}", flush=True)

    # Stream progress from the DB while the pipeline runs in another task.
    pipeline_task = asyncio.create_task(
        run_full_pipeline(
            simulation_id=sim_id,
            sessionmaker=sessionmaker,
            provider=provider,
        )
    )
    started = datetime.now(UTC)
    last_signature: tuple | None = None
    while not pipeline_task.done():
        await asyncio.sleep(5)
        try:
            snapshot = await _print_status(sim_id)
            sig = (
                snapshot.get("status"),
                (snapshot.get("progress") or {}).get("stage"),
                (snapshot.get("progress") or {}).get("round_index"),
                (snapshot.get("progress") or {}).get("agents_completed"),
            )
            if sig != last_signature:
                stage = sig[1] or sig[0]
                round_idx = sig[2]
                agents_done = sig[3]
                agents_total = (snapshot.get("progress") or {}).get("agents_total")
                line = f"  [{datetime.now(UTC).strftime('%H:%M:%S')}] {stage}"
                if round_idx is not None:
                    line += f" round={round_idx}/7"
                if agents_done is not None and agents_total is not None:
                    line += f" agents={agents_done}/{agents_total}"
                print(line, flush=True)
                last_signature = sig
        except Exception as e:
            print(f"  (status poll error: {e})", flush=True)

    # Re-raise pipeline exception in the foreground so traceback is visible.
    result = await pipeline_task
    elapsed = (datetime.now(UTC) - started).total_seconds()

    print(f"\n=== complete ===")
    print(f"  final_status:    {result.final_status}")
    print(f"  rounds_completed:{result.rounds_completed}")
    print(f"  total_cost:      ${result.total_cost_usd:.4f}")
    print(f"  wall_time:       {elapsed:.1f}s")
    print(f"  simulation_id:   {sim_id}")
    print(f"\n  Phase 7 aggregation is gated by ASSEMBLY_ENABLE_AGGREGATION=false.")
    print(f"  Raw state visible via /simulations/{{id}}/raw-state when")
    print(f"  ASSEMBLY_EXPOSE_RAW_STATE=true.\n")
    return 0


async def run_queue(api_base: str) -> int:
    """Hit the API, enqueue via arq, poll until done.

    Requires:
      - API server running:    `uv run uvicorn assembly.main:app --port 8000`
      - arq worker running:    `uv run arq assembly.workers.WorkerSettings`
      - Redis running
    """
    print(f"=== Phase 6.5 demo — QUEUE mode (api={api_base}) ===\n")
    brief = _make_brief()
    payload = brief.model_dump(mode="json")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{api_base}/simulations", json=payload)
        if r.status_code != 202:
            print(f"FAIL: POST returned {r.status_code} — {r.text}")
            return 1
        body = r.json()
        sim_id = body["id"]
        print(f"enqueued simulation_id={sim_id} status={body['status']}\n")

        last_stage = None
        # A full Phase 6 simulation at society size 6 takes ~25-30 min wall-clock
        # (society_builder ~9 min + 7 rounds × 6 agents × ~13s/call serialized by
        # the cost-guard row lock). Poll up to 60 min to leave headroom for slow
        # repair attempts.
        for _ in range(3600):  # poll up to 60 min at 1s interval
            r = await client.get(f"{api_base}/simulations/{sim_id}/status")
            data = r.json()
            stage = (data.get("progress") or {}).get("stage", data.get("status"))
            if stage != last_stage:
                round_idx = (data.get("progress") or {}).get("round_index")
                round_type = (data.get("progress") or {}).get("current_round")
                extra = f" ({round_type} {round_idx}/7)" if round_type else ""
                print(f"  [{datetime.now(UTC).strftime('%H:%M:%S')}] {stage}{extra}")
                last_stage = stage
            status_v = data.get("status")
            if status_v in ("simulation_completed", "reported", "failed"):
                print(f"\n  final status: {status_v}")
                if status_v == "failed":
                    print(f"  failed_stage: {data.get('failed_stage')}")
                    print(f"  error:        {data.get('error')}")
                    return 1
                return 0
            await asyncio.sleep(1.0)

        print("\nFAIL: timed out polling")
        return 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("inline", "queue"),
        default="inline",
        help="inline = call run_full_pipeline directly; queue = POST through API",
    )
    parser.add_argument(
        "--api-base", default="http://localhost:8000",
        help="API base URL for queue mode",
    )
    args = parser.parse_args()

    if args.mode == "inline":
        return await run_inline()
    return await run_queue(args.api_base)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
