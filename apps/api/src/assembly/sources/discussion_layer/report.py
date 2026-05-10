"""Phase 9A.4 — discussion report renderer (founder-facing).

Pure formatter — no LLM calls, no DB writes. Takes the structured
discussion artifacts and emits both JSON and markdown views with the
mandatory caveats baked in.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any


_HEADER_CAVEAT = (
    "_This is a synthetic discussion simulation. n=30 persisted society, "
    "5 groups of 6 personas. Not representative of the California market. "
    "Not a forecast. Not a demand forecast. Not a launch verdict. The "
    "product is unlaunched — no persona has actually used it._"
)


def _persona_name_map(personas: list[dict[str, Any]]) -> dict[str, str]:
    return {
        p["persona_id"]: p.get("display_name") or p["persona_id"][:8]
        for p in personas
    }


def render_discussion_report_json(
    *,
    run_scope_id: str,
    discussion_session_id: str,
    product_name: str,
    launch_state: str,
    personas: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    pre_ballots: list[dict[str, Any]],
    reflection_ballots: list[dict[str, Any]],
    final_ballots: list[dict[str, Any]],
    memory_atom_count: int,
    memory_atoms_by_type: dict[str, int],
    overcooperation: dict[str, Any],
    social_influence_classification: dict[str, int],
    quality_scores: dict[str, Any],
    forbidden_audit: dict[str, Any],
    sensitive_audit: dict[str, Any],
) -> dict[str, Any]:
    name_of = _persona_name_map(personas)
    pre_stances = Counter(b["private_stance"] for b in pre_ballots)
    final_stances = Counter(b["private_stance"] for b in final_ballots)
    public_opening = [
        t for t in turns if t.get("turn_type") == "public_opening"
    ]
    public_opening_summary = [
        {
            "speaker": name_of.get(t["speaker_persona_id"], "?"),
            "stance": t.get("stance"),
            "text": (t.get("public_text") or "")[:280],
        }
        for t in public_opening[:30]
    ]
    main_arguments = []
    for t in turns:
        if t.get("turn_type") == "challenge":
            main_arguments.append({
                "speaker": name_of.get(t["speaker_persona_id"], "?"),
                "argument": (t.get("public_text") or "")[:280],
            })
    proof_demands = []
    for b in pre_ballots:
        need = (b.get("top_proof_need") or "").strip()
        if need:
            proof_demands.append({
                "speaker": name_of.get(b["persona_id"], "?"),
                "proof_need": need[:200],
            })
    arguments_that_changed_minds: list[dict[str, Any]] = []
    arguments_that_failed: list[dict[str, Any]] = []
    pre_by_persona = {b["persona_id"]: b["private_stance"] for b in pre_ballots}
    for b in final_ballots:
        delta = b.get("public_private_delta") or "no_change"
        if delta == "private_acceptance":
            arguments_that_changed_minds.append({
                "persona": name_of.get(b["persona_id"], "?"),
                "from": pre_by_persona.get(b["persona_id"]),
                "to": b["private_stance"],
                "reasoning": (b.get("private_reasoning") or "")[:280],
            })
        if delta == "resistance":
            arguments_that_failed.append({
                "persona": name_of.get(b["persona_id"], "?"),
                "held_at": b["private_stance"],
                "reasoning": (b.get("private_reasoning") or "")[:280],
            })
    persona_clusters: dict[str, list[str]] = {}
    for b in final_ballots:
        persona_clusters.setdefault(
            b["private_stance"], []
        ).append(name_of.get(b["persona_id"], "?"))

    return {
        "schema_version": "9A.4.v1",
        "run_scope_id": run_scope_id,
        "discussion_session_id": discussion_session_id,
        "product_name": product_name,
        "launch_state": launch_state,
        "generated_at": datetime.now(UTC).isoformat(),
        "header_caveat": _HEADER_CAVEAT,
        "executive_summary": _exec_summary(
            personas, pre_ballots, final_ballots, overcooperation,
            quality_scores, social_influence_classification,
        ),
        "discussion_setup": {
            "persona_count": len(personas),
            "group_count": len(groups),
            "group_size": (
                len(groups[0]["persona_ids"]) if groups else 0
            ),
            "structure": (
                "0 pre-ballot · 1 public opening · 2 challenge · "
                "3 peer-response · 4 proof discussion · 5 reflection · "
                "6 final ballot"
            ),
        },
        "group_composition": [
            {
                "group_index": g["group_index"],
                "personas": [
                    name_of.get(pid, pid[:8]) for pid in g["persona_ids"]
                ],
                "metadata": g.get("metadata") or {},
            }
            for g in groups
        ],
        "public_opening_stances": public_opening_summary,
        "main_arguments_raised": main_arguments[:20],
        "main_objections_that_spread": _top_objections(turns)[:10],
        "arguments_that_changed_minds": arguments_that_changed_minds,
        "arguments_that_failed": arguments_that_failed,
        "public_vs_private_opinion_change": {
            "pre_stance_distribution": dict(pre_stances),
            "final_stance_distribution": dict(final_stances),
            "delta_classification": social_influence_classification,
        },
        "social_influence_analysis": {
            "delta_classification": social_influence_classification,
            "overcooperation_audit": overcooperation,
        },
        "persona_cluster_differences": persona_clusters,
        "proof_demands": proof_demands[:30],
        "positioning_implications": _positioning_implications(
            final_stances, arguments_that_changed_minds,
            arguments_that_failed,
        ),
        "what_founder_should_test_next": _what_to_test_next(
            arguments_that_failed, proof_demands,
        ),
        "caveats": [
            "This is a synthetic discussion simulation, not a real focus group.",
            "n=30 persisted society; 5 groups of 6.",
            "Not representative of the California market.",
            "Not a demand forecast.",
            "Not a launch verdict.",
            "Personas are run-scoped and brief-scoped. No global personas.",
            f"Product '{product_name}' is {launch_state}; no persona has "
            "actually used it.",
            "Psychology trait values are simulation controls, not real "
            "psychological diagnoses.",
        ],
        "appendix": {
            "discussion_turn_count": len(turns),
            "pre_ballot_count": len(pre_ballots),
            "reflection_ballot_count": len(reflection_ballots),
            "final_ballot_count": len(final_ballots),
            "memory_atom_count": memory_atom_count,
            "memory_atoms_by_type": memory_atoms_by_type,
            "forbidden_claim_audit": forbidden_audit,
            "sensitive_inference_audit": sensitive_audit,
            "discussion_quality_scores": quality_scores,
        },
    }


def _exec_summary(
    personas, pre_ballots, final_ballots, overcooperation,
    quality_scores, social_influence_classification,
):
    pre = Counter(b["private_stance"] for b in pre_ballots)
    final = Counter(b["private_stance"] for b in final_ballots)
    bullets = [
        f"This is a synthetic discussion simulation across {len(personas)} "
        f"run-scoped personas in 5 groups of 6.",
        f"Pre-discussion private stance distribution: "
        f"{dict(pre)}.",
        f"Post-discussion private stance distribution: "
        f"{dict(final)}.",
        f"Social-influence classification: {social_influence_classification}.",
    ]
    if overcooperation.get("flag"):
        bullets.append(
            "Over-cooperation detected — public discussion converged "
            "without private dissent. The discussion may be too "
            "agreeable for this society's psychology profile."
        )
    else:
        bullets.append(
            "No over-cooperation detected. Public/private distinction "
            "preserved."
        )
    bullets.append(
        f"Discussion quality aggregate: {quality_scores.get('aggregate_score')} "
        f"({quality_scores.get('ready_state')})."
    )
    return bullets


def _top_objections(turns):
    spread: list[dict[str, Any]] = []
    seen = Counter()
    for t in turns:
        text = (t.get("public_text") or "").lower()
        for kw in (
            "ip rating", "battery life", "durab", "expensive", "review",
            "proof", "cheaper", "lumens", "warrant", "weather",
        ):
            if kw in text:
                seen[kw] += 1
    for kw, n in seen.most_common(10):
        spread.append({"keyword": kw, "raised_in_turns": n})
    return spread


def _positioning_implications(
    final_stances: Counter,
    changed: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> list[str]:
    out = []
    if final_stances.get("interested_if_proven", 0) > 0:
        out.append(
            "A subset of personas would shift positively given the right "
            "proof material — focus marketing claims on what they asked for."
        )
    if final_stances.get("skeptical", 0) >= 5:
        out.append(
            "Skeptical voices were durable through discussion — surface "
            "their specific objections in upcoming concept tests."
        )
    if changed:
        out.append(
            f"{len(changed)} persona(s) privately accepted peer arguments — "
            "those arguments are candidates for messaging tests."
        )
    if failed:
        out.append(
            f"{len(failed)} persona(s) actively resisted peer pressure — "
            "their dissenting reasoning is the contrarian signal worth "
            "validating with real users."
        )
    if not out:
        out.append(
            "Insufficient signal to recommend positioning; consider "
            "running a broader discussion or revising prompts."
        )
    return out


def _what_to_test_next(
    failed: list[dict[str, Any]],
    proof_demands: list[dict[str, Any]],
) -> list[str]:
    out = []
    proof_counter = Counter()
    for p in proof_demands:
        for kw in ("ip rating", "battery", "durability", "review", "warrant",
                   "proof", "lumens"):
            if kw in (p.get("proof_need") or "").lower():
                proof_counter[kw] += 1
    if proof_counter:
        top = ", ".join(f"{k} (x{v})" for k, v in proof_counter.most_common(5))
        out.append(
            f"Proof artifacts personas demanded most: {top}. Build small "
            "concept tests around each before scaling spend."
        )
    if failed:
        out.append(
            "Talk to real prospects matching the resisting personas' "
            "profile — their objection pattern is the contrarian risk."
        )
    out.append(
        "Validate this synthetic discussion against a small real-people "
        "discussion before treating any signal as load-bearing."
    )
    return out


def render_discussion_report_markdown(
    report: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append(f"# {report['product_name']} — Discussion Report (Phase 9A.4)")
    lines.append("")
    lines.append(f"**Run scope:** `{report['run_scope_id']}`")
    lines.append(f"**Discussion session:** `{report['discussion_session_id']}`")
    lines.append(f"**Launch state:** `{report['launch_state']}`")
    lines.append(f"**Generated at:** {report['generated_at']}")
    lines.append("")
    lines.append(f"> {report['header_caveat']}")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    for b in report["executive_summary"]:
        lines.append(f"- {b}")
    lines.append("")
    setup = report["discussion_setup"]
    lines.append("## 2. Discussion Setup")
    lines.append(
        f"- Persona count: {setup['persona_count']}\n"
        f"- Group count: {setup['group_count']}\n"
        f"- Group size: {setup['group_size']}\n"
        f"- Structure: {setup['structure']}"
    )
    lines.append("")
    lines.append("## 3. Group Composition")
    lines.append("")
    for g in report["group_composition"]:
        lines.append(
            f"- **Group {g['group_index'] + 1}**: "
            + ", ".join(g["personas"])
        )
    lines.append("")
    lines.append("## 4. Public Opening Stances")
    lines.append("")
    if report["public_opening_stances"]:
        lines.append("| Speaker | Stance | Text |")
        lines.append("|---|---|---|")
        for o in report["public_opening_stances"][:20]:
            text = (o["text"] or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {o['speaker']} | `{o.get('stance')}` | {text[:160]} |"
            )
    lines.append("")
    lines.append("## 5. Main Arguments Raised")
    lines.append("")
    for a in report["main_arguments_raised"]:
        text = (a["argument"] or "").replace("\n", " ")
        lines.append(f"- **{a['speaker']}**: {text[:240]}")
    lines.append("")
    lines.append("## 6. Main Objections That Spread")
    lines.append("")
    for o in report["main_objections_that_spread"]:
        lines.append(f"- `{o['keyword']}` raised in {o['raised_in_turns']} turns")
    lines.append("")
    lines.append("## 7. Arguments That Changed Minds")
    lines.append("")
    if report["arguments_that_changed_minds"]:
        for c in report["arguments_that_changed_minds"]:
            lines.append(
                f"- **{c['persona']}** shifted "
                f"`{c.get('from')}` → `{c['to']}`: "
                f"{(c.get('reasoning') or '')[:180]}"
            )
    else:
        lines.append("- No personas privately accepted peer arguments in this run.")
    lines.append("")
    lines.append("## 8. Arguments That Failed")
    lines.append("")
    if report["arguments_that_failed"]:
        for f in report["arguments_that_failed"]:
            lines.append(
                f"- **{f['persona']}** held at `{f['held_at']}`: "
                f"{(f.get('reasoning') or '')[:180]}"
            )
    else:
        lines.append("- No active resistance recorded.")
    lines.append("")
    lines.append("## 9. Public vs Private Opinion Change")
    lines.append("")
    pv = report["public_vs_private_opinion_change"]
    lines.append(f"- Pre-discussion stance distribution: `{pv['pre_stance_distribution']}`")
    lines.append(f"- Final-discussion stance distribution: `{pv['final_stance_distribution']}`")
    lines.append(f"- Delta classification: `{pv['delta_classification']}`")
    lines.append("")
    lines.append("## 10. Social Influence Analysis")
    lines.append("")
    si = report["social_influence_analysis"]
    lines.append(f"- Delta classification counts: `{si['delta_classification']}`")
    oc = si["overcooperation_audit"]
    if oc.get("flag"):
        lines.append(f"- ⚠ Over-cooperation flag: `{oc.get('warning')}`")
    else:
        lines.append("- Over-cooperation: not flagged.")
    lines.append("")
    lines.append("## 11. Persona Cluster Differences")
    lines.append("")
    for stance, names in report["persona_cluster_differences"].items():
        lines.append(f"- `{stance}`: {', '.join(names) or '(none)'}")
    lines.append("")
    lines.append("## 12. Proof Demands")
    lines.append("")
    for p in report["proof_demands"][:20]:
        lines.append(
            f"- **{p['speaker']}** wants: {(p.get('proof_need') or '')[:160]}"
        )
    lines.append("")
    lines.append("## 13. Positioning Implications")
    lines.append("")
    for b in report["positioning_implications"]:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## 14. What Founder Should Test Next")
    lines.append("")
    for b in report["what_founder_should_test_next"]:
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
    lines.append(f"- discussion_turn_count: {appx['discussion_turn_count']}")
    lines.append(f"- pre_ballot_count: {appx['pre_ballot_count']}")
    lines.append(f"- reflection_ballot_count: {appx['reflection_ballot_count']}")
    lines.append(f"- final_ballot_count: {appx['final_ballot_count']}")
    lines.append(f"- memory_atom_count: {appx['memory_atom_count']}")
    lines.append(f"- memory_atoms_by_type: {appx['memory_atoms_by_type']}")
    lines.append(f"- forbidden_claim_audit: `{appx['forbidden_claim_audit']}`")
    lines.append(f"- sensitive_inference_audit: `{appx['sensitive_inference_audit']}`")
    lines.append(
        f"- discussion_quality_scores: `{appx['discussion_quality_scores']}`"
    )
    lines.append("")
    return "\n".join(lines)
