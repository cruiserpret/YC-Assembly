"""Phase 8.5F — render a `FounderReport` to founder-readable Markdown.

Pure function. Same input → same output. No LLM, no retrieval, no
external dependencies — string assembly only.
"""
from __future__ import annotations

from assembly.sources.founder_report_generator.schemas import FounderReport


def _bullet(s: str) -> str:
    return f"- {s.strip()}"


def render_markdown_report(report: FounderReport) -> str:
    out: list[str] = []
    out.append(
        f"# {report.product_name} — Founder Report (Phase 8.5F)\n"
    )
    out.append(
        f"**Run scope:** `{report.run_scope_id}`  \n"
        f"**Simulation:** `{report.simulation_id}`  \n"
        f"**Launch state:** `{report.launch_state}`  \n"
        f"**Generated at:** {report.completed_at}\n"
    )
    out.append(
        "> _This is a 7-person run-scoped micro-simulation. "
        "It is NOT a forecast and NOT a market verdict. "
        f"{report.product_name} is unlaunched; no persona has actually "
        "used it._\n"
    )

    # 1. Executive Summary
    out.append("## 1. Executive Summary\n")
    for b in report.executive_summary:
        out.append(_bullet(b))
    out.append("")

    # 2. Simulated Audience Snapshot
    out.append("## 2. Simulated Audience Snapshot\n")
    out.append(
        "_The 7 personas are run-scoped synthetic agents — not real "
        "people. Each was generated from the founder brief + retrieved "
        "evidence for this specific run._\n"
    )
    out.append(
        "| Display name | Role (normalized) | Evidence theme | Provider | Final stance |"
    )
    out.append("|---|---|---|---|---|")
    for p in report.simulated_audience_snapshot:
        stance = p.final_stance or "—"
        out.append(
            f"| {p.display_name} | `{p.normalized_primary_role}` | "
            f"{p.evidence_theme} | {p.source_provider_family} | "
            f"{stance} |"
        )
    out.append("")
    out.append("**Why each persona was included:**\n")
    for p in report.simulated_audience_snapshot:
        out.append(
            _bullet(f"**{p.display_name}** — {p.why_included}")
        )
    out.append("")

    # 3. Overall Reaction
    out.append("## 3. Overall Reaction\n")
    out.append("**Stance distribution:**\n")
    for label, cnt in report.stance_distribution.items():
        out.append(_bullet(f"`{label}`: {cnt}"))
    out.append("")
    for line in report.overall_reaction:
        out.append(_bullet(line))
    out.append("")

    # 4. Top Objections
    out.append("## 4. Top Objections\n")
    if not report.top_objections:
        out.append("_(no objections captured)_\n")
    for o in report.top_objections:
        out.append(f"### {o.title}  \n")
        out.append(f"_severity: **{o.severity}** · raised {o.raised_count}×_\n")
        out.append(f"**Explanation:** {o.explanation}\n")
        if o.raised_by_personas:
            out.append(
                "**Raised by personas:** "
                + ", ".join(o.raised_by_personas)
            )
        if o.raised_by_roles:
            out.append(
                "**Raised by roles:** "
                + ", ".join(f"`{r}`" for r in o.raised_by_roles)
            )
        out.append(f"**Founder action:** {o.founder_action}\n")

    # 5. Top Persuasion Levers
    out.append("## 5. Top Persuasion Levers\n")
    if not report.top_persuasion_levers:
        out.append("_(no persuasion levers captured)_\n")
    for l in report.top_persuasion_levers:
        out.append(f"### {l.title}  \n")
        out.append(f"_raised {l.raised_count}×_\n")
        out.append(f"**Why it matters:** {l.why_it_matters}\n")
        if l.likely_movable_personas:
            out.append(
                "**Likely-movable personas:** "
                + ", ".join(l.likely_movable_personas)
            )
        out.append(
            f"**Suggested founder change:** {l.suggested_founder_change}\n"
        )

    # 6. Competitor Comparison
    out.append("## 6. Competitor Comparison\n")
    if not report.competitor_comparison:
        out.append("_(no competitor mentions captured)_\n")
    for c in report.competitor_comparison:
        out.append(
            f"### {c.competitor}  \n"
            f"_{c.mention_count} mentions across the run_\n"
        )
        if c.simulated_strengths:
            out.append("**Simulated strengths (from persona reasoning):**")
            for s in c.simulated_strengths:
                out.append(_bullet(s))
        if c.simulated_weaknesses:
            out.append("**Simulated weaknesses (from persona reasoning):**")
            for w in c.simulated_weaknesses:
                out.append(_bullet(w))
        if c.where_target_product_could_differentiate:
            out.append(
                f"**Where {report.product_name} could differentiate:**"
            )
            for d in c.where_target_product_could_differentiate:
                out.append(_bullet(d))
        out.append("")

    # 7. Proof Needed
    out.append("## 7. Proof Needed Before Adoption\n")
    if not report.proof_needed:
        out.append("_(no proof requirements captured)_\n")
    for p in report.proof_needed:
        out.append(f"### {p.proof_kind}  \n")
        out.append(f"{p.description}\n")
        if p.suggested_founder_assets:
            out.append("**Suggested founder assets:**")
            for a in p.suggested_founder_assets:
                out.append(_bullet(a))
        out.append("")

    # 8. Positioning Recommendations
    out.append("## 8. Positioning Recommendations\n")
    if not report.positioning_recommendations:
        out.append("_(no positioning recommendations)_\n")
    for r in report.positioning_recommendations:
        out.append(f"### {r.angle_label}  \n")
        out.append(f"**Rationale:** {r.rationale}\n")
        if r.target_personas:
            out.append(
                "**Target personas:** "
                + ", ".join(r.target_personas)
            )
        out.append(f"**Test idea:** {r.test_idea}\n")

    # 9. Product / Offer Recommendations
    out.append("## 9. Product / Offer Recommendations\n")
    if not report.product_offer_recommendations:
        out.append("_(no product/offer recommendations)_\n")
    for po in report.product_offer_recommendations:
        out.append(
            f"- **{po.area}:** {po.suggestion}  \n"
            f"  _triggered by: {', '.join(po.triggered_by) or 'n/a'}_"
        )
    out.append("")

    # 10. What to Test Next
    out.append("## 10. What to Test Next\n")
    if not report.what_to_test_next:
        out.append("_(no test recommendations)_\n")
    for t in report.what_to_test_next:
        out.append(f"### {t.test_label}  \n")
        out.append(f"{t.description}\n")
        out.append(f"**Expected signal:** {t.expected_signal}\n")

    # 11. Caveats
    out.append("## 11. Caveats\n")
    for c in report.caveats:
        out.append(_bullet(c))
    out.append("")

    # 12. Appendix
    out.append("## 12. Appendix\n")
    out.append("### Persona-to-evidence map\n")
    out.append(
        "| Display name | Compressed candidate ID | Role | Traits | Links | Sources |"
    )
    out.append("|---|---|---|---|---|---|")
    for p in report.appendix.persona_to_evidence_map:
        out.append(
            f"| {p.get('display_name', '')} | "
            f"{p.get('compressed_candidate_id', '')} | "
            f"`{p.get('normalized_primary_role', '')}` | "
            f"{p.get('trait_count', 0)} | "
            f"{p.get('evidence_link_count', 0)} | "
            f"{p.get('source_record_count', 0)} |"
        )
    out.append("")
    out.append("### Round summary\n")
    out.append(
        "| Round | # | Agents | Stance distribution | Forbidden claims |"
    )
    out.append("|---|---|---|---|---|")
    for r in report.appendix.round_summary:
        out.append(
            f"| {r.get('round_type', '')} | "
            f"{r.get('round_number', '')} | "
            f"{r.get('agent_count', 0)} | "
            f"{r.get('stance_distribution', {})} | "
            f"{r.get('any_forbidden_claims', False)} |"
        )
    out.append("")
    out.append("### Quality scores\n")
    for k, v in (report.appendix.quality_scores or {}).items():
        out.append(_bullet(f"`{k}`: {v}"))
    out.append("")
    out.append("### Forbidden-claim audit\n")
    for k, v in (report.appendix.forbidden_claim_audit or {}).items():
        out.append(_bullet(f"`{k}`: {v}"))
    out.append("")
    out.append("### Source / persona traceability\n")
    for k, v in (
        report.appendix.source_persona_traceability or {}
    ).items():
        out.append(_bullet(f"`{k}`: {v}"))
    out.append("")
    return "\n".join(out)
