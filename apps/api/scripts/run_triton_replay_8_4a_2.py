"""Phase 8.4A.2 — Triton replay through the dynamic market-entry
planner + market-entry weight profile.

READ-ONLY. No DB writes (except the audit JSON file). No LLM calls.
No Tavily / Firecrawl / Brave / Reddit. No persona / trait / evidence-
link writes. No graph / simulation / UI writes.

Loads the existing 44 Triton personas from Phase 8.4A, re-runs the
audience-retrieval pipeline using the new dynamic-market-entry
planner output (22 generated categories from primitives) AND the new
market-entry weight profile, and produces a before/after delta
report:

  * old score (from Phase 8.4A — classic CPG template + classic weights)
  * new score (Phase 8.4A.2 — dynamic categories + market-entry weights)
  * old classification (under unchanged 27/36 thresholds)
  * new InclusionTier (CORE_RELEVANT / ADJACENT_RELEVANT / EXCLUDED)
  * old best category
  * new best category
  * strongest matched evidence
  * why included / excluded under new mode
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
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
    from sqlalchemy import select
    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink,
        PersonaRecord,
        PersonaTrait,
        SourceRecord,
    )
    from assembly.pipeline.audience_retrieval import (
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.audience_retrieval.inclusion_tier import (
        InclusionTier,
        classify_inclusion_tier_from_score,
    )
    from assembly.pipeline.audience_retrieval.scorer import (
        score_persona_against_category,
    )
    from assembly.pipeline.persona_relevance.auditor import (
        EvidenceLinkView,
        PersonaAuditInput,
        TraitView,
    )
    from assembly.pipeline.persona_relevance.rubric import (
        RelevanceClassification,
    )
    from assembly.pipeline.run_scoped_topup.executor import (
        _load_audience_inputs,
    )
    from assembly.pipeline.target_society import build_target_society_plan
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput

    sm = get_sessionmaker()
    print("=" * 70)
    print("Phase 8.4A.2 — Triton replay through dynamic planner (READ-ONLY)")
    print("=" * 70)

    # ---- Build Triton brief — explicitly TEST_MARKET_ENTRY this time
    triton_brief_v2 = ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can. Targeted "
            "at college students, athletes, gym-goers, and busy young "
            "adults. Competes with Red Bull and Monster on the energy "
            "drink shelf, and overlaps with Celsius, Prime, Gatorade, "
            "pre-workout drinks, cold brew, and electrolyte drinks. "
            "Triton is unlaunched; relevance means evidence-backed "
            "buyers / rejectors / influencers in the category, not "
            "Triton-specific buyers."
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
            "pre-workout powders, electrolyte drinks. Triton is "
            "unlaunched; relevance is market-entry, not direct-product."
        ),
        # Explicit market-entry goal (per Phase 8.4A.2 design)
        simulation_goal=SimulationGoal.TEST_MARKET_ENTRY,
    )
    plan_v2 = build_target_society_plan(triton_brief_v2)
    print(
        f"plan: family={plan_v2.interpreted_brief.detected_product_family.value}, "
        f"{len(plan_v2.stakeholder_categories)} categories, "
        f"market_entry weights"
    )
    for c in plan_v2.stakeholder_categories[:8]:
        print(f"  [{c.priority}] {c.category_key}")
    if len(plan_v2.stakeholder_categories) > 8:
        print(f"  ... + {len(plan_v2.stakeholder_categories) - 8} more")

    # ---- Build the OLD plan (Phase 8.4A — classic CPG template)
    triton_brief_v1 = triton_brief_v2.model_copy(update={
        "simulation_goal": SimulationGoal.TEST_PRICE,
        "product_description": (
            # Strip "unlaunched" markers so the planner falls back to
            # the classic CPG template path.
            "Triton Drinks is a caffeinated sports/energy drink for "
            "California at $3.99 per can. Targeted at college "
            "students, athletes, gym-goers, busy young adults. "
            "Competes with Red Bull and Monster."
        ),
        "extra_context": "Substitutes include cold brew, coffee.",
    })
    plan_v1 = build_target_society_plan(triton_brief_v1)
    assert any(
        c.category_key == "mass_market_grocery_buyer"
        for c in plan_v1.stakeholder_categories
    ), "v1 plan must use classic CPG template"

    # ---- Load 44 Triton personas
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
    print(f"\nTriton-tagged source_records: {len(triton_sr)}")
    print(f"Triton-only personas:         {len(triton_pids)}")

    # Load full PersonaAuditInput for each Triton persona
    audience_inputs, domain_map = await _load_audience_inputs(sm)
    triton_only = [
        ai for ai in audience_inputs if ai.persona_id in triton_pids
    ]
    print(f"PersonaAuditInput for Triton: {len(triton_only)}")

    # ---- v1 audit: classic plan + classic weights
    v1_audit = retrieve_personas_for_target_society(
        brief=triton_brief_v1,
        plan=plan_v1,
        personas=triton_only,
        domain_by_record_id=domain_map,
    )
    v1_by_pid: dict[str, dict] = {}
    for m in v1_audit.matched_personas:
        v1_by_pid[m.persona_id] = {
            "score": m.relevance_score,
            "classification": m.classification.value,
            "category": m.matched_category_key,
        }
    for ex in v1_audit.excluded_personas:
        v1_by_pid[ex.persona_id] = {
            "score": ex.score,
            "classification": "not_relevant",
            "category": ex.best_possible_category,
        }

    # ---- v2 audit: dynamic plan + market-entry weights
    v2_audit = retrieve_personas_for_target_society(
        brief=triton_brief_v2,
        plan=plan_v2,
        personas=triton_only,
        domain_by_record_id=domain_map,
    )
    v2_by_pid: dict[str, dict] = {}
    for m in v2_audit.matched_personas:
        v2_by_pid[m.persona_id] = {
            "score": m.relevance_score,
            "classification": m.classification.value,
            "category": m.matched_category_key,
            "matched_signals": list(m.matched_signals)[:6],
            "why_included": m.why_included[:300],
            "evidence_link_count": m.evidence_link_count,
        }
    for ex in v2_audit.excluded_personas:
        v2_by_pid[ex.persona_id] = {
            "score": ex.score,
            "classification": "not_relevant",
            "category": ex.best_possible_category,
            "matched_signals": [],
            "why_included": ex.exclusion_reason[:300],
            "evidence_link_count": 0,
        }

    # ---- Compute tier distribution under v2
    tier_counts: Counter = Counter()
    deltas: list[dict] = []
    for ai in triton_only:
        pid = str(ai.persona_id)
        v1 = v1_by_pid.get(pid)
        v2 = v2_by_pid.get(pid)
        if v2 is None:
            continue
        new_tier = classify_inclusion_tier_from_score(v2["score"])
        tier_counts[new_tier.value] += 1
        # Strongest evidence excerpts (longest 2 by char count)
        excerpts = sorted(
            [el.excerpt for el in ai.evidence_links if el.excerpt],
            key=lambda s: -len(s),
        )[:2]
        excerpts = [e[:240] for e in excerpts]
        deltas.append({
            "persona_id": pid,
            "display_name": ai.display_name,
            "old_score": v1["score"] if v1 else None,
            "new_score": v2["score"],
            "old_classification": (
                v1["classification"] if v1 else None
            ),
            "new_classification": v2["classification"],
            "new_tier": new_tier.value,
            "old_best_category": v1["category"] if v1 else None,
            "new_best_category": v2["category"],
            "score_delta": (
                v2["score"] - v1["score"] if v1 else None
            ),
            "evidence_excerpts": excerpts,
            "matched_signals_v2": v2["matched_signals"],
            "why_included_v2": v2["why_included"],
            "evidence_link_count": v2["evidence_link_count"],
        })

    deltas.sort(key=lambda r: -r["new_score"])

    # ---- Print operator-facing summary
    print()
    print("=" * 70)
    print("REPLAY SUMMARY")
    print("=" * 70)
    print(f"personas replayed: {len(deltas)}")
    print()
    print("InclusionTier distribution (v2 / market-entry mode):")
    for tier_name in ("core_relevant", "adjacent_relevant", "excluded"):
        n = tier_counts.get(tier_name, 0)
        print(f"  {tier_name}: {n}")
    print()
    print("TOP 10 RECLASSIFIED (sorted by new_score):")
    for r in deltas[:10]:
        print(
            f"  {r['display_name']} | old={r['old_score']} "
            f"({r['old_classification']}) → new={r['new_score']} "
            f"({r['new_tier']}) | cat={r['new_best_category']}"
        )

    # ---- Save audit JSON
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_4a_2_triton_replay_dynamic_planner",
        "completed_at": datetime.now(UTC).isoformat(),
        "personas_replayed": len(deltas),
        "v1_plan_summary": {
            "family": plan_v1.interpreted_brief.detected_product_family.value,
            "category_count": len(plan_v1.stakeholder_categories),
            "categories": [c.category_key for c in plan_v1.stakeholder_categories],
        },
        "v2_plan_summary": {
            "family": plan_v2.interpreted_brief.detected_product_family.value,
            "is_market_entry": True,
            "category_count": len(plan_v2.stakeholder_categories),
            "categories": [
                {"key": c.category_key, "priority": c.priority}
                for c in plan_v2.stakeholder_categories
            ],
            "weights": dict(plan_v2.scorer_weights or {}),
            "geography_required": (
                plan_v2.coverage_requirements.geography_coverage_required
            ),
        },
        "tier_distribution_v2": dict(tier_counts),
        "personas_top10_v2": deltas[:10],
        "personas_all": deltas,
    }
    out_path = out_dir / "triton_replay_8_4a_2.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
