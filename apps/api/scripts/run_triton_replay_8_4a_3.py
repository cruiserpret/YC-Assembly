"""Phase 8.4A.3 — Triton replay through the dynamic planner +
market-entry weight profile + anchor inclusion gate.

READ-ONLY. No DB writes (except audit JSON). No LLM calls. No
ingestion. No persona / trait / evidence-link / graph / simulation
/ UI writes.

Replays the existing 44 Triton personas under:
  * Phase 8.4A.2 dynamic plan (22 categories from primitives)
  * Phase 8.4A.2 market-entry weight profile
  * Phase 8.4A.3 anchor inclusion gate (this phase)

Each persona is scored against every category, the best score is
taken, and the inclusion-gate is applied. Output: per-persona
before/after with anchor types, downgrade reason, and evidence
excerpts.
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
    from sqlalchemy import select
    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
    )
    from assembly.pipeline.audience_retrieval.inclusion_tier import (
        InclusionTier, classify_inclusion_tier_from_score,
    )
    from assembly.pipeline.audience_retrieval.market_entry_gate import (
        GATE_REASON_PASS,
        apply_market_entry_inclusion_gate,
    )
    from assembly.pipeline.audience_retrieval.scorer import (
        score_persona_against_category,
    )
    from assembly.pipeline.run_scoped_topup.executor import (
        _load_audience_inputs,
    )
    from assembly.pipeline.target_society import build_target_society_plan
    from assembly.pipeline.target_society.constants import SimulationGoal
    from assembly.pipeline.target_society.schemas import ProductBriefInput

    sm = get_sessionmaker()
    print("=" * 72)
    print("Phase 8.4A.3 — Triton replay (anchor inclusion gate; READ-ONLY)")
    print("=" * 72)

    triton_brief = ProductBriefInput(
        product_name="Triton Drinks",
        product_type="Caffeinated sports / energy drink",
        product_description=(
            "Triton Drinks is an unlaunched caffeinated sports/energy "
            "drink launching in California at $3.99 per can. Targeted "
            "at college students, athletes, gym-goers, busy young "
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
    weights = plan.scorer_weights
    print(
        f"plan: {len(plan.stakeholder_categories)} categories, "
        f"market-entry weights"
    )

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

    audience_inputs, _ = await _load_audience_inputs(sm)
    triton_only = [
        ai for ai in audience_inputs if ai.persona_id in triton_pids
    ]
    print(f"Triton-only personas loaded: {len(triton_only)}")

    # ---- For each persona: best-fit score across all categories,
    # then apply inclusion gate.
    rows: list[dict] = []
    for ai in triton_only:
        best_score = -100
        best_cat_key = None
        best_signals: tuple[str, ...] = tuple()
        for cat in plan.stakeholder_categories:
            bd = score_persona_against_category(
                ai, cat,
                geography_required=False,  # market-entry mode
                weights=weights,
            )
            if bd.total_score > best_score:
                best_score = bd.total_score
                best_cat_key = cat.category_key
                best_signals = bd.matched_signals[:6]

        result = apply_market_entry_inclusion_gate(
            persona=ai, plan=plan, score=best_score,
        )

        # Strongest evidence excerpts (longest 2 by char)
        excerpts = sorted(
            [el.excerpt for el in ai.evidence_links if el.excerpt],
            key=lambda s: -len(s),
        )[:2]
        excerpts = [e[:240] for e in excerpts]

        rows.append({
            "persona_id": str(ai.persona_id),
            "display_name": ai.display_name,
            "score": best_score,
            "best_category": best_cat_key,
            "best_matched_signals": list(best_signals),
            "base_tier_v8_4a_2": result.base_tier.value,
            "final_tier_v8_4a_3": result.final_tier.value,
            "downgrade_reason": result.reason,
            "anchor_has": result.anchor_report.has_anchor,
            "anchor_types": list(result.anchor_report.anchor_types),
            "matched_anchor_terms": list(
                result.anchor_report.matched_anchor_terms
            )[:8],
            "anchor_evidence_excerpts": list(
                result.anchor_report.anchor_evidence_excerpts
            ),
            "anchor_explanation": result.anchor_report.explanation,
            "evidence_excerpts": excerpts,
        })

    rows.sort(key=lambda r: -r["score"])

    # Tier distributions: base (Phase 8.4A.2) vs final (Phase 8.4A.3)
    base_counts: Counter = Counter(r["base_tier_v8_4a_2"] for r in rows)
    final_counts: Counter = Counter(r["final_tier_v8_4a_3"] for r in rows)

    # Personas downgraded by the gate
    downgrades = [
        r for r in rows
        if r["final_tier_v8_4a_3"] != r["base_tier_v8_4a_2"]
    ]

    # ---- Print summary
    print()
    print("=" * 72)
    print("PHASE 8.4A.3 — REPLAY SUMMARY")
    print("=" * 72)
    print()
    print("Tier distribution (BEFORE gate / Phase 8.4A.2 base):")
    for tier in ("core_relevant", "adjacent_relevant", "excluded"):
        print(f"  {tier}: {base_counts.get(tier, 0)}")
    print()
    print("Tier distribution (AFTER gate / Phase 8.4A.3 final):")
    for tier in ("core_relevant", "adjacent_relevant", "excluded"):
        print(f"  {tier}: {final_counts.get(tier, 0)}")
    print()
    print(f"personas downgraded by gate: {len(downgrades)}")
    for d in downgrades:
        print(
            f"  {d['display_name']:30s} | "
            f"{d['base_tier_v8_4a_2']:18s} → {d['final_tier_v8_4a_3']:8s} "
            f"| reason={d['downgrade_reason']} | score={d['score']}"
        )
    print()
    print("TOP 10 INCLUDED (CORE + ADJACENT) AFTER GATE:")
    included = [
        r for r in rows
        if r["final_tier_v8_4a_3"] in ("core_relevant", "adjacent_relevant")
    ]
    for r in included[:10]:
        print(
            f"  {r['display_name']:30s} | "
            f"{r['final_tier_v8_4a_3']:18s} | score={r['score']:3d} "
            f"| {','.join(r['anchor_types'])[:50]}"
        )

    # ---- Save audit JSON
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_4a_3_triton_replay_anchor_gate",
        "completed_at": datetime.now(UTC).isoformat(),
        "personas_replayed": len(rows),
        "tier_distribution_v8_4a_2_base": dict(base_counts),
        "tier_distribution_v8_4a_3_final": dict(final_counts),
        "downgrade_count": len(downgrades),
        "downgrades": downgrades,
        "personas_top10_included": included[:10],
        "personas_all": rows,
    }
    out_path = out_dir / "triton_replay_8_4a_3.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
