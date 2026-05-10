"""Phase 9D — cohort architecture founder/operator report renderer.

Pure formatter — no LLM calls, no DB writes. Emits both JSON and
markdown views with mandatory caveats.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


_HEADER_CAVEAT = (
    "_This is a synthetic discussion simulation summarized into "
    "run-scoped cohorts. n=66 persisted society. Not representative of "
    "the California market. Not a forecast. Not a launch verdict. The "
    "product is unlaunched — no persona has actually used it._"
)


def render_cohort_report_json(
    *,
    run_scope_id: str,
    phase: str,
    product_name: str,
    cohorts: list[dict[str, Any]],
    rollup: dict[str, Any],
    quality_scores: dict[str, Any],
    persona_count: int,
    forbidden_audit: dict[str, Any],
    sensitive_audit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "9D.v1",
        "run_scope_id": run_scope_id,
        "phase": phase,
        "product_name": product_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "header_caveat": _HEADER_CAVEAT,
        "executive_summary": _exec_summary(
            cohorts, rollup, quality_scores, persona_count,
        ),
        "why_cohorts_are_needed": [
            (
                "Per-persona LLM simulation cost grows linearly with "
                "society size. A 66-person society fit one full "
                "discussion run, but n=300/1000/100k cannot."
            ),
            (
                "Cohorts compress similar personas into traceable, "
                "weighted summaries. Each cohort retains pointers back "
                "to its members, source records, discussion turns, and "
                "memory atoms."
            ),
            (
                "Cohorts are NOT global market segments. They are "
                "run-scoped, brief-scoped artifacts of THIS particular "
                "society's discussion. They do not transfer."
            ),
        ],
        "input_society_summary": {
            "persona_count": persona_count,
            "cohort_count": len(cohorts),
        },
        "cohort_map": cohorts,
        "weighted_society_rollup": rollup,
        "what_this_preserves_from_the_66_person_discussion": [
            "Final-stance distribution (pre + final).",
            "Objection bucket frequency.",
            "Proof-need bucket frequency.",
            "Social-influence classification (resistance / no_change / "
            "private_acceptance / etc.).",
            "Per-cohort psychology means + standard deviations.",
            "Pointers to specific discussion turns + memory atoms + "
            "personas (traceability).",
        ],
        "what_it_loses_compared_to_full_individual_simulation": [
            "Individual persona voice on a per-stance basis (the "
            "report only highlights cohort-level patterns).",
            "Cross-cohort discussion dynamics (cohorts are summarized, "
            "not re-debated against each other in this phase).",
            "Per-persona psychology trajectories within a single "
            "discussion round.",
            "The cost of seeing every individual reflection ballot "
            "verbatim — operators should still browse the appendix "
            "for any cohort that matters most.",
        ],
        "how_this_scales_to_100_or_1000_or_100k": [
            "n=100: same architecture; expect 12-15 cohorts.",
            "n=1,000: clustering becomes a hierarchical tree; primary "
            "cohorts at 12-15, sub-cohorts when a primary cohort exceeds "
            "size 50.",
            "n=100,000: cohorts at multiple resolutions (segments → "
            "cohorts → sub-cohorts). Discussion runs only on cohort "
            "representatives. Memory atoms become per-cohort, not "
            "per-persona.",
            "Cost scaling: discussion-LLM cost becomes O(cohort_count × "
            "rounds), not O(persona_count × rounds).",
        ],
        "founder_implications": _founder_implications(rollup),
        "caveats": [
            "Synthetic n=66 simulation. Not a forecast. Not a launch verdict.",
            "Cohorts are run-scoped + brief-scoped — never global market segments.",
            "Cohort claims trace back to real persona/source/turn/atom IDs. "
            "Quality gates verify this.",
            "Psychology values are simulation controls, not real "
            "psychological diagnoses.",
            "The product is unlaunched. No persona has bought, used, "
            "owned, or reviewed it.",
        ],
        "appendix": {
            "forbidden_claim_audit": forbidden_audit,
            "sensitive_inference_audit": sensitive_audit,
            "quality_scores": quality_scores,
        },
    }


def _exec_summary(
    cohorts: list[dict[str, Any]],
    rollup: dict[str, Any],
    quality: dict[str, Any],
    persona_count: int,
) -> list[str]:
    bullets = [
        f"{persona_count} run-scoped personas compressed into "
        f"{len(cohorts)} cohorts via deterministic agglomerative "
        "clustering on a feature vector built from role + evidence + "
        "psychology + discussion behavior + memory signals.",
        f"Weighted final-stance distribution: "
        f"{rollup.get('weighted_stance_distribution')}.",
        f"Top three weighted objection buckets: "
        f"{list(rollup.get('weighted_objection_summary', {}).keys())[:3]}.",
        f"Top three weighted proof-need buckets: "
        f"{list(rollup.get('weighted_proof_need_summary', {}).keys())[:3]}.",
        f"Resistance-bearing cohorts: "
        f"{(rollup.get('resistance_summary') or {}).get('cohorts_with_resistance')}",
        f"Cohort-architecture quality aggregate: "
        f"{quality.get('aggregate_score')} "
        f"({quality.get('ready_state')}).",
    ]
    return bullets


def _founder_implications(rollup: dict[str, Any]) -> list[str]:
    out: list[str] = []
    obj = rollup.get("weighted_objection_summary") or {}
    if obj:
        top_obj = next(iter(obj))
        out.append(
            f"Most weighted objection bucket: `{top_obj}`. Concept tests "
            "should target this concern first."
        )
    proof = rollup.get("weighted_proof_need_summary") or {}
    if proof:
        top_proof = next(iter(proof))
        out.append(
            f"Most weighted proof-need bucket: `{top_proof}`. Build a "
            "small concept test that delivers exactly this proof "
            "artifact, then run a real-people discussion to validate."
        )
    sis = rollup.get("social_influence_summary") or {}
    resistance = sis.get("resistance", 0)
    if resistance > 0:
        out.append(
            f"Resistance signal present in the rollup ({resistance:.2f}). "
            "Talk to real prospects matching the resisting cohorts' "
            "profile — their dissent is the contrarian signal worth "
            "validating before scaling spend."
        )
    out.append(
        "Treat this rollup as input to a small real-people discussion "
        "before treating any signal as load-bearing for a launch decision."
    )
    return out


def render_cohort_report_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"# {report['product_name']} — Cohort Architecture Report "
        f"(Phase {report['phase']})"
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
    lines.append("## 2. Why Cohorts Are Needed")
    lines.append("")
    for b in report["why_cohorts_are_needed"]:
        lines.append(f"- {b}")
    lines.append("")
    setup = report["input_society_summary"]
    lines.append("## 3. Input Society Summary")
    lines.append(
        f"- persona_count: {setup['persona_count']}\n"
        f"- cohort_count: {setup['cohort_count']}"
    )
    lines.append("")
    lines.append("## 4. Cohort Map")
    lines.append("")
    lines.append(
        "| # | Label | Size | Weight | Top role | Top stance | "
        "Representative |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(report["cohort_map"]):
        roles = c.get("role_distribution") or {}
        stance = c.get("stance_distribution") or {}
        top_role = next(iter(sorted(
            roles.items(), key=lambda kv: -kv[1],
        )), ("?", 0))[0]
        top_stance = next(iter(sorted(
            stance.items(), key=lambda kv: -kv[1],
        )), ("?", 0))[0]
        rep = c.get("representative_persona_display_name") or c.get(
            "representative_persona_id", "?",
        )
        lines.append(
            f"| {i + 1} | `{c.get('cohort_label')}` | {c.get('cohort_size')} | "
            f"{c.get('cohort_weight'):.3f} | `{top_role}` | "
            f"`{top_stance}` | {rep} |"
        )
    lines.append("")
    lines.append("## 5. Cohort Psychology Summary")
    lines.append("")
    for i, c in enumerate(report["cohort_map"]):
        psy = c.get("psychology_summary") or {}
        if not psy:
            continue
        extreme = sorted(
            psy.items(),
            key=lambda kv: -abs(float(kv[1].get("mean", 0.5)) - 0.5),
        )[:4]
        lines.append(
            f"- **Cohort {i + 1}** (`{c.get('cohort_label')}`): "
            + ", ".join(
                f"{name}={summary['label']}({summary['mean']})"
                for name, summary in extreme
            )
        )
    lines.append("")
    lines.append("## 6. Cohort Objection Map")
    lines.append("")
    for i, c in enumerate(report["cohort_map"]):
        obj = (c.get("objection_summary") or {}).get("top_buckets") or []
        if obj:
            lines.append(
                f"- **Cohort {i + 1}**: {', '.join(f'`{b}`' for b in obj[:5])}"
            )
    lines.append("")
    lines.append("## 7. Cohort Proof Demand Map")
    lines.append("")
    for i, c in enumerate(report["cohort_map"]):
        proof = (c.get("proof_need_summary") or {}).get("top_buckets") or []
        if proof:
            lines.append(
                f"- **Cohort {i + 1}**: "
                + ", ".join(f"`{b}`" for b in proof[:5])
            )
    lines.append("")
    lines.append("## 8. Social Influence / Resistance Map")
    lines.append("")
    for i, c in enumerate(report["cohort_map"]):
        d = (c.get("discussion_behavior_summary") or {}).get(
            "public_private_delta_distribution"
        ) or {}
        if d:
            lines.append(
                f"- **Cohort {i + 1}**: "
                + ", ".join(f"`{k}`={v}" for k, v in sorted(
                    d.items(), key=lambda kv: -kv[1],
                ))
            )
    lines.append("")
    lines.append("## 9. Representative Personas")
    lines.append("")
    for i, c in enumerate(report["cohort_map"]):
        reps = c.get("representatives") or {}
        primary = reps.get("primary_display_name") or reps.get("primary")
        dissent = reps.get("dissent_display_name") or reps.get("dissent")
        proof = reps.get("proof_threshold_display_name") or reps.get("proof_threshold")
        lines.append(
            f"- **Cohort {i + 1}** (`{c.get('cohort_label')}`): "
            f"primary={primary}, dissent={dissent}, proof_threshold={proof}"
        )
    lines.append("")
    lines.append("## 10. Weighted Society Rollup")
    lines.append("")
    rollup = report["weighted_society_rollup"]
    lines.append(
        f"- weighted_stance_distribution: `{rollup.get('weighted_stance_distribution')}`"
    )
    lines.append(
        f"- weighted_objection_summary: `{rollup.get('weighted_objection_summary')}`"
    )
    lines.append(
        f"- weighted_proof_need_summary: `{rollup.get('weighted_proof_need_summary')}`"
    )
    lines.append(
        f"- social_influence_summary: `{rollup.get('social_influence_summary')}`"
    )
    lines.append(
        f"- resistance_summary: `{rollup.get('resistance_summary')}`"
    )
    lines.append(
        f"- uncertainty_summary: `{rollup.get('uncertainty_summary')}`"
    )
    lines.append("")
    lines.append("## 11. What This Preserves From the 66-Person Discussion")
    lines.append("")
    for b in report["what_this_preserves_from_the_66_person_discussion"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 12. What It Loses Compared to Full Individual Simulation")
    lines.append("")
    for b in report["what_it_loses_compared_to_full_individual_simulation"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 13. How This Scales to 100 / 1,000 / 100k Personas")
    lines.append("")
    for b in report["how_this_scales_to_100_or_1000_or_100k"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 14. Founder Implications")
    lines.append("")
    for b in report["founder_implications"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 15. Caveats")
    lines.append("")
    for c in report["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("## 16. Appendix")
    lines.append("")
    appx = report["appendix"]
    lines.append(f"- forbidden_claim_audit: `{appx['forbidden_claim_audit']}`")
    lines.append(
        f"- sensitive_inference_audit: `{appx['sensitive_inference_audit']}`"
    )
    lines.append(f"- quality_scores: `{appx['quality_scores']}`")
    lines.append("")
    return "\n".join(lines)
