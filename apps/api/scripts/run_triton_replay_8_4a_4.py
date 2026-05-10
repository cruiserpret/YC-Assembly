"""Phase 8.4A.4 — Triton replay through the production-wired
audience-retrieval pipeline.

READ-ONLY. No DB writes (except audit JSON). No LLM calls. No
ingestion. No persona / trait / evidence-link / graph / simulation
/ UI writes.

This script differs from `run_triton_replay_8_4a_3.py`: it calls the
canonical `retrieve_personas_for_target_society` directly. The gate
is applied AUTOMATICALLY inside the pipeline (no manual gate
invocation). PersonaMatch / PersonaExclusion rows now carry the gate
output fields (final_tier, anchor_*, gate_reason, etc.) populated by
the production code path.

Asserts replay parity:
  Phase 8.4A.3 (manual replay): 7 CORE + 14 ADJACENT + 23 EXCLUDED
  Phase 8.4A.4 (production):    7 CORE + 14 ADJACENT + 23 EXCLUDED
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


TARGET_BRIEF_TAG = "triton_drinks"


async def _amain() -> int:
    _load_env()
    from sqlalchemy import select, func
    from assembly.db import get_sessionmaker
    from assembly.models import (
        Agent, AgentEdge, AgentResponse, DebateTurn,
        PersonaCluster, PersonaClusterMembership,
        PersonaEvidenceLink, PersonaGraphEdge, PersonaOpinion,
        PersonaRecord, PersonaTrait,
        SimulationOutput, SimulationRound, SourceRecord,
    )
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.run_scoped_topup.executor import (
        _load_audience_inputs,
    )
    from assembly.pipeline.target_society import build_target_society_plan
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput

    sm = get_sessionmaker()
    print("=" * 72)
    print("Phase 8.4A.4 — Triton production-wired retrieval (READ-ONLY)")
    print("=" * 72)

    # ---- Build Triton brief
    triton_brief = ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can. Targeted "
            "at college students, athletes, gym-goers, and busy young "
            "adults. Competes with Red Bull and Monster on the energy "
            "drink shelf, and overlaps with Celsius, Prime, Gatorade, "
            "pre-workout drinks, cold brew, and electrolyte drinks. "
            "Triton is unlaunched."
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
    plan = build_target_society_plan(triton_brief)
    print(
        f"plan: {len(plan.stakeholder_categories)} categories, "
        f"market-entry weights"
    )

    # ---- Snapshot forbidden tables BEFORE
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
        before["SourceRecord"] = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        before["PersonaRecord"] = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        before["PersonaTrait"] = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        before["PersonaEvidenceLink"] = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()

    # ---- Load Triton-tagged source_records + filter to Triton-only
    # personas
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
    print(f"Triton-tagged source_records: {len(triton_sr)}")
    print(f"Triton-only personas:         {len(triton_only)}")

    # ---- Production-wired retrieval
    started = time.monotonic()
    result = retrieve_personas_for_target_society(
        brief=triton_brief, plan=plan,
        personas=triton_only, domain_by_record_id=domain_map,
    )
    elapsed = time.monotonic() - started

    # ---- Aggregate
    matched = result.matched_personas
    excluded = result.excluded_personas
    final_tier_counts: Counter = Counter(
        m.final_tier or "<none>" for m in matched
    )
    excl_reason_counts: Counter = Counter(
        ex.gate_reason or "<no_gate>" for ex in excluded
    )

    # ---- Snapshot AFTER
    async with sm() as session:
        after = {
            m.__name__: (await session.execute(
                select(func.count()).select_from(m)
            )).scalar_one()
            for m in forbidden_models
        }
        after["SourceRecord"] = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        after["PersonaRecord"] = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        after["PersonaTrait"] = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        after["PersonaEvidenceLink"] = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    deltas = {
        k: (before[k], after[k]) for k in before if before[k] != after[k]
    }

    # ---- Build report
    matched_rows = []
    for m in matched:
        excerpt = ""
        if m.anchor_evidence_excerpts:
            excerpt = m.anchor_evidence_excerpts[0][:240]
        matched_rows.append({
            "persona_id": m.persona_id,
            "display_name": m.display_name,
            "score": m.relevance_score,
            "classification": m.classification.value,
            "base_tier": m.base_tier,
            "final_tier": m.final_tier,
            "gate_reason": m.gate_reason,
            "anchor_has": m.anchor_has,
            "anchor_types": list(m.anchor_types),
            "matched_anchor_terms": list(m.matched_anchor_terms)[:8],
            "anchor_evidence_excerpt": excerpt,
            "category": m.matched_category_key,
            "category_display": m.matched_category_display_name,
            "caveats": list(m.caveats),
            "why_included": m.why_included[:300],
        })
    excluded_rows = []
    for ex in excluded:
        excluded_rows.append({
            "persona_id": ex.persona_id,
            "display_name": ex.display_name,
            "score": ex.score,
            "best_possible_category": ex.best_possible_category,
            "base_tier": ex.base_tier,
            "final_tier": ex.final_tier,
            "gate_reason": ex.gate_reason,
            "anchor_has": ex.anchor_has,
            "anchor_types": list(ex.anchor_types),
            "exclusion_reason": ex.exclusion_reason[:300],
        })
    matched_rows.sort(key=lambda r: -r["score"])
    excluded_rows.sort(key=lambda r: -r["score"])

    # ---- Operator-facing print
    print()
    print("=" * 72)
    print("PHASE 8.4A.4 — PRODUCTION-WIRED REPLAY SUMMARY")
    print("=" * 72)
    print(f"runtime: {elapsed:.2f}s")
    print()
    print("Final tier distribution (matched_personas):")
    for tier in ("core_relevant", "adjacent_relevant", "<none>"):
        print(f"  {tier}: {final_tier_counts.get(tier, 0)}")
    print(f"\nexcluded count: {len(excluded)}")
    print("Excluded gate-reason distribution:")
    for r, n in excl_reason_counts.most_common():
        print(f"  {r}: {n}")
    print()
    print(f"DB row deltas: {deltas}")
    print(f"forbidden-table deltas (must be SR-only): "
          f"{ {k: v for k, v in deltas.items() if k != 'SourceRecord'} }")

    # ---- Save audit
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_4a_4_market_entry_gate_production_wire",
        "completed_at": datetime.now(UTC).isoformat(),
        "personas_evaluated": len(triton_only),
        "tier_distribution_final": dict(final_tier_counts),
        "excluded_count": len(excluded),
        "excluded_gate_reason_distribution": dict(excl_reason_counts),
        "row_deltas": {k: v for k, v in deltas.items()},
        "matched_personas": matched_rows,
        "excluded_personas": excluded_rows,
        "next_step_recommendation": result.next_step_recommendation.value,
        "warnings_and_caveats": list(result.warnings_and_caveats),
        "category_coverage": [
            {
                "category_key": c.category_key,
                "matched_total": c.matched_total,
                "label": c.coverage_label.value,
            }
            for c in result.category_coverage
        ],
    }
    out_path = out_dir / "triton_replay_8_4a_4.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
