"""Phase 9E — intent + society-wide debate report renderer.

Founder-facing JSON + markdown views with mandatory caveats.
Pure formatter — no LLM calls, no DB writes.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any


_DEFAULT_HEADER_PERSONA_COUNT = 66


def _build_header_caveat(persona_count: int) -> str:
    return (
        "_This is a synthetic discussion simulation augmented with "
        "simulated-intent labels and a deterministic cross-cohort "
        f"argument propagation pass. n={persona_count} run-scoped "
        "society. Simulated intent is NOT a real-world purchase "
        "forecast. The product is unlaunched — no persona has "
        "actually used it._"
    )


_HEADER_CAVEAT = _build_header_caveat(_DEFAULT_HEADER_PERSONA_COUNT)


def render_intent_and_debate_report_json(
    *,
    run_scope_id: str,
    phase: str,
    product_name: str,
    persona_count: int,
    cohort_count: int,
    intents: list[dict[str, Any]],
    intent_rollup: dict[str, Any],
    arguments: list[dict[str, Any]],
    propagations: list[dict[str, Any]],
    cohort_id_to_label: dict[str, str],
    cohort_id_to_size: dict[str, int],
    quality_scores: dict[str, Any],
    forbidden_audit: dict[str, Any],
    sensitive_audit: dict[str, Any],
) -> dict[str, Any]:
    intent_dist = intent_rollup.get("intent_distribution") or {}
    switching_dist = intent_rollup.get("switching_status_distribution") or {}
    high_intent = intent_rollup.get("high_intent_segments") or []
    rejection = intent_rollup.get("strongest_rejection_segments") or []

    # Buy-now / try-once / waitlist signals
    buy_now = [
        h for h in high_intent if h["intent"] == "would_buy_now"
    ]
    try_once = [
        h for h in high_intent if h["intent"] == "would_try_once"
    ]
    waitlist = [
        h for h in high_intent if h["intent"] == "would_join_waitlist"
    ]
    consider = [
        i for i in intents
        if i.get("simulated_intent") == "would_consider_if_proven"
    ]
    loyal_or_reject = [
        i for i in intents
        if i.get("simulated_intent") in (
            "would_reject", "would_block",
            "loyal_to_current_alternative",
        )
    ]
    switching_barriers = [
        i for i in intents
        if i.get("switching_status") in (
            "loyal_to_current_alternative", "refuses_switching",
        )
    ]
    conditions_to_buy = []
    for i in intents:
        for cond in (i.get("conditions_to_buy") or []):
            if isinstance(cond, str) and cond:
                conditions_to_buy.append({
                    "persona_id": i["persona_id"],
                    "intent": i.get("simulated_intent"),
                    "condition": cond,
                })

    # Argument propagation summary
    response_counter = Counter(p.get("response_type") for p in propagations)
    effect_counter = Counter(p.get("effect_on_intent") for p in propagations)
    args_by_type = Counter(a.get("argument_type") for a in arguments)
    # Find arguments that "spread" (adopted/intensified in target cohorts)
    spreads_by_arg: dict[str, int] = {}
    resists_by_arg: dict[str, int] = {}
    for p in propagations:
        aid = p.get("argument_id")
        if not aid:
            continue
        if p.get("response_type") in ("adopted", "intensified"):
            spreads_by_arg[aid] = spreads_by_arg.get(aid, 0) + 1
        elif p.get("response_type") == "resisted":
            resists_by_arg[aid] = resists_by_arg.get(aid, 0) + 1
    # Top arguments
    arg_lookup = {a["id"]: a for a in arguments if a.get("id")}
    top_spread = sorted(
        spreads_by_arg.items(), key=lambda kv: -kv[1],
    )[:6]
    top_resist = sorted(
        resists_by_arg.items(), key=lambda kv: -kv[1],
    )[:6]
    arguments_that_spread = []
    for aid, n in top_spread:
        a = arg_lookup.get(aid) or {}
        arguments_that_spread.append({
            "argument_id": aid,
            "argument_type": a.get("argument_type"),
            "source_cohort_label": cohort_id_to_label.get(
                a.get("source_cohort_id"), None,
            ),
            "argument_text": (a.get("argument_text") or "")[:280],
            "cohorts_adopting": n,
        })
    arguments_that_were_resisted = []
    for aid, n in top_resist:
        a = arg_lookup.get(aid) or {}
        arguments_that_were_resisted.append({
            "argument_id": aid,
            "argument_type": a.get("argument_type"),
            "source_cohort_label": cohort_id_to_label.get(
                a.get("source_cohort_id"), None,
            ),
            "argument_text": (a.get("argument_text") or "")[:280],
            "cohorts_resisting": n,
        })

    # Cohort persuasion / resistance maps
    persuaded_by_cohort: dict[str, int] = {}
    resistant_by_cohort: dict[str, int] = {}
    for p in propagations:
        cid = p.get("target_cohort_id")
        if not cid:
            continue
        if p.get("response_type") in ("adopted", "intensified"):
            persuaded_by_cohort[cid] = persuaded_by_cohort.get(cid, 0) + 1
        elif p.get("response_type") == "resisted":
            resistant_by_cohort[cid] = resistant_by_cohort.get(cid, 0) + 1
    cohorts_most_persuaded = sorted(
        persuaded_by_cohort.items(), key=lambda kv: -kv[1],
    )[:5]
    cohorts_most_resistant = sorted(
        resistant_by_cohort.items(), key=lambda kv: -kv[1],
    )[:5]

    return {
        "schema_version": "9E.v1",
        "run_scope_id": run_scope_id,
        "phase": phase,
        "product_name": product_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "header_caveat": _build_header_caveat(persona_count),
        "executive_summary": _exec_summary(
            persona_count, cohort_count, intent_dist,
            switching_dist, response_counter, effect_counter,
            quality_scores,
        ),
        "synthetic_intent_snapshot": {
            "persona_count": persona_count,
            "cohort_count": cohort_count,
            "intent_distribution": intent_dist,
            "switching_status_distribution": switching_dist,
        },
        "buy_now_or_try_once_signals": {
            "would_buy_now_count": len(buy_now),
            "would_try_once_count": len(try_once),
            "would_join_waitlist_count": len(waitlist),
            "buy_now_personas": buy_now[:30],
            "try_once_personas": try_once[:30],
            "waitlist_personas": waitlist[:30],
        },
        "consider_if_proven_signals": {
            "count": len(consider),
            "examples": [
                {
                    "persona_id": c["persona_id"],
                    "intent_strength": c.get("intent_strength"),
                    "confidence": c.get("confidence"),
                    "switching_status": c.get("switching_status"),
                    "proof_needed": c.get("proof_needed") or [],
                }
                for c in consider[:30]
            ],
        },
        "loyal_or_reject_signals": {
            "count": len(loyal_or_reject),
            "examples": rejection[:30],
        },
        "switching_barriers": {
            "count": len(switching_barriers),
            "examples": [
                {
                    "persona_id": s["persona_id"],
                    "current_alternative": s.get("current_alternative"),
                    "switching_status": s.get("switching_status"),
                    "reason_for_rejection": s.get("reason_for_rejection"),
                }
                for s in switching_barriers[:30]
            ],
        },
        "conditions_to_buy": conditions_to_buy[:30],
        "society_wide_debate_setup": {
            "cohort_count": cohort_count,
            "argument_count": len(arguments),
            "argument_type_distribution": dict(args_by_type),
            "propagation_count": len(propagations),
            "response_type_distribution": dict(response_counter),
            "effect_on_intent_distribution": dict(effect_counter),
        },
        "arguments_that_spread": arguments_that_spread,
        "arguments_that_were_resisted": arguments_that_were_resisted,
        "cohorts_most_persuaded": [
            {
                "cohort_id": cid,
                "cohort_label": cohort_id_to_label.get(cid, cid[:8]),
                "adopted_or_intensified_count": n,
            }
            for cid, n in cohorts_most_persuaded
        ],
        "cohorts_most_resistant": [
            {
                "cohort_id": cid,
                "cohort_label": cohort_id_to_label.get(cid, cid[:8]),
                "resisted_count": n,
            }
            for cid, n in cohorts_most_resistant
        ],
        "intent_by_cohort": intent_rollup.get("intent_by_cohort") or {},
        "public_vs_private_shift_summary": (
            "See Phase 9B/9B.1 report for the per-persona public/private "
            "ballot deltas; Phase 9E does not regenerate them."
        ),
        "founder_implications": _founder_implications(
            intent_dist, persuaded_by_cohort, resistant_by_cohort,
            cohort_id_to_label,
        ),
        "recommended_next_tests": _recommended_next_tests(
            intent_dist, conditions_to_buy, loyal_or_reject,
        ),
        "caveats": [
            "Simulated intent is NOT a real-world purchase forecast.",
            "Cohorts are run-scoped; not transferable to other briefs.",
            (
                f"Synthetic n={persona_count} simulation. Not a "
                "launch verdict."
            ),
            "Personas have not bought, used, owned, or reviewed the unlaunched product.",
            "Intent inference is rule-based + deterministic; "
            "psychology trait values are simulation controls, not real "
            "psychological diagnoses.",
        ],
        "appendix": {
            "forbidden_claim_audit": forbidden_audit,
            "sensitive_inference_audit": sensitive_audit,
            "quality_scores": quality_scores,
        },
    }


def _exec_summary(
    persona_count: int, cohort_count: int,
    intent_dist: dict[str, Any], switching_dist: dict[str, Any],
    response_counter, effect_counter, quality: dict[str, Any],
) -> list[str]:
    return [
        f"{persona_count} run-scoped personas across {cohort_count} "
        "cohorts received exactly one synthetic-intent record each.",
        f"Intent distribution: {dict(intent_dist)}.",
        f"Switching status distribution: {dict(switching_dist)}.",
        f"Cross-cohort argument propagation: response types "
        f"{dict(response_counter)}; effects on intent {dict(effect_counter)}.",
        f"Quality aggregate: {quality.get('aggregate_score')} "
        f"({quality.get('ready_state')}).",
        "These are simulated intents inside the synthetic run, not "
        "real-world purchase forecasts.",
    ]


def _founder_implications(
    intent_dist: dict[str, Any],
    persuaded_by_cohort: dict[str, int],
    resistant_by_cohort: dict[str, int],
    cohort_id_to_label: dict[str, str],
) -> list[str]:
    out: list[str] = []
    n_consider = intent_dist.get("would_consider_if_proven", 0)
    n_loyal = intent_dist.get("loyal_to_current_alternative", 0)
    n_reject = intent_dist.get("would_reject", 0) + intent_dist.get(
        "would_block", 0,
    )
    n_buy = intent_dist.get("would_buy_now", 0) + intent_dist.get(
        "would_try_once", 0,
    ) + intent_dist.get("would_join_waitlist", 0)
    out.append(
        f"In this synthetic society, {n_buy} persona(s) expressed "
        f"would_buy_now / would_try_once / would_join_waitlist — but "
        "this is not a market forecast."
    )
    out.append(
        f"{n_consider} persona(s) expressed would_consider_if_proven — "
        "the dominant gating condition is proof. The most-mentioned "
        "proof needs in the rollup are the highest-leverage concept-test "
        "targets."
    )
    if n_loyal + n_reject > 0:
        out.append(
            f"{n_loyal + n_reject} persona(s) expressed loyalty to "
            "current alternatives or outright rejection. The cohorts "
            "with the highest resistance to cross-cohort arguments "
            "warrant a real-people validation pass before scaling spend."
        )
    if persuaded_by_cohort:
        top_persuaded = max(
            persuaded_by_cohort.items(), key=lambda kv: kv[1],
        )
        out.append(
            f"Most argument-receptive cohort: "
            f"`{cohort_id_to_label.get(top_persuaded[0], top_persuaded[0][:8])}` "
            f"({top_persuaded[1]} arguments adopted/intensified)."
        )
    if resistant_by_cohort:
        top_resistant = max(
            resistant_by_cohort.items(), key=lambda kv: kv[1],
        )
        out.append(
            f"Most argument-resistant cohort: "
            f"`{cohort_id_to_label.get(top_resistant[0], top_resistant[0][:8])}` "
            f"({top_resistant[1]} arguments resisted)."
        )
    return out


def _recommended_next_tests(
    intent_dist: dict[str, Any],
    conditions_to_buy: list[dict[str, Any]],
    loyal_or_reject: list[dict[str, Any]],
) -> list[str]:
    out = []
    if conditions_to_buy:
        out.append(
            f"Build the smallest concept tests that satisfy the top "
            f"`conditions_to_buy` listed in this report ({len(conditions_to_buy)} entries) "
            "before any paid distribution."
        )
    if loyal_or_reject:
        out.append(
            f"Run a small real-people discussion with profiles matching "
            f"the {len(loyal_or_reject)} loyal/rejecting personas — "
            "their dissent is the contrarian signal worth validating."
        )
    out.append(
        "Treat the cohorts most receptive to arguments as a testable "
        "hypothesis, not a proven segment. Validate with real prospects "
        "matching that cohort's profile."
    )
    out.append(
        "Do NOT promote any synthetic intent number to a market "
        "forecast. Synthetic intent is a hypothesis-generator, not a "
        "demand predictor."
    )
    return out


def render_intent_and_debate_report_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"# {report['product_name']} — Intent & Society-Wide Debate "
        f"Report (Phase {report['phase']})"
    )
    lines.append("")
    lines.append(f"**Run scope:** `{report['run_scope_id']}`")
    lines.append(f"**Generated at:** {report['generated_at']}")
    lines.append("")
    lines.append(f"> {report['header_caveat']}")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    for b in report["executive_summary"]:
        lines.append(f"- {b}")
    lines.append("")
    snap = report["synthetic_intent_snapshot"]
    lines.append("## 2. Synthetic Intent Snapshot")
    lines.append(
        f"- persona_count: {snap['persona_count']}\n"
        f"- cohort_count: {snap['cohort_count']}\n"
        f"- intent_distribution: `{snap['intent_distribution']}`\n"
        f"- switching_status_distribution: "
        f"`{snap['switching_status_distribution']}`"
    )
    lines.append("")
    bn = report["buy_now_or_try_once_signals"]
    lines.append("## 3. Buy-Now / Try-Once Signals")
    lines.append(
        f"- would_buy_now: {bn['would_buy_now_count']}\n"
        f"- would_try_once: {bn['would_try_once_count']}\n"
        f"- would_join_waitlist: {bn['would_join_waitlist_count']}"
    )
    lines.append("")
    cs = report["consider_if_proven_signals"]
    lines.append("## 4. Consider-if-Proven Signals")
    lines.append(f"- count: {cs['count']}")
    if cs["examples"]:
        for ex in cs["examples"][:10]:
            proof = ", ".join(ex.get("proof_needed") or [])[:160]
            lines.append(
                f"  - `{ex['persona_id'][:8]}` "
                f"(strength={ex.get('intent_strength')}, "
                f"confidence={ex.get('confidence')}): proof_needed={proof}"
            )
    lines.append("")
    lr = report["loyal_or_reject_signals"]
    lines.append("## 5. Loyal-to-Alternative / Reject Signals")
    lines.append(f"- count: {lr['count']}")
    for ex in lr["examples"][:10]:
        lines.append(
            f"  - `{ex['persona_id'][:8]}` intent=`{ex['intent']}` "
            f"strength={ex.get('strength')}"
        )
    lines.append("")
    sb = report["switching_barriers"]
    lines.append("## 6. Switching Barriers")
    lines.append(f"- count: {sb['count']}")
    for ex in sb["examples"][:10]:
        lines.append(
            f"  - `{ex['persona_id'][:8]}` "
            f"current_alternative=`{ex.get('current_alternative')}` "
            f"switching_status=`{ex.get('switching_status')}`"
        )
    lines.append("")
    lines.append("## 7. Conditions to Buy")
    lines.append("")
    for c in report["conditions_to_buy"][:20]:
        lines.append(
            f"- `{c['persona_id'][:8]}` ({c['intent']}): "
            f"{c['condition'][:200]}"
        )
    lines.append("")
    debate = report["society_wide_debate_setup"]
    lines.append("## 8. Society-Wide Debate Setup")
    lines.append(
        f"- cohort_count: {debate['cohort_count']}\n"
        f"- argument_count: {debate['argument_count']}\n"
        f"- argument_type_distribution: "
        f"`{debate['argument_type_distribution']}`\n"
        f"- propagation_count: {debate['propagation_count']}\n"
        f"- response_type_distribution: "
        f"`{debate['response_type_distribution']}`\n"
        f"- effect_on_intent_distribution: "
        f"`{debate['effect_on_intent_distribution']}`"
    )
    lines.append("")
    lines.append("## 9. Arguments That Spread")
    lines.append("")
    for a in report["arguments_that_spread"]:
        lines.append(
            f"- `{a['argument_type']}` from "
            f"`{a.get('source_cohort_label')}` — adopted/intensified by "
            f"{a.get('cohorts_adopting')} cohort(s): {a['argument_text'][:200]}"
        )
    lines.append("")
    lines.append("## 10. Arguments That Were Resisted")
    lines.append("")
    for a in report["arguments_that_were_resisted"]:
        lines.append(
            f"- `{a['argument_type']}` from "
            f"`{a.get('source_cohort_label')}` — resisted by "
            f"{a.get('cohorts_resisting')} cohort(s): {a['argument_text'][:200]}"
        )
    lines.append("")
    lines.append("## 11. Cohorts Most Persuaded")
    lines.append("")
    for c in report["cohorts_most_persuaded"]:
        lines.append(
            f"- `{c['cohort_label']}` ← "
            f"{c['adopted_or_intensified_count']} arguments "
            "adopted/intensified"
        )
    lines.append("")
    lines.append("## 12. Cohorts Most Resistant")
    lines.append("")
    for c in report["cohorts_most_resistant"]:
        lines.append(
            f"- `{c['cohort_label']}` ← {c['resisted_count']} arguments "
            "resisted"
        )
    lines.append("")
    lines.append("## 13. Intent by Cohort")
    lines.append("")
    for cohort_label, ibc in (report["intent_by_cohort"] or {}).items():
        lines.append(
            f"- **{cohort_label}**: "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(ibc.items(), key=lambda kv: -kv[1])
            )
        )
    lines.append("")
    lines.append("## 14. Public vs Private Shift Summary")
    lines.append("")
    lines.append(report["public_vs_private_shift_summary"])
    lines.append("")
    lines.append("## 15. Founder Implications")
    lines.append("")
    for b in report["founder_implications"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 16. Recommended Next Tests")
    lines.append("")
    for b in report["recommended_next_tests"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 17. Caveats")
    lines.append("")
    for c in report["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("## 18. Appendix")
    lines.append("")
    appx = report["appendix"]
    lines.append(f"- forbidden_claim_audit: `{appx['forbidden_claim_audit']}`")
    lines.append(
        f"- sensitive_inference_audit: `{appx['sensitive_inference_audit']}`"
    )
    lines.append(f"- quality_scores: `{appx['quality_scores']}`")
    lines.append("")
    return "\n".join(lines)
