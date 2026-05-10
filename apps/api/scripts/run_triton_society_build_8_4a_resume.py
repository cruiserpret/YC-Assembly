"""Phase 8.4A — resume from stage 3 (persona construction + audit).

Stages 1-2 of the main run already inserted 100 Tavily + 1 Firecrawl
Triton-tagged source_records. This continuation script:
  Stage 3: persona construction (live LLMTraitExtractor)
  Stage 4: relevance audit against the Triton plan
  Stage 5: save full audit JSON

NEVER re-runs Tavily or Firecrawl. NEVER touches existing rows
outside the persona-construction surface. Uses the same cost-guard
discipline as Phase 8.2K + 8.3B.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


MAX_PERSONAS = 50
TARGET_BRIEF_TAG = "triton_drinks"
APPROVER_LABEL = "phase_8_4a_triton_society_build_resume"


async def _amain() -> int:
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Aborting.")
        return 2

    from sqlalchemy import func, select
    from assembly.db import get_sessionmaker
    from assembly.llm.anthropic import AnthropicProvider
    from assembly.models import (
        Agent, AgentEdge, AgentResponse, DebateTurn,
        PersonaCluster, PersonaClusterMembership,
        PersonaEvidenceLink, PersonaGraphEdge, PersonaOpinion,
        PersonaRecord, PersonaTrait,
        SimulationOutput, SimulationRound, SourceRecord,
    )
    from assembly.models.simulation import Simulation
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.persona_construction import (
        LLMTraitExtractor, run_persona_construction,
    )
    from assembly.pipeline.persona_relevance.rubric import (
        RelevanceClassification,
    )
    from assembly.pipeline.target_society import build_target_society_plan
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput

    sm = get_sessionmaker()
    print("=" * 70)
    print("Phase 8.4A — Triton society build (RESUME from Stage 3)")
    print("=" * 70)

    # ---- Same Triton brief + plan as the main run --------------------
    triton_brief = ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is a new caffeinated sports/energy drink launching "
            "in California at $3.99 per can. Targeted at college students, "
            "athletes, gym-goers, and busy young adults who use energy drinks "
            "or caffeine for studying, workouts, alertness, or performance. "
            "Competes with Red Bull and Monster on the energy drink shelf, and "
            "overlaps with Celsius, Prime, Gatorade, pre-workout drinks, cold "
            "brew, and electrolyte drinks for share-of-occasion. Triton is "
            "unlaunched; relevance means evidence-backed buyers / rejectors / "
            "influencers in the category, not Triton-specific buyers."
        ),
        price_or_price_structure="$3.99 per can (single-serve)",
        competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
        target_market_or_society=(
            "California consumers in the energy / sports / functional-beverage "
            "occasion: college students, athletes, gym-goers, busy young "
            "adults; caffeine-for-study and pre-workout users; convenience-"
            "store impulse buyers; price-sensitive shoppers and premium-buyers "
            "alike."
        ),
        geography="California, United States",
        intended_user_or_buyer=(
            "college students, athletes, gym-goers, busy young adults"
        ),
        extra_context=(
            "Substitutes considered in scope: cold brew, coffee, pre-workout "
            "powders, electrolyte drinks. Relevance means evidence-backed "
            "energy / sports / caffeine buyers in the California market — "
            "not Triton-specific buyers."
        ),
        simulation_goal=SimulationGoal.TEST_PRICE,
    )
    plan = build_target_society_plan(triton_brief)
    print(
        f"plan: family={plan.interpreted_brief.detected_product_family.value}, "
        f"{len(plan.stakeholder_categories)} categories"
    )

    # ---- Load Triton-tagged source_records ---------------------------
    async with sm() as session:
        triton_sr = (await session.execute(
            select(SourceRecord)
            .where(
                SourceRecord.metadata_["target_brief"].astext
                == TARGET_BRIEF_TAG
            )
        )).scalars().all()
    print(f"\nTriton-tagged source_records loaded: {len(triton_sr)}")

    # Snapshot row counts BEFORE.
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
    print(
        f"row counts BEFORE: SR={before['SourceRecord']}, "
        f"PR={before['PersonaRecord']}, PT={before['PersonaTrait']}, "
        f"PEL={before['PersonaEvidenceLink']}"
    )

    persona_construction_summary = None
    audience_result = None
    error_in_run: str | None = None
    started = time.monotonic()

    try:
        # ---- Stage 3: persona construction --------------------------
        print("\n" + "=" * 70)
        print("Stage 3: persona construction (live LLMTraitExtractor)")
        print("=" * 70)

        # Create admin Simulation row to anchor cost_guarded_chat.
        admin_sim_id = uuid4()
        async with sm() as session:
            async with session.begin():
                session.add(Simulation(
                    id=admin_sim_id,
                    user_id=APPROVER_LABEL[:32],
                    # `simulations.status` is varchar(32); use a short tag.
                    status="phase_8_4a_persona_build",
                    progress={
                        "stage": "persona_construction",
                        "approver": APPROVER_LABEL,
                    },
                    total_cost_usd=Decimal("0"),
                    total_latency_ms=0,
                ))
        provider = AnthropicProvider()
        extractor = LLMTraitExtractor(
            sessionmaker=sm,
            simulation_id=admin_sim_id,
            provider=provider,
        )
        persona_construction_summary = await run_persona_construction(
            sessionmaker=sm,
            source_records=triton_sr,
            extractor=extractor,
            write_personas=True,
        )
        print(
            f"  candidate_shells={persona_construction_summary.candidate_shells}, "
            f"strong={persona_construction_summary.strong_persona_signal_records}, "
            f"weak={persona_construction_summary.weak_persona_signal_records}, "
            f"context_only={persona_construction_summary.context_only_records}, "
            f"rejected={persona_construction_summary.rejected_records}"
        )
        print(
            f"  personas_created={persona_construction_summary.personas_created}, "
            f"skipped={persona_construction_summary.personas_skipped}, "
            f"traits={persona_construction_summary.traits_created}, "
            f"links={persona_construction_summary.evidence_links_created}"
        )

        # ---- Stage 4: relevance audit -------------------------------
        print("\n" + "=" * 70)
        print("Stage 4: relevance audit against Triton plan")
        print("=" * 70)
        from assembly.pipeline.run_scoped_topup.executor import (
            _load_audience_inputs,
        )
        audience_inputs, domain_map = await _load_audience_inputs(sm)
        # Filter to Triton-only personas (those with evidence_links to
        # Triton-tagged source_records).
        triton_sr_ids = {r.id for r in triton_sr}
        async with sm() as session:
            triton_pids: set = set()
            if triton_sr_ids:
                links = (await session.execute(
                    select(PersonaEvidenceLink)
                    .where(
                        PersonaEvidenceLink.source_record_id.in_(triton_sr_ids)
                    )
                )).scalars().all()
                for el in links:
                    triton_pids.add(el.persona_id)
        triton_only_inputs = [
            ai for ai in audience_inputs if ai.persona_id in triton_pids
        ]
        print(
            f"  total persona pool: {len(audience_inputs)}, "
            f"Triton-only personas (via Triton-tagged sources): "
            f"{len(triton_only_inputs)}"
        )
        if triton_only_inputs:
            audience_result = retrieve_personas_for_target_society(
                brief=triton_brief,
                plan=plan,
                personas=triton_only_inputs,
                domain_by_record_id=domain_map,
            )
            print(
                f"  matched={len(audience_result.matched_personas)}, "
                f"excluded={len(audience_result.excluded_personas)}"
            )
    except Exception as e:
        error_in_run = f"{type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()
        print(f"\nUNEXPECTED ERROR: {error_in_run}")

    elapsed = time.monotonic() - started

    # Snapshot AFTER
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
    forbidden_deltas = {
        k: v for k, v in deltas.items()
        if k not in (
            "SourceRecord", "PersonaRecord", "PersonaTrait",
            "PersonaEvidenceLink",
        )
    }

    # Build relevant / weakly / excluded buckets
    relevant: list[dict] = []
    weakly: list[dict] = []
    excluded: list[dict] = []
    if audience_result is not None:
        for m in audience_result.matched_personas:
            entry = {
                "persona_id": m.persona_id,
                "display_name": m.display_name,
                "score": m.relevance_score,
                "classification": m.classification.value,
                "category": m.matched_category_key,
                "category_display": m.matched_category_display_name,
                "matched_signals": list(m.matched_signals)[:5],
                "evidence_link_count": m.evidence_link_count,
                "why_included": m.why_included[:300],
            }
            if m.classification in (
                RelevanceClassification.RELEVANT,
                RelevanceClassification.HIGHLY_RELEVANT,
            ):
                relevant.append(entry)
            elif m.classification == RelevanceClassification.WEAKLY_RELEVANT:
                weakly.append(entry)
        for ex in audience_result.excluded_personas:
            excluded.append({
                "persona_id": ex.persona_id,
                "display_name": ex.display_name,
                "exclusion_reason": ex.exclusion_reason[:300],
                "best_possible_category": ex.best_possible_category,
                "score": ex.score,
            })
    relevant.sort(key=lambda x: -x["score"])
    weakly.sort(key=lambda x: -x["score"])

    # Save audit JSON
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_4a_triton_society_build_resume",
        "completed_at": datetime.now(UTC).isoformat(),
        "runtime_s": round(elapsed, 1),
        "plan": {
            "family": plan.interpreted_brief.detected_product_family.value,
            "category_count": len(plan.stakeholder_categories),
            "categories": [
                {
                    "key": c.category_key,
                    "display": c.display_name,
                    "priority": c.priority,
                    "tiny_min": c.minimum_persona_target_tiny,
                    "small_min": c.minimum_persona_target_small,
                } for c in plan.stakeholder_categories
            ],
            "warnings": [
                {"code": w.code, "severity": w.severity.value,
                 "message": w.message[:200]}
                for w in plan.warnings_and_limitations
            ],
        },
        "persona_construction": (
            {
                "source_records_seen":
                    persona_construction_summary.source_records_seen,
                "candidate_shells":
                    persona_construction_summary.candidate_shells,
                "strong_persona_signal_records":
                    persona_construction_summary
                    .strong_persona_signal_records,
                "weak_persona_signal_records":
                    persona_construction_summary
                    .weak_persona_signal_records,
                "context_only_records":
                    persona_construction_summary.context_only_records,
                "rejected_records":
                    persona_construction_summary.rejected_records,
                "personas_created":
                    persona_construction_summary.personas_created,
                "personas_skipped":
                    persona_construction_summary.personas_skipped,
                "skipped_reasons": [
                    s.value for s in
                    persona_construction_summary.skipped_reasons
                ][:30],
                "traits_created":
                    persona_construction_summary.traits_created,
                "traits_rejected":
                    persona_construction_summary.traits_rejected,
                "evidence_links_created":
                    persona_construction_summary.evidence_links_created,
            }
            if persona_construction_summary is not None else None
        ),
        "audience_audit": (
            {
                "matched_count": len(audience_result.matched_personas),
                "relevant_or_better_count": len(relevant),
                "weakly_relevant_count": len(weakly),
                "excluded_count": len(excluded),
                "next_step":
                    audience_result.next_step_recommendation.value,
                "category_coverage": [
                    {
                        "key": cc.category_key,
                        "matched_total": cc.matched_total,
                        "label": cc.coverage_label.value,
                        "missing_signals": list(cc.missing_signals)[:5],
                    }
                    for cc in audience_result.category_coverage
                ],
                "warnings": list(audience_result.warnings_and_caveats),
            }
            if audience_result is not None else None
        ),
        "relevant_personas_top10": relevant[:10],
        "weakly_relevant_top10": weakly[:10],
        "excluded_top10": excluded[:10],
        "row_deltas": {
            "SourceRecord": deltas.get("SourceRecord"),
            "PersonaRecord": deltas.get("PersonaRecord"),
            "PersonaTrait": deltas.get("PersonaTrait"),
            "PersonaEvidenceLink": deltas.get("PersonaEvidenceLink"),
        },
        "forbidden_table_deltas_must_be_empty": forbidden_deltas,
        "error_in_run": error_in_run,
    }
    out_path = out_dir / "triton_society_build_8_4a_resume.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print("=" * 70)
    print("Phase 8.4A — TRITON SOCIETY BUILD SUMMARY (RESUME)")
    print("=" * 70)
    print(f"runtime: {elapsed:.1f}s")
    pr_delta = (deltas.get('PersonaRecord') or (0, 0))
    pt_delta = (deltas.get('PersonaTrait') or (0, 0))
    pel_delta = (deltas.get('PersonaEvidenceLink') or (0, 0))
    print(
        f"row deltas: PR+{pr_delta[1]-pr_delta[0]}, "
        f"PT+{pt_delta[1]-pt_delta[0]}, "
        f"PEL+{pel_delta[1]-pel_delta[0]}"
    )
    print(f"forbidden-table deltas (must be empty): {forbidden_deltas}")
    print(
        f"audience: relevant={len(relevant)}, "
        f"weakly={len(weakly)}, excluded={len(excluded)}"
    )
    print(f"\n→ audit JSON: {out_path}")
    return 0 if error_in_run is None else 1


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
