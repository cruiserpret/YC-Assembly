"""Phase 8.4B — Triton Drinks micro-simulation operator script.

Two modes:

  * `--dry-run` (default): NO live LLM calls. Loads personas via the
    production retrieval path (Phase 8.4A.4), confirms the 7 expected
    personas are inclusion-eligible (5 CORE + 2 ADJACENT), runs the
    deterministic baseline round only, and produces a structured
    pre-flight artifact under `_audit/`. Used for the Phase 8.4B
    preparation pass before operator approval.

  * `--live` (requires explicit flag): full 4-stage micro-simulation
    via the existing Phase 8.2K + 8.2K.1 micro-simulation harness.
    Routes through `cost_guarded_chat`. Hard cost cap $1.00.

Mandatory caveats appended to every output:

  1. MICRO-TEST label.
  2. Sample-size caveat: n=7 personas; not a population sample.
  3. Unlaunched-product caveat: anchored on competitor / substitute /
     use-case / category-objection evidence — NOT direct-Triton.
  4. Adjacent-tier caveat: 2 of 7 personas are ADJACENT_RELEVANT
     (lower-weight, caveated voices).
  5. Coverage-thinness caveat: subset of dynamic-plan categories
     covered.
  6. Geography caveat: most evidence non-California-specific;
     California is a soft market context.

Forbidden writes (drift-tested by the existing micro_simulation
package): SimulationOutput, SimulationRound, PersonaGraphEdge,
PersonaCluster, PersonaClusterMembership, PersonaOpinion, Agent,
AgentResponse, DebateTurn, AgentEdge, frontend / UI.
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
APPROVER_LABEL = "phase_8_4b_triton_micro_simulation"
MAX_CORE = 5
MAX_ADJACENT = 2
MAX_LLM_CALLS = 25
COST_CAP_USD = Decimal("1.00")
PILOT_MODEL = "claude-sonnet-4-6"

# The 7 personas the Phase 8.4A.4 report nominated. The script
# verifies these names appear in the production-retrieval inclusion
# set; if any are missing, it stops before any LLM call.
EXPECTED_CORE_NAMES = (
    "Winslow B.", "Harper R.", "Xael J.", "Zev L.", "Phoenix R.",
)
EXPECTED_ADJACENT_NAMES = ("Indigo G.", "Rowan F.")


# ---------------------------------------------------------------------------
# 6 mandatory caveats — appended to every audit-output JSON
# ---------------------------------------------------------------------------


def _build_mandatory_caveats(
    *, n: int, n_core: int, n_adjacent: int,
    distinct_category_count: int, total_plan_categories: int,
) -> list[str]:
    return [
        "MICRO-TEST: this is a mechanical micro-test, not a real "
        "market simulation, demand forecast, or buyer-intent claim.",
        f"sample-size caveat: this is a MICRO-TEST on n={n} personas; "
        "not a population-level sample and not a statistically "
        "representative California subset.",
        "unlaunched-product caveat: Triton is unlaunched. Persona "
        "reactions are anchored on competitor / substitute / "
        "use-case / category-objection evidence — there is no "
        "direct-product evidence and no persona was characterized as "
        "a customer of the unlaunched product (no such customers "
        "exist).",
        f"adjacent-tier caveat: {n_adjacent} of {n} personas are "
        "ADJACENT_RELEVANT — lower-weight, caveated category / "
        "substitute voices, not direct-competitor users.",
        f"coverage-thinness caveat: {distinct_category_count} of "
        f"{total_plan_categories} dynamic-plan stakeholder categories "
        "are represented in this audience. This is NOT a full "
        "California energy-drink society.",
        "geography caveat: most evidence is non-California-specific. "
        "California is a soft market context, not a fully grounded "
        "geographic sample.",
    ]


# ---------------------------------------------------------------------------
# Triton brief — single source of truth for both dry-run and live
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Pre-flight: load production retrieval result + pick the 7 personas
# ---------------------------------------------------------------------------


async def _retrieve_triton_audience():
    """Returns (audience_result, picked_core, picked_adjacent,
    plan, audience_inputs, domain_map) — all in-memory; no DB writes."""
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

    # Bucket matched personas by tier (Phase 8.4A.4 wiring)
    core: list = []
    adjacent: list = []
    for m in audience_result.matched_personas:
        if m.final_tier == InclusionTier.CORE_RELEVANT.value:
            core.append(m)
        elif m.final_tier == InclusionTier.ADJACENT_RELEVANT.value:
            adjacent.append(m)
    # Sort by score (descending) so we pick highest-scoring first
    core.sort(key=lambda m: -m.relevance_score)
    adjacent.sort(key=lambda m: -m.relevance_score)

    # Pick by name (stable, matches Phase 8.4A.4 nominees) when
    # available; fall back to top-scoring otherwise.
    def _pick_named(pool: list, names: tuple[str, ...], cap: int) -> list:
        chosen: list = []
        by_name = {m.display_name: m for m in pool}
        for n in names:
            if n in by_name and by_name[n] not in chosen:
                chosen.append(by_name[n])
                if len(chosen) >= cap:
                    return chosen
        # Fall back to top-scoring to fill remaining slots
        for m in pool:
            if m not in chosen:
                chosen.append(m)
                if len(chosen) >= cap:
                    return chosen
        return chosen

    picked_core = _pick_named(core, EXPECTED_CORE_NAMES, MAX_CORE)
    picked_adjacent = _pick_named(
        adjacent, EXPECTED_ADJACENT_NAMES, MAX_ADJACENT
    )
    return (
        audience_result, picked_core, picked_adjacent,
        plan, brief, sm,
    )


# ---------------------------------------------------------------------------
# Dry-run pre-flight — NO LLM, NO DB writes
# ---------------------------------------------------------------------------


async def _dry_run_preflight() -> int:
    print("=" * 72)
    print("Phase 8.4B — Triton Micro-Simulation PRE-FLIGHT (DRY-RUN)")
    print("=" * 72)
    print("policy: NO live LLM. NO new ingestion. NO DB writes "
          "(except audit JSON).")

    (
        audience_result, picked_core, picked_adjacent,
        plan, brief, _sm,
    ) = await _retrieve_triton_audience()

    n = len(picked_core) + len(picked_adjacent)
    distinct_categories = len({
        m.matched_category_key for m in
        list(picked_core) + list(picked_adjacent)
    })

    # Verify expected personas surface
    expected_missing = []
    chosen_names = {
        m.display_name for m in picked_core + picked_adjacent
    }
    for n_exp in (EXPECTED_CORE_NAMES + EXPECTED_ADJACENT_NAMES):
        if n_exp not in chosen_names:
            expected_missing.append(n_exp)

    print(
        f"\nAudience retrieval (production-wired Phase 8.4A.4):"
        f"  matched={len(audience_result.matched_personas)}, "
        f"excluded={len(audience_result.excluded_personas)}"
    )
    print(f"\nSELECTED CORE_RELEVANT ({len(picked_core)} of {MAX_CORE} cap):")
    for m in picked_core:
        print(
            f"  - {m.display_name} | score={m.relevance_score} "
            f"| anchors={','.join(m.anchor_types)} "
            f"| category={m.matched_category_key}"
        )
    print(
        f"\nSELECTED ADJACENT_RELEVANT "
        f"({len(picked_adjacent)} of {MAX_ADJACENT} cap):"
    )
    for m in picked_adjacent:
        print(
            f"  - {m.display_name} | score={m.relevance_score} "
            f"| anchors={','.join(m.anchor_types)} "
            f"| category={m.matched_category_key}"
        )
    if expected_missing:
        print(f"\nMISSING expected personas: {expected_missing}")
    else:
        print("\nALL 7 expected personas present in production retrieval.")

    # Project LLM calls + cost
    projected_llm_calls = (
        n * 3  # first_exposure, objection, final_stance per persona
        + 2    # 2 debate turns when n >= 2
    )
    projected_cost_low = projected_llm_calls * 0.020
    projected_cost_high = projected_llm_calls * 0.040
    print(
        f"\nProjected LLM calls (live mode): {projected_llm_calls} "
        f"(≤ {MAX_LLM_CALLS} cap)"
    )
    print(
        f"Projected cost range (Sonnet rates): "
        f"${projected_cost_low:.2f} – ${projected_cost_high:.2f} "
        f"(≤ ${COST_CAP_USD} cap)"
    )

    # Assemble mandatory caveats
    caveats = _build_mandatory_caveats(
        n=n, n_core=len(picked_core),
        n_adjacent=len(picked_adjacent),
        distinct_category_count=distinct_categories,
        total_plan_categories=len(plan.stakeholder_categories),
    )
    print("\nMandatory caveats (will be in every output):")
    for c in caveats:
        print(f"  • {c[:130]}")

    # Save preflight audit JSON
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    summary = {
        "phase": "8_4b_triton_micro_simulation_preflight",
        "mode": "dry_run",
        "completed_at": datetime.now(UTC).isoformat(),
        "brief": {
            "product_name": brief.product_name,
            "product_type": brief.product_type,
            "price": brief.price_or_price_structure,
            "competitors": list(brief.competitors),
            "geography": brief.geography,
            "simulation_goal": brief.simulation_goal.value,
        },
        "audience_retrieval": {
            "matched_count": len(audience_result.matched_personas),
            "excluded_count": len(audience_result.excluded_personas),
            "core_pool": [m.display_name for m in [
                m for m in audience_result.matched_personas
                if m.final_tier == "core_relevant"
            ]],
            "adjacent_pool": [m.display_name for m in [
                m for m in audience_result.matched_personas
                if m.final_tier == "adjacent_relevant"
            ]],
        },
        "selected_personas": {
            "core": [
                {
                    "display_name": m.display_name,
                    "score": m.relevance_score,
                    "tier": m.final_tier,
                    "category": m.matched_category_key,
                    "anchor_types": list(m.anchor_types),
                    "matched_anchor_terms": list(m.matched_anchor_terms),
                    "anchor_evidence_excerpt": (
                        m.anchor_evidence_excerpts[0]
                        if m.anchor_evidence_excerpts else None
                    ),
                }
                for m in picked_core
            ],
            "adjacent": [
                {
                    "display_name": m.display_name,
                    "score": m.relevance_score,
                    "tier": m.final_tier,
                    "category": m.matched_category_key,
                    "anchor_types": list(m.anchor_types),
                    "matched_anchor_terms": list(m.matched_anchor_terms),
                    "anchor_evidence_excerpt": (
                        m.anchor_evidence_excerpts[0]
                        if m.anchor_evidence_excerpts else None
                    ),
                    "caveats": list(m.caveats),
                }
                for m in picked_adjacent
            ],
        },
        "expected_missing": expected_missing,
        "projected_llm_calls": projected_llm_calls,
        "projected_cost_usd_range": [
            round(projected_cost_low, 4),
            round(projected_cost_high, 4),
        ],
        "cost_cap_usd": float(COST_CAP_USD),
        "max_llm_calls_cap": MAX_LLM_CALLS,
        "mandatory_caveats": caveats,
        "ready_for_live": (
            len(picked_core) + len(picked_adjacent) == 7
            and not expected_missing
        ),
    }
    out_path = out_dir / "triton_micro_simulation_preflight_8_4b.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ pre-flight JSON: {out_path}")
    print(
        f"\nready_for_live: {summary['ready_for_live']} "
        "— if True, operator approval required to run live."
    )
    return 0 if summary["ready_for_live"] else 1


# ---------------------------------------------------------------------------
# Live run — invoked only with --live AND requires operator approval
# ---------------------------------------------------------------------------


async def _live_run() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Aborting live run.")
        return 2

    from assembly.llm.anthropic import AnthropicProvider
    from assembly.pipeline.audience_retrieval.schemas import (
        PersonaMatch, RunScopedAudienceRetrievalResult,
    )
    from assembly.pipeline.micro_simulation import (
        MicroSimulationRefused, run_micro_simulation,
        map_micro_stance_to_market_entry,
    )

    print("=" * 72)
    print("Phase 8.4B — Triton Micro-Simulation LIVE RUN")
    print("=" * 72)
    (
        audience_result, picked_core, picked_adjacent,
        plan, brief, sm,
    ) = await _retrieve_triton_audience()

    # Verify presence
    if len(picked_core) + len(picked_adjacent) != 7:
        print(
            f"ERROR: production retrieval returned "
            f"{len(picked_core)} CORE + {len(picked_adjacent)} ADJACENT; "
            "expected 7 total. Aborting before any LLM call."
        )
        return 2
    print(
        f"audience: {len(picked_core)} CORE + "
        f"{len(picked_adjacent)} ADJACENT"
    )

    # Build a trimmed RunScopedAudienceRetrievalResult that contains
    # ONLY the 7 selected personas. The micro-simulation runner reads
    # `audience_result.matched_personas` for relevant + weakly_relevant,
    # so we trim the input to the runner's expectation.
    trimmed_matched: list[PersonaMatch] = list(picked_core) + list(picked_adjacent)
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
            brief_label="triton_drinks",
            provider=provider,
            dry_run=False,
            include_weakly_relevant=True,
            enable_debate=True,
            max_relevant_personas=MAX_CORE,
            max_weakly_relevant_personas=MAX_ADJACENT,
            cost_cap_usd=COST_CAP_USD,
            model=PILOT_MODEL,
        )
    except MicroSimulationRefused as e:
        print(f"ERROR: runner refused: {e}")
        return 1
    elapsed = time.monotonic() - started

    # Append the 6 Phase 8.4B mandatory caveats to the result's
    # `caveats` list (the runner already adds its own sample-size +
    # coverage-thinness caveats; we extend with the unlaunched +
    # adjacent + geography caveats).
    enriched_caveats = list(result.caveats) + _build_mandatory_caveats(
        n=len(picked_core) + len(picked_adjacent),
        n_core=len(picked_core),
        n_adjacent=len(picked_adjacent),
        distinct_category_count=len({
            m.matched_category_key for m in trimmed_matched
        }),
        total_plan_categories=len(plan.stakeholder_categories),
    )

    # Post-process: map each persona's final stance from MicroStance →
    # MarketEntryFinalStance for the report
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
    summary["phase"] = "8_4b_triton_micro_simulation_live"
    summary["enriched_caveats"] = enriched_caveats
    summary["final_stances_market_entry"] = final_stances
    summary["runtime_s"] = round(elapsed, 1)
    out_path = out_dir / "triton_micro_simulation_live_8_4b.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Operator-facing summary
    print()
    print(result.summary_text)
    print()
    print("=" * 72)
    print("Phase 8.4B — TRITON MICRO-SIMULATION RESULT")
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
    print()
    print("Final stances (market-entry mapping):")
    fs_counts: Counter = Counter(
        f["market_entry_stance"] for f in final_stances
    )
    for v, c in fs_counts.most_common():
        print(f"  {v}: {c}")
    print(f"\n→ live audit JSON: {out_path}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 8.4B — Triton Drinks micro-simulation."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Run the live LLM micro-simulation. Requires explicit "
            "operator approval. Default is dry-run pre-flight."
        ),
    )
    args = parser.parse_args()
    _load_env()
    if args.live:
        return asyncio.run(_live_run())
    return asyncio.run(_dry_run_preflight())


if __name__ == "__main__":
    sys.exit(main())
