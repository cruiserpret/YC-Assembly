"""Phase 8.2K — operator-only live MICRO-TEST run on Amboras.

NOT a market simulation. NOT representative of the Amboras target
market. NOT user-facing. Only a mechanical proof that source-grounded
personas can react / object / debate / update stance via the harness
without hallucinating or making forbidden population claims.

Pre-flight:
  * ANTHROPIC_API_KEY loaded from .env (NEVER printed)
  * audience-retrieval result must contain Blair K. + Ellis L. as
    RELEVANT / HIGHLY_RELEVANT (Phase 8.2J calibration outcome)
  * weakly_relevant personas excluded by default
  * dry_run=False, enable_debate=True, cost cap $1.00, model
    claude-sonnet-4-6

Output:
  * full MicroSimulationResult JSON saved under apps/api/_audit/
  * before/after row-count snapshots of 10 forbidden tables saved
    alongside, so the operator can verify zero pollution
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv


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


async def _amain() -> int:
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set after .env load. Aborting.")
        return 2

    from sqlalchemy import func, select

    from assembly.db import get_sessionmaker
    from assembly.llm.anthropic import AnthropicProvider
    from assembly.models import (
        Agent,
        AgentEdge,
        AgentResponse,
        DebateTurn,
        PersonaCluster,
        PersonaClusterMembership,
        PersonaGraphEdge,
        PersonaOpinion,
        SimulationOutput,
        SimulationRound,
    )
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.micro_simulation import (
        MicroSimulationRefused,
        run_micro_simulation,
    )
    from assembly.pipeline.persona_relevance.rubric import (
        RelevanceClassification,
    )
    from assembly.pipeline.run_scoped_topup.executor import (
        _load_audience_inputs,
    )
    from assembly.pipeline.target_society import (
        AMBORAS_BRIEF,
        build_target_society_plan,
    )

    sm = get_sessionmaker()

    # ---- Pre-flight: load audience + run retrieval ---------------------
    print("=" * 64)
    print("Phase 8.2K — LIVE MICRO-TEST (operator-only)")
    print("=" * 64)
    print("brief: AMBORAS_BRIEF")
    print(
        "policy: dry_run=False, debate=True, cost cap=$1.00, "
        "model=claude-sonnet-4-6"
    )
    print(
        "ANTHROPIC_API_KEY: detected in environment "
        "(value not printed)"
    )

    plan = build_target_society_plan(AMBORAS_BRIEF)
    audience_inputs, domain_map = await _load_audience_inputs(sm)
    audience = retrieve_personas_for_target_society(
        brief=AMBORAS_BRIEF,
        plan=plan,
        personas=audience_inputs,
        domain_by_record_id=domain_map,
    )
    relevant_or_better = [
        m for m in audience.matched_personas
        if m.classification in (
            RelevanceClassification.RELEVANT,
            RelevanceClassification.HIGHLY_RELEVANT,
        )
    ]
    print(
        f"\naudience retrieval: {len(audience.matched_personas)} matched, "
        f"{len(relevant_or_better)} RELEVANT-or-better."
    )
    if not relevant_or_better:
        print("ERROR: no relevant personas; refusing live micro-run.")
        return 1
    print("RELEVANT-or-better personas (id / display_name / score / class):")
    for m in relevant_or_better:
        print(
            f"  - {m.persona_id} | {m.display_name} | "
            f"score={m.relevance_score} | {m.classification.value}"
        )

    # ---- Snapshot forbidden-table row counts BEFORE -------------------
    forbidden_models = [
        SimulationOutput, SimulationRound,
        PersonaGraphEdge, PersonaCluster, PersonaClusterMembership,
        PersonaOpinion,
        Agent, AgentResponse, DebateTurn, AgentEdge,
    ]
    async with sm() as session:
        before = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
    print("\nforbidden-table row counts BEFORE:")
    for k, v in before.items():
        print(f"  - {k}: {v}")

    # ---- Live micro-run ------------------------------------------------
    provider = AnthropicProvider()
    started = time.monotonic()
    try:
        result = await run_micro_simulation(
            sessionmaker=sm,
            brief=AMBORAS_BRIEF,
            audience_result=audience,
            brief_label="amboras",
            provider=provider,
            dry_run=False,
            include_weakly_relevant=False,
            enable_debate=True,
            cost_cap_usd=Decimal("1.00"),
            model="claude-sonnet-4-6",
        )
    except MicroSimulationRefused as e:
        print(f"ERROR: runner refused live micro-run: {e}")
        return 1
    elapsed = time.monotonic() - started

    # ---- Snapshot forbidden-table row counts AFTER --------------------
    async with sm() as session:
        after = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
    deltas = {k: (before[k], after[k]) for k in before if before[k] != after[k]}

    # ---- Save full result + snapshots ---------------------------------
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / "micro_simulation_live_amboras.json"
    snap_path = out_dir / "micro_simulation_live_amboras_table_snapshots.json"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    snap_path.write_text(
        json.dumps({"before": before, "after": after, "deltas": deltas},
                   indent=2),
        encoding="utf-8",
    )

    # ---- Print operator-facing summary --------------------------------
    print("\n" + result.summary_text)
    print()
    print("=" * 64)
    print("Phase 8.2K — LIVE MICRO-TEST AUDIT")
    print("=" * 64)
    print(f"runtime: {elapsed:.1f}s")
    print(f"llm_call_count: {result.llm_call_count}")
    print(f"cost_actual_usd: ${result.cost_actual_usd:.4f}")
    print(f"cost_cap_usd: ${result.cost_cap_usd:.2f}")
    print(
        f"audit.forbidden_claims_found: "
        f"{result.output_audit.forbidden_claims_found}"
    )
    print(
        f"audit.rounds_failing_audit: "
        f"{result.output_audit.rounds_failing_audit}"
    )
    print(
        f"audit.sample_size_caveat_present="
        f"{result.output_audit.sample_size_caveat_present}, "
        f"coverage_thinness_caveat_present="
        f"{result.output_audit.coverage_thinness_caveat_present}, "
        f"micro_test_label_present="
        f"{result.output_audit.micro_test_label_present}"
    )
    print("caveats:")
    for c in result.caveats:
        print(f"  - {c}")
    print(f"\nforbidden-table deltas (must be empty): {deltas}")
    print(f"\n→ Full result JSON: {json_path}")
    print(f"→ Table snapshots:  {snap_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
