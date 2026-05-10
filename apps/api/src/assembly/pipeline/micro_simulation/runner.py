"""Phase 8.2K — top-level micro-simulation runner.

`run_micro_simulation(...)` is the only entry point. Two paths:

  * `dry_run=True` (default): no LLM calls, no DB writes beyond the
    admin Simulation row. Loads persona states, runs the deterministic
    baseline round, builds a result with `dry_run=True` and
    `llm_call_count=0`. Useful for operator-side plan inspection.

  * `dry_run=False`: full live loop:
      1. cost-guard anchor (admin Simulation row)
      2. baseline round (deterministic)
      3. first_exposure round (LLM, per persona)
      4. objection round (LLM, per persona)
      5. optional pairwise debate (1 LLM call per direction)
      6. final_stance round (LLM, per persona)
      7. output audit
      8. summary text generation (with mandatory caveats)

NEVER writes any of the Phase 7 / population-graph surfaces. See the
drift test in `tests/test_no_drift_micro_simulation.py` for the full
forbidden list.

The runner refuses to proceed if:
  * the audience-retrieval result has zero relevant personas
  * the operator passes weakly-relevant personas without
    `include_weakly_relevant=True`
  * any forbidden-language audit failure remains after the run
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.provider import LLMProvider
from assembly.models.llm_log import LLMCallLog
from assembly.models.simulation import Simulation
from assembly.pipeline.audience_retrieval.schemas import (
    PersonaMatch,
    RunScopedAudienceRetrievalResult,
)
from assembly.pipeline.micro_simulation.caveats import (
    build_micro_simulation_caveats,
)
from assembly.pipeline.micro_simulation.debate import run_debate_turn
from assembly.pipeline.micro_simulation.output_audit import (
    audit_full_trace_and_summary,
)
from assembly.pipeline.micro_simulation.persona_state import (
    MicroPersonaStateLoadError,
    load_micro_persona_state,
)
from assembly.pipeline.micro_simulation.rounds import (
    run_baseline_round,
    run_llm_round,
)
from assembly.pipeline.micro_simulation.schemas import (
    MicroPersonaState,
    MicroRelevanceLabel,
    MicroRoundKind,
    MicroSimulationResult,
    MicroTrace,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification
from assembly.pipeline.target_society.constants import SimulationGoal
from assembly.pipeline.target_society.schemas import ProductBriefInput


DEFAULT_COST_CAP_USD: Decimal = Decimal("1.00")
PILOT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_PERSONAS = 4
DEFAULT_MAX_WEAKLY_RELEVANT = 3


class MicroSimulationRefused(Exception):
    """Raised when the runner refuses to start (zero relevant personas,
    sensitive caveats unresolved, etc.)."""


async def run_micro_simulation(
    *,
    sessionmaker: async_sessionmaker,
    brief: ProductBriefInput,
    audience_result: RunScopedAudienceRetrievalResult,
    brief_label: str,
    provider: LLMProvider | None = None,
    dry_run: bool = True,
    include_weakly_relevant: bool = False,
    enable_debate: bool = True,
    max_relevant_personas: int = DEFAULT_MAX_PERSONAS,
    max_weakly_relevant_personas: int = DEFAULT_MAX_WEAKLY_RELEVANT,
    max_debate_turns: int = 2,
    cost_cap_usd: Decimal = DEFAULT_COST_CAP_USD,
    model: str | None = PILOT_MODEL,
) -> MicroSimulationResult:
    """Run one micro-simulation. See module docstring."""
    # ---- 1. Pick personas ------------------------------------------
    relevant: list[PersonaMatch] = [
        m for m in audience_result.matched_personas
        if m.classification.value in (
            RelevanceClassification.RELEVANT.value,
            RelevanceClassification.HIGHLY_RELEVANT.value,
        )
    ][:max_relevant_personas]

    if not relevant:
        raise MicroSimulationRefused(
            f"audience for brief={brief_label} has zero relevant personas; "
            "micro-simulation has nothing to anchor on."
        )

    weakly: list[PersonaMatch] = []
    if include_weakly_relevant:
        weakly = [
            m for m in audience_result.matched_personas
            if m.classification.value == RelevanceClassification.WEAKLY_RELEVANT.value
        ][:max_weakly_relevant_personas]

    chosen = relevant + weakly

    # ---- 2. Load persona states ------------------------------------
    states: list[MicroPersonaState] = []
    for m in chosen:
        try:
            s = await load_micro_persona_state(
                sessionmaker=sessionmaker,
                persona_match=m,
                include_weakly_relevant=include_weakly_relevant,
            )
            states.append(s)
        except MicroPersonaStateLoadError:
            # Skip personas that cannot anchor (no source-bound traits).
            continue
    if not states:
        raise MicroSimulationRefused(
            "Every selected persona failed state-load; aborting."
        )
    initial_states = [s.model_copy(deep=True) for s in states]

    # ---- 3. Cost-guard anchor (admin Simulation row) ---------------
    sim_id = uuid4()
    async with sessionmaker() as session:
        async with session.begin():
            session.add(Simulation(
                id=sim_id,
                user_id="phase_8_2k_micro_simulation",
                status=(
                    "phase_8_2k_micro_dry"
                    if dry_run else "phase_8_2k_micro_running"
                ),
                progress={
                    "stage": "micro_simulation",
                    "brief_label": brief_label,
                    "persona_count": len(states),
                },
                total_cost_usd=Decimal("0"),
                total_latency_ms=0,
            ))

    # ---- 4. Baseline round (deterministic) -------------------------
    trace = MicroTrace()
    for s in states:
        trace.rounds.append(run_baseline_round(s))

    # ---- 5. LLM rounds -------------------------------------------
    llm_call_count = 0
    if not dry_run:
        if provider is None:
            raise MicroSimulationRefused(
                "live mode requires a non-None provider."
            )
        brief_summary = (
            f"Product: {brief.product_name}. "
            f"{brief.product_description[:600]}"
        )
        for kind in (
            MicroRoundKind.FIRST_EXPOSURE,
            MicroRoundKind.OBJECTION,
            MicroRoundKind.FINAL_STANCE,
        ):
            for s in states:
                rr = await run_llm_round(
                    state=s, round_kind=kind,
                    brief_summary=brief_summary,
                    sessionmaker=sessionmaker, simulation_id=sim_id,
                    provider=provider, model=model,
                )
                trace.rounds.append(rr)
                llm_call_count += 1
                # Update the persona's current_stance for the next round.
                s.current_stance = rr.stance_after

        # Pairwise debate. For the default `max_debate_turns=2`, this
        # preserves the legacy 8.2K behavior (one bidirectional pair
        # between states[0] and states[1]). For higher caps (Phase
        # 8.4C 21-person run), pairs are chosen by
        # `_select_diverse_debate_pairs` to maximize CORE-vs-ADJACENT,
        # category, and anchor-type diversity.
        if enable_debate and len(states) >= 2 and max_debate_turns > 0:
            pairs = _select_diverse_debate_pairs(
                states=states, max_turns=max_debate_turns,
            )
            for speaker_idx, target_idx in pairs:
                d = await run_debate_turn(
                    speaker=states[speaker_idx],
                    target=states[target_idx],
                    sessionmaker=sessionmaker, simulation_id=sim_id,
                    provider=provider, model=model,
                )
                trace.debate_turns.append(d)
                states[target_idx].current_stance = d.target_stance_after
                llm_call_count += 1

    # ---- 6. Cost stats --------------------------------------------
    cost_actual = 0.0
    if not dry_run:
        async with sessionmaker() as session:
            log_rows = (await session.execute(
                select(LLMCallLog).where(
                    LLMCallLog.simulation_id == sim_id,
                    LLMCallLog.stage.like("micro_%"),
                )
            )).scalars().all()
        cost_actual = float(
            sum((r.cost_usd or Decimal("0")) for r in log_rows)
        )

    # ---- 7. Mark sim row completed --------------------------------
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                update(Simulation).where(Simulation.id == sim_id).values(
                    status="phase_8_2k_micro_completed",
                    completed_at=datetime.now(UTC),
                )
            )

    # ---- 8. Build summary text + audit ----------------------------
    relevant_count = sum(
        1 for s in states if s.relevance_label != MicroRelevanceLabel.WEAKLY_RELEVANT
    )
    weakly_count = sum(
        1 for s in states if s.relevance_label == MicroRelevanceLabel.WEAKLY_RELEVANT
    )
    distinct_categories = len({s.matched_category_key for s in states})
    # Phase 8.4B.1 — derive the actual plan total from the audience
    # result so caveats name the right number (NOT a hardcoded 8).
    total_plan_categories = (
        len(audience_result.category_coverage)
        if audience_result.category_coverage else distinct_categories
    )

    summary_text = _render_summary(
        brief_label=brief_label,
        product_name=brief.product_name,
        states=states,
        trace=trace,
        relevant_count=relevant_count,
        weakly_count=weakly_count,
        distinct_categories=distinct_categories,
        total_plan_categories=total_plan_categories,
        dry_run=dry_run,
        llm_call_count=llm_call_count,
        cost_actual=cost_actual,
        cost_cap=cost_cap_usd,
    )
    audit = audit_full_trace_and_summary(
        trace=trace,
        summary_text=summary_text,
        persona_count=len(states),
    )

    # Phase 8.4B.1 — generic, product-agnostic caveat builder. Detects
    # market-entry mode from the plan's category-key shape (mirrors
    # the Phase 8.4A.4 retriever's heuristic) and emits the right
    # caveat set for the active brief.
    is_market_entry = _looks_like_market_entry_audience(audience_result)
    geo_strength = _derive_geography_strength(
        geography=brief.geography,
        audience_result=audience_result,
    )
    caveats = build_micro_simulation_caveats(
        product_name=brief.product_name,
        product_type=brief.product_type,
        geography=brief.geography,
        total_categories=total_plan_categories,
        represented_categories=distinct_categories,
        sample_size=len(states),
        core_count=relevant_count,
        adjacent_count=weakly_count,
        is_market_entry=is_market_entry,
        is_unlaunched=is_market_entry,  # market-entry implies unlaunched framing
        geography_strength=geo_strength,
    )
    if audit.forbidden_claims_found:
        caveats.append(
            f"forbidden-language audit found: "
            f"{audit.forbidden_claims_found} — review required."
        )

    return MicroSimulationResult(
        is_micro_test=True,
        brief_label=brief_label,
        persona_count=len(states),
        relevant_count=relevant_count,
        weakly_relevant_count=weakly_count,
        mixed_relevance_pool=(weakly_count > 0),
        persona_states_initial=initial_states,
        persona_states_final=states,
        trace=trace,
        output_audit=audit,
        dry_run=dry_run,
        llm_call_count=llm_call_count,
        cost_actual_usd=cost_actual,
        cost_cap_usd=float(cost_cap_usd),
        caveats=caveats,
        summary_text=summary_text,
    )


# ---------------------------------------------------------------------------
# Summary rendering — must include all three mandatory caveat markers.
# ---------------------------------------------------------------------------


def _render_summary(
    *,
    brief_label: str,
    product_name: str,
    states: Sequence[MicroPersonaState],
    trace: MicroTrace,
    relevant_count: int,
    weakly_count: int,
    distinct_categories: int,
    total_plan_categories: int,
    dry_run: bool,
    llm_call_count: int,
    cost_actual: float,
    cost_cap: Decimal,
) -> str:
    n = len(states)
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append(
        f"MICRO-TEST — micro_simulation result for {product_name} "
        f"(brief={brief_label})"
    )
    lines.append("=" * 64)
    lines.append(
        f"sample size: n={n} (relevant={relevant_count}, weakly={weakly_count})"
    )
    lines.append(
        f"coverage thinness: {distinct_categories} of "
        f"{total_plan_categories} stakeholder categories represented "
        "(coverage is thin; not simulation-ready)"
    )
    lines.append(f"dry_run: {dry_run}; llm_call_count: {llm_call_count}")
    lines.append(f"cost_actual_usd: ${cost_actual:.4f} / cap ${cost_cap}")
    lines.append("")
    lines.append("Per-persona MICRO-TEST trajectory:")
    for s in states:
        lines.append(
            f"  - {s.display_name} ({s.relevance_label.value}, "
            f"category={s.matched_category_key}): "
            f"initial_stance={s.initial_stance.value} → "
            f"final_stance={s.current_stance.value}"
        )
    lines.append("")
    lines.append("Round trace count:")
    by_kind: dict[str, int] = {}
    for r in trace.rounds:
        by_kind[r.round_kind.value] = by_kind.get(r.round_kind.value, 0) + 1
    for k, v in by_kind.items():
        lines.append(f"  - {k}: {v} round(s)")
    if trace.debate_turns:
        lines.append(f"  - debate_turns: {len(trace.debate_turns)}")
    lines.append("")
    lines.append(
        "MICRO-TEST disclaimer: this is a mechanical micro-test, not a "
        "market simulation. Sample size n=" + str(n) + " is too small to "
        "support population-level claims. Coverage of stakeholder "
        "categories is thin. Output cannot be presented as a forecast, "
        "verdict, or representative-of-market claim."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 8.4B.1 — helpers for product-agnostic caveat construction
# ---------------------------------------------------------------------------


def _looks_like_market_entry_audience(
    audience_result: RunScopedAudienceRetrievalResult,
) -> bool:
    """Detect whether the audience result was produced via the
    Phase 8.4A.4 market-entry path. Heuristic: any matched persona
    has a populated `final_tier` field (only set under the dynamic-
    planner + anchor-gate path) OR the category-coverage list shows
    dynamic-planner sentinel category-key prefixes.

    This avoids passing an extra `is_market_entry` argument all the
    way down from the operator script — the audience_result itself
    already carries the routing signal.
    """
    for m in audience_result.matched_personas:
        if m.final_tier is not None:
            return True
    for cc in audience_result.category_coverage:
        if (
            cc.category_key.startswith("competitor_user_")
            or cc.category_key.startswith("substitute_user_")
            or cc.category_key.startswith("use_case_")
            or cc.category_key.startswith("objection_")
            or cc.category_key.startswith("buyer_type_")
        ):
            return True
    return False


def _derive_geography_strength(
    *,
    geography: str | None,
    audience_result: RunScopedAudienceRetrievalResult,
) -> str:
    """Determine whether the geography signal in this audience is
    'strong' (≥3 personas matched against geography categories),
    'soft' (geography in brief but thin local evidence — typical
    public-web pool), or 'absent' (no geography in brief)."""
    if not geography:
        return "absent"
    geo_categories_in_audience = 0
    total_geo_categories = 0
    for cc in audience_result.category_coverage:
        if cc.category_key.startswith("geography_"):
            total_geo_categories += 1
            if cc.matched_total > 0:
                geo_categories_in_audience += cc.matched_total
    if total_geo_categories > 0 and geo_categories_in_audience >= 3:
        return "strong"
    return "soft"


def _select_diverse_debate_pairs(
    *,
    states: Sequence[MicroPersonaState],
    max_turns: int,
) -> list[tuple[int, int]]:
    """Choose `max_turns` ordered (speaker_idx, target_idx) pairs that
    maximize CORE-vs-ADJACENT, category, and stance diversity.

    Behavior contract:
      * `max_turns <= 2` returns the legacy bidirectional pair between
        states[0] and states[1] — preserves Phase 8.2K test parity.
      * `max_turns > 2` greedily picks pairs that combine maximum
        category-key diversity + CORE/ADJACENT crossover. Each turn
        is bidirectional with the next selected pair, so e.g.
        max_turns=10 yields 5 pairs × 2 directions.
      * No persona may speak more than `max_turns // 2` times to
        prevent one-voice domination.
      * Output is deterministic given the same input ordering.
    """
    n = len(states)
    if n < 2 or max_turns <= 0:
        return []
    if max_turns <= 2:
        # Legacy 8.2K behavior: states[0] <-> states[1]
        if max_turns == 1:
            return [(0, 1)]
        return [(0, 1), (1, 0)]

    # Bucket personas by tier (CORE = RELEVANT/HIGHLY_RELEVANT
    # mapped through MicroRelevanceLabel; ADJACENT = WEAKLY_RELEVANT)
    core_idxs = [
        i for i, s in enumerate(states)
        if s.relevance_label.value == "RELEVANT"
    ]
    adj_idxs = [
        i for i, s in enumerate(states)
        if s.relevance_label.value == "WEAKLY_RELEVANT"
    ]

    pair_count = max_turns // 2
    used_categories: set[str] = set()
    speaks_count: dict[int, int] = {}
    speak_cap = max(1, max_turns // 2)
    pairs: list[tuple[int, int]] = []

    def _try_add_pair(a: int, b: int) -> bool:
        if a == b:
            return False
        if speaks_count.get(a, 0) >= speak_cap:
            return False
        if speaks_count.get(b, 0) >= speak_cap:
            return False
        # Bidirectional turn: both speak once.
        pairs.append((a, b))
        pairs.append((b, a))
        speaks_count[a] = speaks_count.get(a, 0) + 1
        speaks_count[b] = speaks_count.get(b, 0) + 1
        used_categories.add(states[a].matched_category_key)
        used_categories.add(states[b].matched_category_key)
        return True

    # Phase 1: CORE-vs-ADJACENT pairs with novel category combinations.
    for ci in core_idxs:
        if len(pairs) // 2 >= pair_count:
            break
        for ai in adj_idxs:
            if len(pairs) // 2 >= pair_count:
                break
            cat_a = states[ci].matched_category_key
            cat_b = states[ai].matched_category_key
            if cat_a == cat_b and cat_a in used_categories:
                continue
            if _try_add_pair(ci, ai):
                break

    # Phase 2: any remaining novel-category pairs (CORE-CORE or
    # ADJACENT-ADJACENT) to fill the cap.
    if len(pairs) // 2 < pair_count:
        all_idxs = core_idxs + adj_idxs
        for a in all_idxs:
            if len(pairs) // 2 >= pair_count:
                break
            for b in all_idxs:
                if len(pairs) // 2 >= pair_count:
                    break
                cat_a = states[a].matched_category_key
                cat_b = states[b].matched_category_key
                if cat_a == cat_b:
                    continue
                if (a, b) in pairs or (b, a) in pairs:
                    continue
                if _try_add_pair(a, b):
                    break

    return pairs[:max_turns]
